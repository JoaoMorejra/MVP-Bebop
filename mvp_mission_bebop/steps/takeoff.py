"""Stage 1: IMU calibration, ground reference, takeoff, and hover stabilization."""

from __future__ import annotations

import logging
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step1Takeoff")

#: Seconds before liftoff at which the takeoff clearance call is made.
_CLEARANCE_ANNOUNCE_SEC: float = 3.2

#: Settling time after flat trim, in seconds. The IMU needs a moment on a level
#: surface before the calibration it just performed is trustworthy.
_FLAT_TRIM_SETTLE_SEC: float = 2.0


class TakeoffStep(BaseStep):
    """Calibrates, launches, and establishes a drift-free airborne origin."""

    def __init__(self) -> None:
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        ctx.current_tilt_deg = ctx.params.gimbal.search_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        logger.info("Executing IMU flat trim on a level surface...")
        ctx.drone.flat_trim()
        ctx.drone.delay(_FLAT_TRIM_SETTLE_SEC)

        if not self._calibrate(ctx):
            return StepStatus.FAILURE

        status = self._countdown(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._launch(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        return self._stabilize(ctx)

    # ------------------------------------------------------------ calibration

    def _calibrate(self, ctx: MissionContext) -> bool:
        """Establish the ground altitude reference, refusing an untrustworthy one."""
        if ctx.odom_supervisor.calibrate_ground_reference():
            return True

        if ctx.drone.no_fly:
            logger.warning(
                "Ground calibration was refused, but --no-fly is active, so the mission "
                "continues on the bench with an uncalibrated altitude reference."
            )
            return True

        logger.critical(
            "Ground reference calibration failed. Refusing to launch: without a trustworthy "
            "z0 the altitude ceiling and the anti-climb governor are both meaningless."
        )
        self._announce(
            "Falha na calibração",
            "falha na calibração de referência de solo, decolagem cancelada",
            priority="CRITICAL",
        )
        return False

    # -------------------------------------------------------------- countdown

    def _countdown(self, ctx: MissionContext) -> StepStatus:
        """Run the pre-flight countdown while warming up the perception pipeline.

        The inference warmup is the point: the first YOLO call allocates and
        compiles, and paying that cost here rather than in the first search
        iteration keeps the search loop's cadence honest from its first cycle.
        """
        duration = ctx.params.kinematics.countdown_sec
        if duration <= 0.0:
            return StepStatus.SUCCESS

        logger.info("Pre-flight countdown and sensor warmup (%.1f s)...", duration)
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        announced = False
        warmup_frames = 0

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            remaining = deadline.remaining_sec
            frame = ctx.handler.take_photo(timeout_sec=0.2)
            if frame is not None:
                ctx.failsafe.notify_frame_received()
                try:
                    result = ctx.detector.detect(frame)
                    warmup_frames += 1
                    ctx.publish_annotated_stream(
                        frame, result, f"CONTAGEM REGRESSIVA: {remaining:.1f}s | YOLO PRONTO"
                    )
                except Exception as exc:  # noqa: BLE001 - warmup is best-effort
                    logger.debug("Warmup inference notice: %s", exc)

            if remaining <= _CLEARANCE_ANNOUNCE_SEC and not announced:
                announced = True
                logger.info("Takeoff synchronization window reached. Announcing clearance.")
                self._announce("Decolagem autorizada", "decolagem autorizada, iniciando voo")

            rate.tick()

        logger.info("Perception pipeline warmed up over %d frames.", warmup_frames)
        return StepStatus.SUCCESS

    # ----------------------------------------------------------------- launch

    def _launch(self, ctx: MissionContext) -> StepStatus:
        """Command takeoff.

        The altitude argument is accepted by the SDK and ignored by the Bebop
        firmware, which runs its own launch profile. It is passed through for
        API fidelity and used here only for logging and the ceiling.
        """
        target_altitude = ctx.params.kinematics.target_altitude_m
        logger.info(
            "Issuing takeoff (nominal altitude %.2f m, ceiling %.2f m). "
            "Note: the Bebop firmware selects its own launch altitude.",
            target_altitude,
            ctx.odom_supervisor.altitude_ceiling,
        )

        if not ctx.drone.takeoff(altitude=target_altitude):
            logger.critical("Autonomous takeoff rejected by the flight controller.")
            self._announce(
                "Falha na decolagem",
                "decolagem rejeitada pela controladora de voo",
                priority="CRITICAL",
                wait=True,
            )
            return StepStatus.FAILURE

        return StepStatus.SUCCESS

    def _stabilize(self, ctx: MissionContext) -> StepStatus:
        """Hover until the liftoff transient decays, then freeze the return origin."""
        duration = ctx.params.kinematics.takeoff_stabilize_duration_sec
        logger.info("Stabilizing in hover for %.1f s...", duration)

        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            if ctx.handler.take_photo(timeout_sec=0.2) is not None:
                ctx.failsafe.notify_frame_received()
            rate.tick()

        ctx.failsafe.notify_frame_received()

        # Freeze the horizontal origin only now. Measuring it on the ground
        # would bake the ground-effect transient into the coordinate the drone
        # spends the rest of the mission trying to return to.
        ctx.odom_supervisor.freeze_hover_takeoff_origin()

        snapshot = ctx.odom_supervisor.snapshot()
        if not ctx.drone.no_fly:
            healthy, reason = ctx.failsafe.evaluate_system_health()
            if not healthy:
                ctx.failsafe.trigger_emergency_land(reason)
                return StepStatus.FAILURE

            if not snapshot.is_calibrated:
                logger.warning("Airborne without a calibrated ground reference.")

        logger.info(
            "Stage 1 complete: relative altitude %.2f m, residual speed %.3f m/s.",
            snapshot.relative_altitude,
            snapshot.horizontal_speed,
        )
        self._announce("Decolagem concluída", "decolagem concluída, iniciando varredura")
        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _announce(
        action: str, detail: str, *, priority: Optional[str] = None, wait: bool = False
    ) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            kwargs = {"details": {"etapa": detail}, "wait": wait}
            if priority is not None:
                kwargs["priority"] = priority
            announce_sync(action, **kwargs)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
