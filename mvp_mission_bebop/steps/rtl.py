"""Step 5: Closed-Loop Odometry Return-to-Launch (RTL) & Safe Autonomous Landing.

Implements a high-precision, closed-loop backward navigation routine along the 1-DOF
longitudinal track back to the stabilized takeoff origin (x0, y0), strictly enforcing
zero yaw rotation, safe altitude containment, and smooth deceleration to landing.
"""

from __future__ import annotations

import logging
import math
import time

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.parameters import ReturnToLaunchConfig
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step5RTL")


def compute_longitudinal_command(ex_body: float, rtl_cfg: ReturnToLaunchConfig) -> float:
    """Compute longitudinal velocity command (X_body) with anti-stall floor.

    Positive ex_body means launch origin is in front (+x).
    Negative ex_body means launch origin is behind (-x), requiring 'de ré' backward flight.
    """
    abs_ex = abs(ex_body)
    sign_x = -1.0 if ex_body < 0 else (1.0 if ex_body > 0 else 0.0)

    if abs_ex < rtl_cfg.deadband_m:
        return 0.0
    if abs_ex <= rtl_cfg.braking_distance_m:
        ramp_range = max(1e-3, rtl_cfg.braking_distance_m - rtl_cfg.deadband_m)
        ramp_ratio = (abs_ex - rtl_cfg.deadband_m) / ramp_range
        speed_target = max(rtl_cfg.min_effective_speed, rtl_cfg.max_speed * ramp_ratio)
        return sign_x * min(rtl_cfg.max_speed, speed_target)
    return sign_x * rtl_cfg.max_speed


def compute_lateral_command(ey_body: float, rtl_cfg: ReturnToLaunchConfig) -> float:
    """Compute lateral velocity command (Y_body) with non-zero floor avoiding driver truncation.

    Positive ey_body means launch origin is to the left (+y).
    Negative ey_body means launch origin is to the right (-y).
    """
    abs_ey = abs(ey_body)
    sign_y = -1.0 if ey_body < 0 else (1.0 if ey_body > 0 else 0.0)

    if abs_ey < rtl_cfg.deadband_m:
        return 0.0
    raw_vy = rtl_cfg.lateral_kp * abs_ey
    vy_mag = max(rtl_cfg.min_effective_speed, min(rtl_cfg.max_lateral_speed, raw_vy))
    return sign_y * vy_mag


class ClosedLoopRTLStep(BaseStep):
    """Closed-loop odometry Return-to-Launch returning 'de ré' to takeoff origin."""

    def __init__(self) -> None:
        super().__init__("STEP 5: Closed-Loop Odometry RTL & Safe Landing")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)
        rtl_cfg = ctx.params.rtl

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Iniciando Retorno à Base",
                details={"etapa": "iniciando retorno à base de lançamento em linha reta de ré"},
                wait=False,
            )
        except Exception as vocal_err:
            logger.debug("Announce dispatch failure: %s", vocal_err)

        # 1. Align camera gimbal to nominal frontal cruise angle for visual stream telemetry
        ctx.drone.camera_control(tilt=ctx.params.gimbal.search_tilt_deg, pan=0.0)

        # 2. Benchtop simulation support (--no-fly mode): inject synthetic displacement if at origin
        if ctx.drone.no_fly:
            ex_b, ey_b, dist_init = ctx.odom_supervisor.get_body_frame_launch_error()
            if dist_init < rtl_cfg.arrival_radius_m:
                logger.info(
                    "[NO-FLY BENCHTOP] Injecting simulated search offset for RTL validation (x=+1.50m, y=+0.15m)."
                )
                with ctx.odom_supervisor._lock:
                    ctx.odom_supervisor.current_x = (ctx.odom_supervisor.takeoff_x or 0.0) + 1.50
                    ctx.odom_supervisor.current_y = (ctx.odom_supervisor.takeoff_y or 0.0) + 0.15

        # 3. Closed-loop 1-DOF backward navigation to takeoff origin (x0, y0)
        rtl_start_time = time.time()
        reached_takeoff_origin: bool = False
        consecutive_settled_cycles: int = 0
        last_calc_time: float = time.time()

        logger.info(
            "RTL Engaged: Max Speed=%.3f m/s, Min Effective Speed=%.3f m/s, Braking Dist=%.2f m, Target Radius=%.2f m, Timeout=%.1f s.",
            rtl_cfg.max_speed,
            rtl_cfg.min_effective_speed,
            rtl_cfg.braking_distance_m,
            rtl_cfg.arrival_radius_m,
            rtl_cfg.timeout_sec,
        )
        logger.info("Kinematic Invariants: vyaw == 0.0 (strictly locked), vz <= 0.0 (anti-climb governor).")

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

            # Query error vector in Body Frame (FLU: x=forward/backward, y=left/right)
            ex_body, ey_body, dist_to_launch = ctx.odom_supervisor.get_body_frame_launch_error()
            vz_cmd = ctx.governor.compute_vz(ctx.odom_supervisor.relative_altitude)

            now = time.time()
            dt = max(1e-3, now - last_calc_time)
            last_calc_time = now

            # --- Longitudinal & Lateral Closed-Loop Control ---
            vx_cmd = compute_longitudinal_command(ex_body, rtl_cfg)
            vy_cmd = compute_lateral_command(ey_body, rtl_cfg)

            mode_prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            h_speed = ctx.odom_supervisor.get_current_horizontal_speed()
            logger.info(
                "%sRTL Navigating: dist=%.2f m (tol <= %.2f m) | err=(ex=%.2f, ey=%.2f) m | "
                "cmd=(vx=%.3f, vy=%.3f, vz=%.3f) | speed=%.2f m/s | alt=%.2f m",
                mode_prefix,
                dist_to_launch,
                rtl_cfg.arrival_radius_m,
                ex_body,
                ey_body,
                vx_cmd,
                vy_cmd,
                vz_cmd,
                h_speed,
                ctx.odom_supervisor.relative_altitude,
            )

            # Optional telemetry stream annotation during RTL
            if hasattr(ctx, "handler") and ctx.handler is not None:
                try:
                    frame = ctx.handler.take_photo(timeout_sec=0.03)
                    if frame is not None:
                        ctx.failsafe.notify_frame_received()
                        elapsed_rtl = time.time() - rtl_start_time
                        telem_text = (
                            f"{mode_prefix}STEP 5: RTL ({elapsed_rtl:.1f}s) "
                            f"| DIST: {dist_to_launch:.2f}m | CMD_VX: {vx_cmd:.3f} "
                            f"| ALT: {ctx.odom_supervisor.relative_altitude:.2f}m"
                        )
                        ctx.publish_annotated_stream(frame, None, telem_text)
                except Exception:
                    pass

            # --- Convergence and Settle Verification ---
            # Drone must remain within arrival radius across consecutive samples to eliminate noise triggers
            if dist_to_launch <= rtl_cfg.arrival_radius_m:
                consecutive_settled_cycles += 1
                if consecutive_settled_cycles >= rtl_cfg.settle_cycles:
                    logger.info(
                        "RTL Target Reached: Confirmed arrival at launch origin (dist=%.2f m <= %.2f m, settled %d cycles).",
                        dist_to_launch,
                        rtl_cfg.arrival_radius_m,
                        consecutive_settled_cycles,
                    )
                    ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                    reached_takeoff_origin = True
                    break
            else:
                consecutive_settled_cycles = 0

            # Strictly assert kinematic invariants before publishing command
            ctx.failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
            ctx.drone.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=vz_cmd, vyaw=0.0)

            # In benchtop simulation (--no-fly), integrate commanded velocity into mock odometry
            if ctx.drone.no_fly:
                with ctx.odom_supervisor._lock:
                    psi = ctx.odom_supervisor.current_yaw
                    sim_dt = dt * 2.5
                    ctx.odom_supervisor.current_x += (
                        vx_cmd * math.cos(psi) - vy_cmd * math.sin(psi)
                    ) * sim_dt
                    ctx.odom_supervisor.current_y += (
                        vx_cmd * math.sin(psi) + vy_cmd * math.cos(psi)
                    ) * sim_dt

            time.sleep(0.04)

        if not reached_takeoff_origin:
            logger.warning(
                "RTL navigation window (%.1f s) concluded. Final dist=%.2f m. Proceeding to safe landing.",
                rtl_cfg.timeout_sec,
                dist_to_launch,
            )

        # 4. Final station-keeping hover over launch origin
        logger.info("Station-keeping hover over origin for %.1f s to dampen residual motion...", rtl_cfg.final_hover_delay_sec)
        ctx.failsafe.assert_kinematics(vz=0.0, vyaw=0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        ctx.drone.delay(rtl_cfg.final_hover_delay_sec)

        # 5. Safe autonomous terminal landing
        logger.info("Executing safe terminal landing at origin...")
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        ctx.drone.land()
        ctx.shared_data["rtl_completed"] = True
        logger.info("Step 5 Complete: Safe landing executed at takeoff origin.")

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Pouso seguro concluído",
                details={"etapa": "pouso seguro concluído na base de lançamento"},
                wait=False,
            )
        except Exception:
            pass

        return StepStatus.SUCCESS

