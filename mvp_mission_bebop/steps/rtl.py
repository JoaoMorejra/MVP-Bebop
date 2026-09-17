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

The altitude governor stays engaged through all four. The previous version
forced ``vz`` to zero for the final hover and the landing, discarding altitude
protection for the last several seconds of the flight.

**Where the origin is.** The return vector comes from the aggregated motion
sequence rather than from ``/bebop/odom``. Every ``move_velocity`` the mission
transmitted has been integrated through the speed calibration since the airborne
origin was frozen, so the drone carries a running displacement from ``(0, 0)``:
five metres forward and four to the left leaves ``(5.0, +4.0)`` in body FLU, and
returning is a matter of flying the reverse of that vector until it is null. The
guidance law decomposes it into the along-track and cross-track channels it
already had, so the return is the same straight, yaw-locked reverse line it
always was -- what has changed is which estimate of the origin it is closing on.

That estimate is preferred because of what it cannot do. Odometry here is optical
flow over featureless tarmac at the tilt angles this mission flies, and when it
drifts nothing on the airframe says so; the aggregate cannot drift, because it is
a record of what was commanded rather than a measurement of the world. It is
wrong exactly to the extent the speed calibration is wrong, which is a fixed
error that can be measured on the ground rather than a growing one that cannot be
measured at all. Both are computed every cycle and their disagreement is logged,
because that number is the only thing that will tell the operator which of the
two to believe next time.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.rtl_guidance import (
    GuidanceCommand,
    ReturnReference,
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

#: Descent, as a multiple of the stall epsilon, that must have happened before a
#: halt in that descent may be read as touchdown. Guards against confirming a
#: landing on noise, or on the first centimetre of a descent that has only just
#: begun.
_MIN_CONFIRMED_DESCENT_RATIO: float = 4.0


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
            rtl_cfg, ctx.params.kinematics, ctx.speed_calibration, ctx.params.governor
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

        tracker = self._tracker(ctx)
        snapshot = ctx.odom_supervisor.snapshot()

        if tracker is None and not snapshot.has_launch_origin:
            # Neither estimator knows where home is. There is no return leg to
            # fly, and guessing one would be worse than not flying it: descend
            # where the drone stands.
            logger.error(
                "No launch origin was ever frozen and no motion sequence was aggregated; "
                "there is nothing to return to. Descending in place."
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
        if tracker is not None:
            logger.info("Return vector from the aggregated motion sequence: %s", tracker.summary())
        else:
            logger.info(
                "Return vector from odometry: no aggregated motion sequence is available."
            )
        logger.info("Invariants: vyaw == 0.0 (locked), vx <= 0.0 (backward only).")

        status = self._navigate(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._station_keep(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        return self._touchdown(ctx, guidance)

    # ------------------------------------------------------------- estimation

    @staticmethod
    def _tracker(ctx: MissionContext):
        """The dead-reckoning tracker to navigate on, or ``None``.

        ``None`` covers three distinct situations and all of them mean the same
        thing to the caller: dead reckoning was switched off in the
        configuration, no tracker was wired in, or one was wired in but never
        armed -- which is what a mission that failed before Stage 1 froze its
        origin looks like. In every case the aggregate has no ``(0, 0)`` to be
        relative to, and odometry is the only reference left.
        """
        if not ctx.params.rtl.use_dead_reckoning:
            return None
        tracker = getattr(ctx.drone, "motion_tracker", None)
        if tracker is None or not tracker.armed:
            return None
        return tracker

    def _reference(
        self, ctx: MissionContext, tracker, snapshot: OdometrySnapshot, *, log: bool
    ) -> ReturnReference:
        """Resolve where the origin is, and say so when the two sources differ.

        The disagreement is computed whether or not it is acted on. It is the
        only observable this mission produces that bears on which estimator to
        trust, and it costs a subtraction: a flight that lands two metres from
        the pad is otherwise a mystery, and with this line in the log it is a
        measurement of either the speed calibration or the optical flow.
        """
        odometric = ReturnReference.from_snapshot(snapshot)
        if tracker is None:
            return odometric

        state = tracker.state()
        ex, ey, distance = state.body_frame_origin_error()
        reckoned = ReturnReference(
            ex_body_m=ex,
            ey_body_m=ey,
            distance_m=distance,
            x_m=state.x_m,
            y_m=state.y_m,
            source="dead_reckoning",
        )

        if log and snapshot.has_launch_origin:
            disagreement = math.hypot(
                reckoned.ex_body_m - odometric.ex_body_m,
                reckoned.ey_body_m - odometric.ey_body_m,
            )
            level = (
                logging.WARNING
                if disagreement > ctx.params.rtl.dead_reckoning_disagreement_warn_m
                else logging.DEBUG
            )
            logger.log(
                level,
                "Return vector: dead reckoning (%+.2f, %+.2f) m vs odometry (%+.2f, %+.2f) m "
                "-- they disagree by %.2f m. Navigating on %s.",
                reckoned.ex_body_m,
                reckoned.ey_body_m,
                odometric.ex_body_m,
                odometric.ey_body_m,
                disagreement,
                reckoned.source,
            )
        return reckoned

    # ------------------------------------------------------------- navigation

    def _navigate(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Fly the reverse of the aggregated displacement vector back to (0, 0).

        The return leg translates, so it holds altitude two-sided like every
        other phase that does. The window closes when this method returns,
        whichever way it returns -- including into the touchdown sequence, which
        must not be able to climb under any circumstances.
        """
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            return self._navigate_home(ctx, guidance)

    def _navigate_home(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.timeout_sec)
        tracker = self._tracker(ctx)
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
            reference = self._reference(
                ctx, tracker, snapshot, log=cycle % _STREAM_DECIMATION == 0
            )
            command = self._emit(
                ctx, guidance, snapshot, dt, RTLPhase.NAVIGATING, reference=reference
            )
            distance = command.distance_m

            if cycle % _STREAM_DECIMATION == 0:
                self._publish_telemetry(ctx, command, deadline.elapsed_sec)
                logger.info(
                    "RTL navigating on %s: dist=%.2f m (arrive <= %.2f m) | err=(%.2f, %.2f) m | "
                    "cmd=(vx=%.3f, vy=%.3f, vz=%.3f) normalized | v=%.3f m/s | alt=%.2f m | %s",
                    command.reference_source,
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

            if command.arrived or command.ready_to_land:
                # Either criterion ends the return leg. Settlement is the
                # stronger evidence and is preferred when it is available, but
                # it must not be the only way out: its positional-sigma term
                # depends on odometry quality the Bebop does not guarantee at
                # hover, so waiting for it alone could keep a drone that is
                # already over the origin airborne until the window expired.
                logger.info(
                    "Return leg complete at %.2f m after %.1f s (%s). Proceeding to landing. %s",
                    command.distance_m,
                    deadline.elapsed_sec,
                    "settlement confirmed" if command.arrived else "landing commit",
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
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            return self._hold_over_origin(ctx, guidance)

    def _hold_over_origin(
        self, ctx: MissionContext, guidance: RTLGuidanceController
    ) -> StepStatus:
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.final_hover_delay_sec)
        tracker = self._tracker(ctx)

        logger.info(
            "Station keeping over the origin for %.1f s to damp residual motion...",
            rtl_cfg.final_hover_delay_sec,
        )

        command = None
        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            reference = self._reference(ctx, tracker, snapshot, log=False)
            command = self._emit(
                ctx, guidance, snapshot, dt, RTLPhase.STATION_KEEPING, reference=reference
            )

        snapshot = ctx.odom_supervisor.snapshot()
        logger.info(
            "Station keeping complete: residual speed %.3f m/s at %.2f m from origin (%s).",
            snapshot.horizontal_speed,
            command.distance_m if command is not None else snapshot.body_frame_launch_error()[2],
            command.reference_source if command is not None else "odometry",
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

        logger.info(
            "Executing terminal landing at the launch origin (window %.1f s)...",
            rtl_cfg.touchdown_timeout_sec,
        )
        self._assert_land(ctx, rtl_cfg.land_burst_count)

        settled_cycles = 0
        stagnant_elapsed = 0.0
        required_cycles = max(2, rtl_cfg.settle_min_samples // 2)
        confirmed = False
        assisted = False

        start_altitude = ctx.odom_supervisor.snapshot().relative_altitude
        # Progress is tracked against the *lowest* altitude reached, not against
        # the previous sample. Comparing consecutive samples makes any descent
        # slower than one epsilon per cycle indistinguishable from no descent at
        # all, which is the opposite of what the check is for.
        lowest_altitude = start_altitude
        stall_elapsed = 0.0

        while deadline.active:
            if ctx.emergency_event.is_set():
                logger.warning("Emergency raised during touchdown; continuing to command land.")
                ctx.drone.land()
                break

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            altitude = snapshot.relative_altitude

            # --- primary: the airframe is low and still -----------------
            grounded = altitude <= rtl_cfg.touchdown_altitude_m
            still = snapshot.speed <= rtl_cfg.settle_max_speed_mps
            settled_cycles = settled_cycles + 1 if (grounded and still) else 0
            if settled_cycles >= required_cycles:
                confirmed = True
                break

            # --- fallback: the descent has stopped moving ---------------
            # The absolute threshold is measured against a ground reference
            # calibrated before launch. If that datum drifted in flight the
            # threshold may never be crossed even with the drone on the ground,
            # and the step would report an unconfirmed landing for a landing
            # that plainly happened. Descent stagnation under a commanded
            # landing is the platform-independent touchdown signature.
            if altitude < lowest_altitude - rtl_cfg.descent_stall_epsilon_m:
                lowest_altitude = altitude
                stagnant_elapsed = 0.0
                stall_elapsed = 0.0
            else:
                stagnant_elapsed += dt
                stall_elapsed += dt

            descended = start_altitude - altitude
            # A descent has to have actually happened, and have been substantial,
            # before its cessation means anything. Without this the counter
            # accumulated during the pre-descent stall and then confirmed
            # touchdown on the first centimetre of movement -- reporting the
            # drone as landed while it was still most of a metre up.
            meaningful = descended >= _MIN_CONFIRMED_DESCENT_RATIO * rtl_cfg.descent_stall_epsilon_m
            if stagnant_elapsed >= rtl_cfg.touchdown_stagnation_sec and still and meaningful:
                logger.info(
                    "Touchdown inferred from descent stagnation: %.2f m descended, then no "
                    "further progress for %.1f s at %.2f m.",
                    descended,
                    stagnant_elapsed,
                    altitude,
                )
                confirmed = True
                break

            # --- escalation: the landing request is not being honoured ---
            # land() publishes an Empty and returns; nothing acknowledges it.
            # If the altitude has not moved at all after descent_stall_sec, the
            # firmware is not acting on it, and re-sending the same request more
            # times will not change that. Command the descent explicitly, then
            # re-assert the landing.
            if not assisted and stall_elapsed >= rtl_cfg.descent_stall_sec and descended <= 0.0:
                assisted = True
                logger.warning(
                    "No descent %.1f s after the landing request (altitude %.2f m). "
                    "Commanding assisted descent at %.3f normalized.",
                    stall_elapsed,
                    altitude,
                    rtl_cfg.assisted_descent_speed,
                )
                # The stall counters describe the period before the escalation
                # and must not be carried into it, or the first sample of real
                # descent would satisfy a stagnation test built from them.
                stagnant_elapsed = 0.0
                stall_elapsed = 0.0
                lowest_altitude = altitude

            if assisted and not grounded:
                # Routed through the failsafe like every other command, so the
                # descent-only invariant and the horizontal envelope still hold.
                safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(
                    -abs(rtl_cfg.assisted_descent_speed), 0.0
                )
                safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, 0.0)
                ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)
            else:
                # The Bebop latches its last command, so keep asserting the
                # landing rather than assuming one publish was enough. No
                # velocity is sent on this path: a Twist arriving mid-sequence
                # is the one thing that can talk the firmware out of landing.
                ctx.drone.land()

        if assisted:
            # Whatever the assisted descent achieved, the airframe must end up
            # under the firmware's own landing logic rather than under a velocity
            # command that stops the moment this loop does.
            self._assert_land(ctx, rtl_cfg.land_burst_count)

        # Only a confirmed touchdown completes the RTL. This flag used to be set
        # unconditionally, so a drone still airborne at the timeout reported a
        # completed flight to everything downstream that reads the blackboard.
        ctx.blackboard.rtl_completed = confirmed
        final = ctx.odom_supervisor.snapshot()

        if confirmed:
            logger.info(
                "Touchdown confirmed: altitude %.2f m, speed %.3f m/s%s.",
                final.relative_altitude,
                final.speed,
                " (assisted descent was required)" if assisted else "",
            )
            self._announce("Pouso seguro concluído", "pouso seguro concluído na base de lançamento")
            return StepStatus.SUCCESS

        logger.warning(
            "Landing commanded for %.1f s without odometric confirmation (altitude %.2f m, "
            "descended %.2f m). The land command has been re-asserted throughout and is the "
            "last command sent.",
            rtl_cfg.touchdown_timeout_sec,
            final.relative_altitude,
            start_altitude - final.relative_altitude,
        )
        self._announce("Pouso concluído", "pouso concluído, confirmação odométrica indisponível")
        return StepStatus.SUCCESS

    @staticmethod
    def _assert_land(ctx: MissionContext, burst: int) -> None:
        """Zero the airframe, then request landing, repeatedly.

        The zero Twist goes first and the land request last, so the final thing
        on the wire is the landing. Reversing that order lets a velocity command
        land after the request and hold the firmware in piloting state.
        """
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        for _ in range(max(1, burst)):
            ctx.drone.land()

    # ---------------------------------------------------------------- helpers

    def _emit(
        self,
        ctx: MissionContext,
        guidance: RTLGuidanceController,
        snapshot: OdometrySnapshot,
        dt: float,
        phase: RTLPhase,
        reference: Optional[ReturnReference] = None,
    ) -> GuidanceCommand:
        """Compute one cycle of guidance and transmit it."""
        vz_governor = ctx.governor.compute_vz(snapshot.relative_altitude, dt)
        command = guidance.compute(snapshot, vz_governor, dt, phase=phase, reference=reference)

        # Saturate rather than raise: a rounding error must not end the mission.
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(command.vz, command.vyaw)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)
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
            frame = ctx.grab_frame(timeout_sec=0.05)
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
