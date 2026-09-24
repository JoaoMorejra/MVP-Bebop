"""Stage 4: motionless nadir hover and forensic evidence capture.

The capture is only worth as much as the hover beneath it. A 14-megapixel frame
taken while the airframe is still translating is motion-blurred at exactly the
scale that matters for reading a plate or a marking, so this stage verifies
stillness statistically before triggering, rather than taking a single velocity
sample at face value.

Verification is bounded: if the drone never settles within the window the
capture happens anyway, because an imperfect photograph of the scene is worth
more than none at all. The metadata records which of the two paths was taken.

The whole stage runs inside an altitude-hold window. That is not a translation
phase, so it might look unnecessary -- but the airframe arrives here having just
decelerated out of the approach, and the sink that deceleration leaves behind
does not stop at the moment the velocity command does. Without the window the
hover would settle wherever the transient left it and photograph the scene from
there, which is the same failure the translation phases had, arriving a stage
later.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.perception.worker import detector_kwargs
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.telemetry.milestones import emit_milestone

logger = logging.getLogger("Step4Inspection")


class NadirInspectionStep(BaseStep):
    """Holds a motionless nadir hover and records forensic evidence."""

    def __init__(self) -> None:
        super().__init__("STEP 4: Motionless Nadir Hover & Evidence Capture")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        # A degraded approach is the case where evidence is most valuable, not
        # least. The stage used to return immediately when the approach had not
        # confirmed nadir alignment -- which is precisely the outcome Stage 3
        # reports on any timeout or lost target -- so the common failure
        # produced no imagery at all, never moved the gimbal to nadir, and still
        # logged success. Capture anyway, from wherever the airframe got to, and
        # record in the evidence metadata that the alignment was unconfirmed so
        # nothing downstream mistakes it for a centred inspection.
        degraded = not ctx.blackboard.approach_finished
        if degraded:
            logger.warning(
                "Approach never confirmed nadir alignment. Inspecting in degraded mode: "
                "evidence will be captured and flagged as unaligned."
            )

        # Lock the gimbal at the inspection attitude for the whole stage. Stage 3
        # has already brought it here and frozen the airframe at the standoff
        # this angle frames; re-asserting it costs nothing and means a degraded
        # approach that never reached the attitude still photographs the ground
        # rather than the horizon.
        ctx.current_tilt_deg = ctx.params.gimbal.nadir_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)
        self._announce("Inspecionando acidente", "drone pairado sobre o acidente em visada nadir")

        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            return self._inspect(ctx, degraded)

    # ------------------------------------------------------------- inspection

    def _inspect(self, ctx: MissionContext, degraded: bool) -> StepStatus:
        """Settle, capture, and hold. Called inside the altitude-hold window."""
        settled = self._await_stillness(ctx)
        if ctx.interrupted():
            # ``_await_stillness`` returns False both on timeout and on an
            # emergency, and the caller could not tell them apart -- so an abort
            # raised during the settle wait was followed by a full blocking
            # capture before anything acted on it.
            logger.warning("Emergency raised during the settle wait; abandoning evidence capture.")
            self._hold(ctx)
            return StepStatus.ABORTED

        settled = settled and not degraded
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
                max_vertical_speed=cfg.settle_max_vertical_speed,
            )
        )
        deadline = Deadline(cfg.settle_timeout_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        elapsed = 0.0
        report = None

        logger.info(
            "Verifying motionlessness: speed <= %.3f m/s, position sigma <= %.3f m, "
            "commanded vz <= %.3f, sustained for %.1f s.",
            cfg.settle_max_speed_mps,
            cfg.settle_max_position_sigma_m,
            cfg.settle_max_vertical_speed,
            cfg.settle_window_sec,
        )

        while deadline.active:
            if ctx.interrupted():
                return False

            commanded_vz = self._hold(ctx)
            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            elapsed += dt

            report = detector.update(
                x=snapshot.x,
                y=snapshot.y,
                speed=snapshot.speed,
                vz=commanded_vz,
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
        """Trigger the onboard snapshot and persist local evidence.

        Station keeping is re-commanded first. The capture that follows is
        synchronous and takes appreciable time -- a blocking frame grab, a YOLO
        inference, an uncompressed PNG write measured at ~155 ms for an 856x480
        frame, a quality-100 JPEG and a JSON sidecar -- and the Bebop latches the
        last Twist it received until another arrives. Whatever the hold loop last
        sent would otherwise stay open-loop for that whole window, and the anti-
        climb governor's descent command is the thing most likely to be latched.
        """
        self._hold(ctx)
        frame = ctx.grab_frame(timeout_sec=1.5)
        if frame is None:
            logger.error("No frame available for evidence capture.")
            return False

        ctx.failsafe.notify_frame_received()
        nadir_conf = ctx.params.inspection.nadir_confidence_threshold
        result = ctx.detector.detect(
            frame, **detector_kwargs(nadir_conf, ctx.params.vision.inference_imgsz)
        )
        targets = result.filter_by_class(ctx.params.vision.target_classes)

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
        if record.captured:
            emit_milestone(
                "mission.capture_done",
                {
                    "raw_image": os.path.basename(record.raw_path) if record.raw_path else None,
                    "settled": bool(settled),
                },
            )
        return record.captured

    def _hover(self, ctx: MissionContext, already_captured: bool) -> StepStatus:
        """Hold the nadir hover for the configured duration.

        Perception runs only while evidence is still missing. The detector used
        to be called on every cycle of the hover, including the whole remainder
        of the window after the capture had already been recorded -- a CPU
        inference per cycle producing results nothing consumed. Once
        ``captured`` holds, the pipeline is disengaged and the loop reduces to
        station keeping: ``_hold`` and ``rate.tick``, nothing else.

        The frame heartbeat stops being refreshed with it. That is safe because
        this loop never evaluates system health, and Stage 5 re-arms the
        heartbeat on entry before its first health check.

        A capture triggered from inside the hover needs the camera and detector
        to itself -- ``ROSCam`` serves new frames to one consumer only -- so the
        pipeline is disengaged before it, and re-engaged only if it failed.
        """
        duration = ctx.params.kinematics.hover_duration_sec
        nadir_conf = ctx.params.inspection.nadir_confidence_threshold
        imgsz = ctx.params.vision.inference_imgsz
        max_age = ctx.params.vision.perception_max_age_sec
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        captured = already_captured
        last_generation = 0

        if captured:
            logger.info(
                "Holding nadir hover for %.1f s; evidence already recorded, vision suspended.",
                duration,
            )
        else:
            logger.info("Holding nadir hover for %.1f s, watching for a capture...", duration)
            ctx.perception.engage(conf=nadir_conf, imgsz=imgsz)

        try:
            while deadline.active:
                if ctx.interrupted():
                    return StepStatus.ABORTED

                self._hold(ctx)
                rate.tick()

                if captured:
                    continue

                sample = ctx.perception.get_latest(max_age)
                if sample is None or sample.generation == last_generation:
                    continue
                last_generation = sample.generation
                ctx.failsafe.notify_frame_received()

                targets = sample.result.filter_by_class(ctx.params.vision.target_classes)
                ctx.perception.set_status(
                    f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: NADIR HOVER "
                    f"({deadline.elapsed_sec:.1f}s/{duration:.1f}s) | TARGETS: {len(targets)}",
                )

                if targets:
                    ctx.perception.disengage()
                    captured = self._capture(ctx, settled=True)
                    if captured:
                        logger.info(
                            "Evidence recorded %.1f s into the hover; vision suspended for "
                            "the remaining %.1f s.",
                            deadline.elapsed_sec,
                            deadline.remaining_sec,
                        )
                    else:
                        ctx.perception.engage(conf=nadir_conf, imgsz=imgsz)
        finally:
            ctx.perception.disengage()
            logger.info("Hover loop cadence: %s.", rate.cadence_report())

        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _hold(ctx: MissionContext, dt: Optional[float] = None) -> float:
        """Command a stationary hover with the altitude governor engaged.

        Returns the commanded vertical speed (post-clamp), in normalized
        units, so callers that need to know whether the vertical axis is
        still actively correcting -- see :meth:`_await_stillness` -- do not
        have to recompute it.
        """
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)
        return safe_vz

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
