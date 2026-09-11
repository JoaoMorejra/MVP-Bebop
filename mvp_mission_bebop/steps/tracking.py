"""Step 3: Visual Servoing (IBVS), Gimbal Pitch & Coupled Guidance."""

from __future__ import annotations

import logging
import time
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step3Tracking")


class VisualServoingStep(BaseStep):
    """High-bandwidth visual servoing coupling camera gimbal pitch and longitudinal advance."""

    def __init__(self) -> None:
        super().__init__("STEP 3: Visual Servoing & Coupled Guidance")

    def execute(self, ctx: MissionContext) -> StepStatus:
        if not ctx.shared_data.get("target_confirmed", False):
            logger.info("Target not confirmed during search. Skipping visual servoing.")
            ctx.shared_data["approach_finished"] = False
            return StepStatus.SUCCESS

        logger.info("--- [%s] ---", self.name)
        nadir_tilt = ctx.params.gimbal.nadir_tilt_deg
        search_tilt = ctx.params.gimbal.search_tilt_deg
        tracking_timeout = ctx.params.timeouts.tracking_timeout_sec
        recovery_timeout = ctx.params.timeouts.target_recovery_timeout_sec
        target_classes = ctx.params.vision.target_classes
        conf_thresh = ctx.params.vision.confidence_threshold

        logger.info("Tracking objective: pitch gimbal from %.1f deg to %.1f deg centered.", search_tilt, nadir_tilt)

        ctx.visual_controller.reset()
        last_confirmed_tilt = ctx.current_tilt_deg
        tracking_start_time = time.time()
        approach_finished = False
        consecutive_lost: int = 0
        nadir_dwell_start: Optional[float] = None
        ctx.failsafe.notify_frame_received()

        while (time.time() - tracking_start_time) < tracking_timeout:
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

            phase_tag = ctx.visual_controller.phase.value.upper()
            mode_prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            telemetry_text = (
                f"{mode_prefix}STEP 3: [{phase_tag}] | TILT: {ctx.current_tilt_deg:.1f}deg "
                f"| ALT_REL: {ctx.odom_supervisor.relative_altitude:.2f}m"
            )
            ctx.publish_annotated_stream(frame, result, telemetry_text)

            if targets:
                consecutive_lost = 0
                primary_target = max(targets, key=lambda d: d.confidence)
                (
                    vx_cmd,
                    vy_cmd,
                    next_tilt,
                    total_err,
                    is_nadir_aligned,
                ) = ctx.visual_controller.compute_control(
                    target_center=primary_target.center,
                    frame_dimensions=(ctx.frame_width, ctx.frame_height),
                    current_tilt_deg=ctx.current_tilt_deg,
                )

                if ctx.visual_controller.pop_transition_to_approach():
                    logger.info("Target centered in camera. Commencing Phase 2 overflight & gimbal pitch.")
                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Alvo centralizado",
                            details={"etapa": "iniciando sobrevoo e aproximação ao nadir"},
                            wait=False,
                        )
                    except Exception as vocal_err:
                        logger.debug("Announce dispatch failure: %s", vocal_err)

                ctx.current_tilt_deg = next_tilt
                last_confirmed_tilt = next_tilt
                ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

                at_nadir_position = (next_tilt <= (nadir_tilt + 2.5))
                if at_nadir_position:
                    if nadir_dwell_start is None:
                        nadir_dwell_start = time.time()
                else:
                    nadir_dwell_start = None

                nadir_settled_long_enough = (
                    nadir_dwell_start is not None and (time.time() - nadir_dwell_start) >= 2.0
                )

                if is_nadir_aligned or nadir_settled_long_enough:
                    logger.info(
                        "Target aligned at Nadir (%.1f deg) with radial error %.1f px (settled: %s).",
                        nadir_tilt,
                        total_err,
                        nadir_settled_long_enough,
                    )
                    ctx.current_tilt_deg = nadir_tilt
                    ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)
                    ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                    approach_finished = True

                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Nadir alinhado",
                            details={"etapa": "drone sobrevoando exatamente sobre o alvo"},
                            wait=False,
                        )
                    except Exception as vocal_err:
                        logger.debug("Announce dispatch failure: %s", vocal_err)

                    break

                ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                ctx.drone.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=vz_cmd, vyaw=0.0)
                time.sleep(0.03)

            else:
                consecutive_lost += 1
                if consecutive_lost < 3:
                    # Brief loss: maintain current heading/gimbal and retry next frame
                    time.sleep(0.04)
                    continue

                consecutive_lost = 0
                # Sustained target loss: Enter Deterministic Recovery Sub-routine
                logger.warning(
                    "Target lost during approach (%d frames). Halting translation and reverting gimbal to %.1f deg.",
                    3,
                    last_confirmed_tilt,
                )
                ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

                ctx.current_tilt_deg = last_confirmed_tilt
                ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

                recovery_start_time = time.time()
                target_recovered = False

                while (time.time() - recovery_start_time) < recovery_timeout:
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

                    rec_frame = ctx.handler.take_photo(timeout_sec=1.0)
                    if rec_frame is None:
                        continue
                    ctx.failsafe.notify_frame_received()

                    rec_result = ctx.detector.detect(rec_frame, conf=conf_thresh)
                    rec_targets = rec_result.filter_by_class(target_classes)

                    telemetry_text = (
                        f"{mode_prefix}TARGET RECOVERY | TILT: {ctx.current_tilt_deg:.1f}deg "
                        f"| ALT_REL: {ctx.odom_supervisor.relative_altitude:.2f}m"
                    )
                    ctx.publish_annotated_stream(rec_frame, rec_result, telemetry_text)

                    if rec_targets:
                        logger.info("Target re-acquired during recovery window. Resuming tracking.")
                        ctx.visual_controller.reset()
                        target_recovered = True
                        break
                    time.sleep(0.04)

                if not target_recovered:
                    logger.error("Recovery failed: Target not found within %.1f s window.", recovery_timeout)
                    break

        ctx.shared_data["approach_finished"] = approach_finished
        if not approach_finished:
            logger.warning("Step 3 did not achieve full nadir lock. Transitioning directly to RTL.")

        return StepStatus.SUCCESS
