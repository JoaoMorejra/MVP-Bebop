"""Step 1: IMU Calibration, Zero Altitude Reference, Takeoff & Stabilization."""

from __future__ import annotations

import logging
import time

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step1Takeoff")


class TakeoffStep(BaseStep):
    """Calibrates flat trim and ground reference, executes takeoff, and polls stream."""

    def __init__(self) -> None:
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        # 1. Pre-align gimbal to initial search inclination
        ctx.current_tilt_deg = ctx.params.gimbal.search_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        # 2. IMU flat trim calibration
        logger.info("Executing IMU flat trim on level surface...")
        ctx.drone.flat_trim()
        ctx.drone.delay(2.0)

        # 3. Establish ground launch reference coordinates (x0, y0, z0)
        ctx.odom_supervisor.calibrate_ground_reference()

        # 4. Pre-flight synchronization countdown & optical warmup
        countdown = ctx.params.kinematics.countdown_sec
        announced_second_3 = False
        countdown_start = time.time()
        if countdown > 0:
            logger.info("Pre-flight countdown and sensor warmup initiated (%.1f s)...", countdown)
            while True:
                if ctx.emergency_event.is_set():
                    return StepStatus.ABORTED
                elapsed_prep = time.time() - countdown_start
                remaining = countdown - elapsed_prep
                if remaining <= 0:
                    break

                # Poll optical frame and run warmup inference so YOLO is fully loaded
                frame = ctx.handler.take_photo(timeout_sec=0.2)
                if frame is not None:
                    ctx.failsafe.notify_frame_received()
                    try:
                        warmup_result = ctx.detector.detect(frame)
                        status_text = f"CONTAGEM REGRESSIVA: {remaining:.1f}s | YOLO PRONTO"
                        ctx.publish_annotated_stream(frame, warmup_result, status_text)
                    except Exception as inf_err:
                        logger.debug("Warmup inference notice: %s", inf_err)
                else:
                    time.sleep(0.08)

                # Autorização vocal de decolagem no limiar de 3s
                if remaining <= 3.2 and not announced_second_3:
                    announced_second_3 = True
                    logger.info("Takeoff synchronization window reached. Announcing takeoff clearance.")
                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Decolagem autorizada",
                            details={"etapa": "decolagem autorizada, iniciando voo"},
                            wait=False,
                        )
                    except Exception as vocal_err:
                        logger.debug("Announce dispatch failure: %s", vocal_err)

        # 5. Autonomous takeoff
        target_alt = ctx.params.kinematics.target_altitude_m
        ceiling = ctx.odom_supervisor.altitude_ceiling
        logger.info("Issuing Takeoff command (Target Alt: %.2f m, Ceiling: %.2f m)...", target_alt, ceiling)

        if not ctx.drone.takeoff(altitude=target_alt):
            logger.critical("Autonomous takeoff rejected by drone platform.")
            try:
                from mvp_mission_bebop.telemetry.announcer import announce_sync
                announce_sync(
                    "Falha na decolagem",
                    details={"erro": "Decolagem rejeitada pela controladora de voo"},
                    priority="CRITICAL",
                    wait=True,
                )
            except Exception:
                pass
            return StepStatus.FAILURE

        # 6. Hover stabilization with active optical frame polling
        stabilize_duration = ctx.params.kinematics.takeoff_stabilize_duration_sec
        logger.info("Stabilizing in hover for %.1f s and polling stream...", stabilize_duration)

        stabilize_start = time.time()
        while (time.time() - stabilize_start) < stabilize_duration:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            poll_frame = ctx.handler.take_photo(timeout_sec=0.5)
            if poll_frame is not None:
                ctx.failsafe.notify_frame_received()
            time.sleep(0.05)

        ctx.failsafe.notify_frame_received()

        # Freeze airborne hover coordinates to establish precise return origin
        ctx.odom_supervisor.freeze_hover_takeoff_origin()

        # 7. Post-takeoff health verification
        if not ctx.drone.no_fly:
            healthy, reason = ctx.failsafe.evaluate_system_health()
            if not healthy:
                ctx.failsafe.trigger_emergency_land(reason)
                return StepStatus.FAILURE

        logger.info(
            "Step 1 Complete: Stabilized at relative altitude %.2f m.",
            ctx.odom_supervisor.relative_altitude,
        )

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Decolagem concluída",
                details={"etapa": "decolagem concluída, iniciando varredura"},
                wait=False,
            )
        except Exception as vocal_err:
            logger.debug("Announce dispatch failure: %s", vocal_err)

        return StepStatus.SUCCESS
