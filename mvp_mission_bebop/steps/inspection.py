"""Step 4: Motionless Nadir Hover & High-Resolution Evidence Capture."""

from __future__ import annotations

import logging
import time

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step4Inspection")


class NadirInspectionStep(BaseStep):
    """Stationary hover at nadir (-80.0 deg) with onboard 14MP snapshot and local dual-fidelity recording."""

    def __init__(self) -> None:
        super().__init__("STEP 4: Motionless Nadir Hover & Evidence Capture")

    def execute(self, ctx: MissionContext) -> StepStatus:
        if not ctx.shared_data.get("approach_finished", False):
            logger.info("Approach not completed. Skipping nadir inspection.")
            return StepStatus.SUCCESS

        logger.info("--- [%s] ---", self.name)
        hover_duration = ctx.params.kinematics.hover_duration_sec
        target_classes = ctx.params.vision.target_classes
        conf_thresh = ctx.params.vision.confidence_threshold

        logger.info("Hovering at Nadir (%.1f deg) for %.1f s and capturing evidence...", ctx.params.gimbal.nadir_tilt_deg, hover_duration)

        # 1. Guarantee camera is locked at strict nadir position
        ctx.current_tilt_deg = ctx.params.gimbal.nadir_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        # 2. Ensure zero horizontal translation, active anti-climb vz
        vz_cmd = ctx.governor.compute_vz(ctx.odom_supervisor.relative_altitude)
        ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

        hover_start_time = time.time()
        evidence_captured: bool = False
        ctx.failsafe.notify_frame_received()

        while (time.time() - hover_start_time) < hover_duration:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            vz_cmd = ctx.governor.compute_vz(ctx.odom_supervisor.relative_altitude)
            ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
            ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

            frame = ctx.handler.take_photo(timeout_sec=1.0)
            if frame is not None:
                ctx.failsafe.notify_frame_received()
                result = ctx.detector.detect(frame, conf=conf_thresh)
                targets = result.filter_by_class(target_classes)

                elapsed_hover = time.time() - hover_start_time
                mode_prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
                telemetry_text = (
                    f"{mode_prefix}STEP 4: HOVER INSPECTION ({elapsed_hover:.1f}s/{hover_duration:.1f}s) "
                    f"| TILT: -80.0deg | ALT_REL: {ctx.odom_supervisor.relative_altitude:.2f}m"
                )
                annotated = ctx.publish_annotated_stream(frame, result, telemetry_text)

                if not evidence_captured and targets:
                    # 1. Hardware onboard snapshot
                    ctx.drone.snapshot()
                    # 2. Local dual-fidelity storage
                    raw_p, ann_p = ctx.record_photographic_evidence(frame, annotated)
                    ctx.shared_data["raw_evidence_path"] = raw_p
                    ctx.shared_data["annotated_evidence_path"] = ann_p
                    evidence_captured = True
                    logger.info("Photographic evidence recorded successfully.")

                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Evidência fotográfica registrada",
                            details={"etapa": "registro fotográfico concluído, evidência armazenada"},
                            wait=False,
                        )
                    except Exception as vocal_err:
                        logger.debug("Announce dispatch failure: %s", vocal_err)

            time.sleep(0.08)

        # Fallback capture if no target confirmed during hover window
        if not evidence_captured:
            fallback_frame = ctx.handler.take_photo(timeout_sec=1.0)
            if fallback_frame is not None:
                ctx.drone.snapshot()
                fallback_result = ctx.detector.detect(fallback_frame, conf=conf_thresh)
                fallback_annotated = ctx.detector.draw_detections(
                    image=fallback_frame.copy(),
                    result=fallback_result,
                    show_labels=True,
                    show_confidence=True,
                    show_class=True,
                )
                raw_fb, _ = ctx.record_photographic_evidence(fallback_frame, fallback_annotated)
                logger.info("Evidence recorded via fallback capture at conclusion of hover.")
                try:
                    from mvp_mission_bebop.telemetry.announcer import announce_sync
                    announce_sync(
                        "Evidência fotográfica registrada",
                        details={"etapa": "registro fotográfico concluído, evidência armazenada"},
                        wait=False,
                    )
                except Exception:
                    pass

        logger.info("Step 4 Complete: Stationary inspection window concluded.")
        return StepStatus.SUCCESS
