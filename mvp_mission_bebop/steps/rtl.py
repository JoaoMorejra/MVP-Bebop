"""Stage 5: closed-loop return to launch and verified terminal landing.

The step owns sequencing only. Every control law lives in
:mod:`mvp_mission_bebop.controllers.rtl_guidance`, which is exercisable without
ROS, hardware, or a wall clock.

Four phases run in order:

1. **Navigating** -- backward flight to the launch origin under the guidance
   law, until multivariate settlement confirms arrival or the window expires.
2. **Station keeping** -- a closed-loop hold over the origin that re-transmits
   every cycle. The previous implementation sent one zero-velocity command and
   then slept, but the Bebop latches the last Twist it received and never zeroes
   it on its own, so that hover was open-loop by construction.
3. **Touchdown** -- repeated land commands with odometric confirmation.
4. **Complete** -- hand back to the runner.

The anti-climb governor stays engaged through all four. The previous version
forced ``vz`` to zero for the final hover and the landing, discarding altitude
protection for the last several seconds of the flight.
"""

from __future__ import annotations

import logging
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.rtl_guidance import (
    GuidanceCommand,
    RTLGuidanceController,
    RTLPhase,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, TelemetryHealth

logger = logging.getLogger("Step5RTL")

#: Publish an annotated telemetry frame every Nth cycle. Grabbing a frame blocks,
#: and the return leg needs its cadence more than it needs every frame.
_STREAM_DECIMATION: int = 3


class ClosedLoopRTLStep(BaseStep):
    """Closed-loop odometry return to launch, flying strictly backward."""

    def __init__(self, guidance: Optional[RTLGuidanceController] = None) -> None:
        super().__init__("STEP 5: Closed-Loop Odometry RTL & Safe Landing")
        self._guidance = guidance

    # ------------------------------------------------------------------ entry

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        rtl_cfg = ctx.params.rtl
        guidance = self._guidance or RTLGuidanceController(
            rtl_cfg, ctx.params.kinematics, ctx.speed_calibration
        )
        guidance.reset()
        ctx.speed_calibration.warn_if_uncalibrated("RTL guidance")

        self._announce(
            "Iniciando Retorno à Base",
            "iniciando retorno à base de lançamento em linha reta de ré",
        )

        # Point the gimbal forward again so the operator sees where the drone is
        # going rather than the ground beneath it.
        ctx.drone.camera_control(tilt=ctx.params.gimbal.search_tilt_deg, pan=0.0)

        snapshot = ctx.odom_supervisor.snapshot()
        if not snapshot.has_launch_origin:
            logger.error(
                "No launch origin was ever frozen; there is nothing to return to. "
                "Descending in place."
            )
            return self._touchdown(ctx, guidance)

        logger.info(
            "RTL engaged. Cruise %.3f m/s (%.3f normalized), braking accel %.2f m/s^2, "
            "jerk %.2f m/s^3, arrival radius %.2f m, window %.1f s.",
            guidance.cruise_speed_mps,
            rtl_cfg.max_speed,
            rtl_cfg.max_accel_mps2,
            rtl_cfg.max_jerk_mps3,
            rtl_cfg.arrival_radius_m,
            rtl_cfg.timeout_sec,
        )
        logger.info("Invariants: vyaw == 0.0 (locked), vx <= 0.0 (backward only), vz <= 0.0.")

        status = self._navigate(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._station_keep(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        return self._touchdown(ctx, guidance)

    # ------------------------------------------------------------- navigation

    def _navigate(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Fly backward to the launch origin until settlement or timeout."""
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.timeout_sec)
        distance = float("inf")
        cycle = 0

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            health = self._check_health(ctx)
            if health is not None:
                ctx.failsafe.trigger_emergency_land(health)
                return StepStatus.FAILURE

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            command = self._emit(ctx, guidance, snapshot, dt, RTLPhase.NAVIGATING)
            distance = command.distance_m

            if cycle % _STREAM_DECIMATION == 0:
                self._publish_telemetry(ctx, command, deadline.elapsed_sec)
                logger.info(
                    "RTL navigating: dist=%.2f m (arrive <= %.2f m) | err=(%.2f, %.2f) m | "
                    "cmd=(vx=%.3f, vy=%.3f, vz=%.3f) normalized | v=%.3f m/s | alt=%.2f m | %s",
                    command.distance_m,
                    rtl_cfg.arrival_radius_m,
                    command.ex_body_m,
                    command.ey_body_m,
                    command.vx,
                    command.vy,
                    command.vz,
                    snapshot.horizontal_speed,
                    snapshot.relative_altitude,
                    command.note,
                )

            if command.arrived:
                logger.info(
                    "Arrival confirmed at %.2f m after %.1f s: %s",
                    command.distance_m,
                    deadline.elapsed_sec,
                    guidance.settlement,
                )
                return StepStatus.SUCCESS

            cycle += 1

        logger.warning(
            "RTL window of %.1f s expired with %.2f m remaining. Proceeding to landing.",
            rtl_cfg.timeout_sec,
            distance,
        )
        return StepStatus.SUCCESS

    def _station_keep(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Hold position over the origin to dissipate residual kinetic energy."""
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.final_hover_delay_sec)

        logger.info(
            "Station keeping over the origin for %.1f s to damp residual motion...",
            rtl_cfg.final_hover_delay_sec,
        )

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            self._emit(ctx, guidance, snapshot, dt, RTLPhase.STATION_KEEPING)

        snapshot = ctx.odom_supervisor.snapshot()
        logger.info(
            "Station keeping complete: residual speed %.3f m/s at %.2f m from origin.",
            snapshot.horizontal_speed,
            snapshot.body_frame_launch_error()[2],
        )
        return StepStatus.SUCCESS

    # --------------------------------------------------------------- terminal

    def _touchdown(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Land, and confirm touchdown from odometry.

        Motor disarm cannot be confirmed on this airframe. ``BebopDrone.land``
        publishes an ``Empty`` message and returns immediately with no
        acknowledgement (``nectar/control/bebop/drone.py:165``), and ``is_armed``
        falls through to the base class's ``None`` because the Bebop publishes no
        armed state on any topic. Odometric touchdown is the strongest
        confirmation this platform can supply, so that is what is asserted here
        rather than a disarm signal that does not exist.
        """
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.touchdown_timeout_sec)

        logger.info("Executing terminal landing at the launch origin...")
        for _ in range(max(1, rtl_cfg.land_burst_count)):
            ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            ctx.drone.land()

        settled_cycles = 0
        required_cycles = max(2, rtl_cfg.settle_min_samples // 2)
        confirmed = False

        while deadline.active:
            snapshot = ctx.odom_supervisor.snapshot()
            rate.tick()

            grounded = snapshot.relative_altitude <= rtl_cfg.touchdown_altitude_m
            still = snapshot.speed <= rtl_cfg.settle_max_speed_mps
            settled_cycles = settled_cycles + 1 if (grounded and still) else 0

            if settled_cycles >= required_cycles:
                confirmed = True
                break

            # The Bebop latches its last command, so keep asserting the landing
            # rather than assuming one publish was enough.
            ctx.drone.land()

        ctx.blackboard.rtl_completed = True

        if confirmed:
            logger.info(
                "Touchdown confirmed by odometry: altitude %.2f m, speed %.3f m/s.",
                ctx.odom_supervisor.snapshot().relative_altitude,
                ctx.odom_supervisor.snapshot().speed,
            )
            self._announce("Pouso seguro concluído", "pouso seguro concluído na base de lançamento")
            return StepStatus.SUCCESS

        logger.warning(
            "Landing commanded but touchdown was not confirmed within %.1f s "
            "(altitude %.2f m). The land command has been re-transmitted throughout.",
            rtl_cfg.touchdown_timeout_sec,
            ctx.odom_supervisor.snapshot().relative_altitude,
        )
        self._announce("Pouso concluído", "pouso concluído, confirmação odométrica indisponível")
        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- helpers

    def _emit(
        self,
        ctx: MissionContext,
        guidance: RTLGuidanceController,
        snapshot: OdometrySnapshot,
        dt: float,
        phase: RTLPhase,
    ) -> GuidanceCommand:
        """Compute one cycle of guidance and transmit it."""
        vz_governor = ctx.governor.compute_vz(snapshot.relative_altitude)
        command = guidance.compute(snapshot, vz_governor, dt, phase=phase)

        # Saturate rather than raise: a rounding error must not end the mission.
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(command.vz, command.vyaw)
        ctx.drone.move_velocity(vx=command.vx, vy=command.vy, vz=safe_vz, vyaw=safe_vyaw)
        return command

    def _check_health(self, ctx: MissionContext) -> Optional[str]:
        """Return a failure reason, or ``None`` when the system is nominal."""
        if ctx.drone.no_fly:
            return None

        health = ctx.odom_supervisor.telemetry_health()
        if health is TelemetryHealth.NEVER_RECEIVED:
            return "No odometry received during RTL. Verify /bebop/odom is publishing."
        if health is TelemetryHealth.STALE:
            return "Odometry telemetry loss during RTL."
        if ctx.odom_supervisor.is_ceiling_breached():
            return "Altitude ceiling breached during RTL."
        return None

    def _publish_telemetry(
        self, ctx: MissionContext, command: GuidanceCommand, elapsed_sec: float
    ) -> None:
        """Overlay guidance state onto the annotated camera stream."""
        if ctx.handler is None:
            return
        try:
            frame = ctx.handler.take_photo(timeout_sec=0.05)
            if frame is None:
                return
            ctx.failsafe.notify_frame_received()
            prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            ctx.publish_annotated_stream(
                frame,
                None,
                f"{prefix}STEP 5: RTL ({elapsed_sec:.1f}s) | DIST: {command.distance_m:.2f}m "
                f"| VX: {command.vx:.3f} | ALT: {ctx.odom_supervisor.relative_altitude:.2f}m",
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break flight
            logger.debug("Telemetry stream publication skipped: %s", exc)

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        """Dispatch a non-blocking acoustic cue, ignoring any failure."""
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
