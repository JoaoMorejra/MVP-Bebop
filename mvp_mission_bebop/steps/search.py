"""Step 2: Linear Forward Search with Strict Kinematic Lock."""

from __future__ import annotations

import logging
import time

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step2Search")


class ForwardSearchStep(BaseStep):
    """Executes forward search cruise while running YOLO inference and enforcing kinematic locks."""

    def __init__(self) -> None:
        super().__init__("STEP 2: Linear Forward Search")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)
        search_timeout = ctx.params.timeouts.search_timeout_sec
        cruise_speed = ctx.params.kinematics.forward_cruise_velocity
        target_classes = ctx.params.vision.target_classes
        conf_thresh = ctx.params.vision.confidence_threshold
        confirm_frames = ctx.params.vision.confirmation_frames

        logger.info("Initiating Linear Search: Timeout=%.1f s, Speed=%.3f", search_timeout, cruise_speed)
        logger.info("Kinematic Constraint Active: vz <= 0.0, vyaw = 0.0 strictly enforced.")

        search_start_time = time.time()
        consecutive_detections: int = 0
        ctx.failsafe.notify_frame_received()

        while (time.time() - search_start_time) < search_timeout:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            frame = ctx.handler.take_photo(timeout_sec=1.0)
            if frame is None:
                continue
            ctx.failsafe.notify_frame_received()

            result = ctx.detector.detect(frame, conf=conf_thresh)
            targets = result.filter_by_class(target_classes)

            vz_cmd = ctx.governor.compute_vz(ctx.odom_supervisor.relative_altitude)

            elapsed = time.time() - search_start_time
            mode_prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            telemetry_text = (
                f"{mode_prefix}STEP 2: SEARCH ({elapsed:.1f}s/{search_timeout:.1f}s) "
                f"| TILT: {ctx.current_tilt_deg:.1f}deg | ALT_REL: {ctx.odom_supervisor.relative_altitude:.2f}m"
            )
            ctx.publish_annotated_stream(frame, result, telemetry_text)

            if targets:
                consecutive_detections += 1
                best_target = max(targets, key=lambda d: d.confidence)
                logger.info(
                    "Target detected (%d/%d): class='%s', conf=%.2f, alt=%.2f m",
                    consecutive_detections,
                    confirm_frames,
                    best_target.class_name,
                    best_target.confidence,
                    ctx.odom_supervisor.relative_altitude,
                )
                if consecutive_detections >= confirm_frames:
                    logger.info("Target '%s' confirmed. Halting forward translation.", best_target.class_name)
                    ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                    ctx.shared_data["target_confirmed"] = True
                    ctx.shared_data["last_confirmed_tilt_deg"] = ctx.current_tilt_deg
                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Acidente detectado",
                            details={"etapa": "alvo detectado na pista, iniciando aproximação"},
                            wait=False,
                        )
                    except Exception as vocal_err:
                        logger.debug("Announce dispatch failure: %s", vocal_err)

                    return StepStatus.SUCCESS
            else:
                consecutive_detections = 0
                ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                ctx.drone.move_velocity(vx=cruise_speed, vy=0.0, vz=vz_cmd, vyaw=0.0)
                time.sleep(0.04)

        logger.warning("Search timeout (%.1f s) exceeded without target confirmation.", search_timeout)
        ctx.shared_data["target_confirmed"] = False
        return StepStatus.SUCCESS
