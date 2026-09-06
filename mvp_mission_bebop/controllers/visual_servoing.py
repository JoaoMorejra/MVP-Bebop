"""Coupled Image-Based Visual Servoing (IBVS) controller.

Orchestrates gimbal pitch tilt, lateral positioning velocity (vy),
and coupled longitudinal advance velocity (vx).
"""

from __future__ import annotations

import math
from typing import Tuple

from nectar.control import PIDController

from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)


class VisualServoingController:
    """Computes gimbal tilt and body-frame velocities coupled to optical target centering."""

    def __init__(
        self,
        gimbal_config: GimbalConstraintsConfig,
        gimbal_pid_cfg: GimbalPIDConfig,
        lateral_pid_cfg: LateralPIDConfig,
        vision_cfg: VisionConfig,
        kinematics_cfg: FlightKinematicsConfig,
    ) -> None:
        self.gimbal_cfg = gimbal_config
        self.vision_cfg = vision_cfg
        self.kinematics_cfg = kinematics_cfg

        self.pid_gimbal = PIDController(
            kp=gimbal_pid_cfg.kp,
            ki=gimbal_pid_cfg.ki,
            kd=gimbal_pid_cfg.kd,
            setpoint=0.0,
            output_limits=gimbal_pid_cfg.output_limits,
        )
        self.pid_lateral = PIDController(
            kp=lateral_pid_cfg.kp,
            ki=lateral_pid_cfg.ki,
            kd=lateral_pid_cfg.kd,
            setpoint=0.0,
            output_limits=lateral_pid_cfg.output_limits,
            output_deadband=lateral_pid_cfg.deadband,
        )
        self.reset()

    def reset(self) -> None:
        """Reset PID states and integrators."""
        self.pid_gimbal.reset()
        self.pid_lateral.reset()

    def compute_control(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
    ) -> Tuple[float, float, float, float, bool]:
        """Compute control outputs for the current optical observation.

        Parameters
        ----------
        target_center : Tuple[float, float]
            Optical center (cx, cy) of detected target in pixels.
        frame_dimensions : Tuple[int, int]
            Optical frame shape (width, height) in pixels.
        current_tilt_deg : float
            Current physical gimbal pitch tilt in degrees.

        Returns
        -------
        Tuple[float, float, float, float, bool]
            vx_cmd, vy_cmd, next_tilt_deg, total_pixel_error, is_nadir_aligned
        """
        frame_w, frame_h = frame_dimensions
        frame_cx = frame_w / 2.0
        frame_cy = frame_h / 2.0

        err_x = float(target_center[0] - frame_cx)
        err_y = float(target_center[1] - frame_cy)
        norm_err_x = err_x / frame_cx
        norm_err_y = err_y / frame_cy
        total_pixel_error = math.hypot(err_x, err_y)

        # 1. Lateral velocity (vy) corrects horizontal target deviation
        vy_cmd = self.pid_lateral.update(norm_err_x)

        # 2. Camera gimbal pitch PID
        gimbal_delta_deg = self.pid_gimbal.update(norm_err_y)
        desired_tilt_deg = current_tilt_deg + gimbal_delta_deg
        next_tilt_deg = max(
            self.gimbal_cfg.nadir_tilt_deg,
            min(self.gimbal_cfg.search_tilt_deg, desired_tilt_deg),
        )

        # 3. Termination condition check: Nadir position (-80 deg) and centered target
        is_nadir_aligned = (
            next_tilt_deg <= (self.gimbal_cfg.nadir_tilt_deg + 1.5)
            and total_pixel_error <= (self.vision_cfg.optical_center_tolerance_px * 1.5)
        )

        # 4. Forward velocity (vx) coupled to visual centering
        tol = self.vision_cfg.approach_centering_tolerance_px
        if abs(err_y) < tol:
            alignment_quality = (1.0 - (abs(err_y) / tol)) ** 2
            angular_headroom = (next_tilt_deg - self.gimbal_cfg.nadir_tilt_deg) / (
                self.gimbal_cfg.search_tilt_deg - self.gimbal_cfg.nadir_tilt_deg
            )
            max_spd = self.kinematics_cfg.max_approach_forward_speed
            vx_cmd = min(max_spd, max_spd * angular_headroom * alignment_quality)
        else:
            vx_cmd = 0.0

        return vx_cmd, vy_cmd, next_tilt_deg, total_pixel_error, is_nadir_aligned
