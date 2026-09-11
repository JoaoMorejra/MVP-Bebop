"""Stage 4: motionless nadir hover and forensic evidence capture.

The capture is only worth as much as the hover beneath it. A 14-megapixel frame
taken while the airframe is still translating is motion-blurred at exactly the
scale that matters for reading a plate or a marking, so this stage verifies
stillness statistically before triggering, rather than taking a single velocity
sample at face value.

Verification is bounded: if the drone never settles within the window the
capture happens anyway, because an imperfect photograph of the scene is worth
more than none at all. The metadata records which of the two paths was taken.
"""

from __future__ import annotations

import logging
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step4Inspection")


class NadirInspectionStep(BaseStep):
    """Holds a motionless nadir hover and records forensic evidence."""

    def __init__(self) -> None:
        super().__init__("STEP 4: Motionless Nadir Hover & Evidence Capture")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        if not ctx.blackboard.approach_finished:
            logger.warning("Approach never completed. Skipping nadir inspection.")
            return StepStatus.SUCCESS

        # Lock the gimbal down for the whole stage.
        ctx.current_tilt_deg = ctx.params.gimbal.nadir_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        settled = self._await_stillness(ctx)
        captured = self._capture(ctx, settled)

        status = self._hover(ctx, captured)
        if status is not StepStatus.SUCCESS:
            return status

        if not ctx.blackboard.evidence.captured:
            logger.warning(
                "No evidence was recorded during the hover window; attempting a final capture."
            )
            self._capture(ctx, settled, forced=True)

        if ctx.blackboard.evidence.captured:
            self._announce("Registro concluído", "registro fotográfico do acidente concluído")
            logger.info(
                "Stage 4 complete. Evidence: raw=%s annotated=%s metadata=%s",
                ctx.blackboard.evidence.raw_path,
                ctx.blackboard.evidence.annotated_path,
                ctx.blackboard.evidence.metadata_path,
            )
        else:
            logger.error("Stage 4 finished without recording any evidence.")

        return StepStatus.SUCCESS

    # -------------------------------------------------------------- stillness

    def _await_stillness(self, ctx: MissionContext) -> bool:
        """Wait until motion is statistically negligible, or the window expires."""
        cfg = ctx.params.inspection
        detector = SettlementDetector(
            SettlementCriteria(
                window_sec=cfg.settle_window_sec,
                min_samples=cfg.settle_min_samples,
                max_speed=cfg.settle_max_speed_mps,
                max_position_sigma=cfg.settle_max_position_sigma_m,
            )
        )
        deadline = Deadline(cfg.settle_timeout_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        elapsed = 0.0
        report = None

        logger.info(
            "Verifying motionlessness: speed <= %.3f m/s and position sigma <= %.3f m "
            "sustained for %.1f s.",
            cfg.settle_max_speed_mps,
            cfg.settle_max_position_sigma_m,
            cfg.settle_window_sec,
        )

        while deadline.active:
            if ctx.emergency_event.is_set():
                return False

            self._hold(ctx)
            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            elapsed += dt

            report = detector.update(
                x=snapshot.x,
                y=snapshot.y,
                speed=snapshot.speed,
                timestamp=elapsed,
            )
            if report.settled:
                logger.info("Hover confirmed motionless after %.1f s: %s", elapsed, report)
                return True

        logger.warning(
            "Hover did not settle within %.1f s (%s). Capturing anyway.",
            cfg.settle_timeout_sec,
            report,
        )
        return False

    # ---------------------------------------------------------------- capture

    def _capture(self, ctx: MissionContext, settled: bool, *, forced: bool = False) -> bool:
        """Trigger the onboard snapshot and persist local evidence."""
        frame = ctx.handler.take_photo(timeout_sec=1.5)
        if frame is None:
            logger.error("No frame available for evidence capture.")
            return False

        ctx.failsafe.notify_frame_received()
        result = ctx.detector.detect(frame, conf=ctx.params.vision.confidence_threshold)
        targets = result.filter_by_class(ctx.params.vision.target_classes)

        if not targets and not forced:
            logger.info("No target visible at nadir yet; deferring capture.")
            return False

        # Trigger the 14 MP onboard camera. It acknowledges nothing and returns
        # no path, so the local frames below are the evidence this mission can
        # actually account for.
        ctx.drone.snapshot()

        annotated = ctx.publish_annotated_stream(
            frame,
            result,
            f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: EVIDENCE CAPTURE "
            f"| {'SETTLED' if settled else 'UNSETTLED'} | TARGETS: {len(targets)}",
        )

        record = ctx.record_photographic_evidence(
            raw_frame=frame,
            annotated_frame=annotated if annotated is not None else frame,
            detections=list(targets),
            snapshot=ctx.odom_supervisor.snapshot(),
        )
        ctx.blackboard.evidence = record
        return record.captured

    def _hover(self, ctx: MissionContext, already_captured: bool) -> StepStatus:
        """Hold the nadir hover for the configured duration."""
        duration = ctx.params.kinematics.hover_duration_sec
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        captured = already_captured

        logger.info("Holding nadir hover for %.1f s...", duration)

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            self._hold(ctx)
            frame = ctx.handler.take_photo(timeout_sec=0.2)
            rate.tick()

            if frame is None:
                continue
            ctx.failsafe.notify_frame_received()

            result = ctx.detector.detect(frame, conf=ctx.params.vision.confidence_threshold)
            targets = result.filter_by_class(ctx.params.vision.target_classes)
            ctx.publish_annotated_stream(
                frame,
                result,
                f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: NADIR HOVER "
                f"({deadline.elapsed_sec:.1f}s/{duration:.1f}s) | TARGETS: {len(targets)}",
            )

            if targets and not captured:
                captured = self._capture(ctx, settled=True)

        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _hold(ctx: MissionContext) -> None:
        """Command a stationary hover with the anti-climb governor engaged."""
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
