"""Step 5: Closed-Loop Odometry Return-to-Launch & Safe Landing."""

from __future__ import annotations

import logging
import time

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step5RTL")


class ClosedLoopRTLStep(BaseStep):
    """Closed-loop proportional navigation back to takeoff origin (x0, y0) in body frame."""

    def __init__(self) -> None:
        super().__init__("STEP 5: Closed-Loop Odometry RTL & Safe Landing")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)
        rtl_cfg = ctx.params.rtl

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Iniciando Retorno à Base",
                details={"etapa": "iniciando retorno à base de lançamento"},
                wait=False,
            )
        except Exception as vocal_err:
            logger.debug("Announce dispatch failure: %s", vocal_err)

        # 1. Re-orient gimbal to frontal cruise inclination
        ctx.drone.camera_control(tilt=ctx.params.gimbal.search_tilt_deg, pan=0.0)

        # 2. Closed-loop odometry navigation back to (x0, y0)
        rtl_start_time = time.time()
        reached_takeoff_origin: bool = False

        while (time.time() - rtl_start_time) < rtl_cfg.timeout_sec:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                if not ctx.odom_supervisor.is_telemetry_healthy():
                    ctx.failsafe.trigger_emergency_land("Odometry telemetry loss during RTL.")
                    return StepStatus.FAILURE
                if ctx.odom_supervisor.is_ceiling_breached():
                    ctx.failsafe.trigger_emergency_land("Altitude ceiling breached during RTL.")
                    return StepStatus.FAILURE

            ex_body, ey_body, dist_to_launch = ctx.odom_supervisor.get_body_frame_launch_error()
            vz_cmd = ctx.governor.compute_vz(ctx.odom_supervisor.relative_altitude)

            mode_prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            logger.info(
                "%sRTL Navigating: dist=%.2f m (target <= %.2f m) | err_body=(%.2f, %.2f) m | alt_rel=%.2f m",
                mode_prefix,
                dist_to_launch,
                rtl_cfg.arrival_radius_m,
                ex_body,
                ey_body,
                ctx.odom_supervisor.relative_altitude,
            )

            # Check convergence to origin
            if dist_to_launch <= rtl_cfg.arrival_radius_m:
                logger.info(
                    "RTL Target Reached: Arrived at launch origin (dist=%.2f m <= %.2f m).",
                    dist_to_launch,
                    rtl_cfg.arrival_radius_m,
                )
                ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                reached_takeoff_origin = True
                break

            # Gentle, low-speed proportional navigation in body frame
            vx_cmd = max(-rtl_cfg.max_speed, min(rtl_cfg.max_speed, rtl_cfg.kp * ex_body))
            vy_cmd = max(-rtl_cfg.max_speed, min(rtl_cfg.max_speed, rtl_cfg.kp * ey_body))

            ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
            ctx.drone.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=vz_cmd, vyaw=0.0)
            time.sleep(0.04)

        if not reached_takeoff_origin:
            logger.warning("RTL navigation window (%.1f s) concluded. Forcing station-keeping.", rtl_cfg.timeout_sec)

        # 3. Final station-keeping hover over launch origin
        logger.info("Executing station-keeping hover over origin for %.1f s...", rtl_cfg.final_hover_delay_sec)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        ctx.drone.delay(rtl_cfg.final_hover_delay_sec)

        # 4. Terminal landing sequence
        logger.info("Executing safe terminal landing at origin...")
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        ctx.drone.land()
        ctx.shared_data["rtl_completed"] = True
        logger.info("Step 5 Complete: Safe landing executed.")

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Pouso seguro concluído",
                details={"etapa": "pouso seguro concluído na base"},
                wait=False,
            )
        except Exception:
            pass

        return StepStatus.SUCCESS
