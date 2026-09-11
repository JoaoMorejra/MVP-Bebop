"""Coupled Image-Based Visual Servoing (IBVS) controller with 2-Phase Target Alignment.

Phase 1 (CENTERING):
  Actively centers the detection in the middle of the camera frame via lateral PID (vy).
  Forward velocity is locked at 0.0 (vx = 0.0) and camera tilt remains at search tilt.
  Prevents premature overflight from causing target loss out of frame edges.

Phase 2 (APPROACHING):
  Once the detection is confirmed centered within tolerance, initiates longitudinal advance (vx > 0)
  coupled with gimbal pitch PID tilting from search tilt down to nadir (-80.0°).
  Includes a safety corridor: if lateral error exceeds corridor tolerance, forward advance
  is immediately held (vx = 0.0) until re-centered.

Phase 3 (NADIR):
  Directly over the target at -80.0°: performs fine 2D Cartesian alignment before
  confirming nadir lock.
"""

from __future__ import annotations

from enum import Enum
import logging
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

logger = logging.getLogger("VisualServoingController")


class TrackingPhase(str, Enum):
    """Operational sub-phases of visual servoing tracking."""

    CENTERING = "centering"
    APPROACHING = "approaching"
    NADIR = "nadir"


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
        self.lateral_pid_cfg = lateral_pid_cfg
        self.gimbal_pid_cfg = gimbal_pid_cfg

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
        self.min_effective_velocity: float = getattr(lateral_pid_cfg, "min_effective_velocity", 0.06)

        self._phase: TrackingPhase = TrackingPhase.CENTERING
        self._consecutive_centered_frames: int = 0
        self._consecutive_decentered_frames: int = 0
        self._just_transitioned_to_approach: bool = False
        self.reset()

    @property
    def phase(self) -> TrackingPhase:
        """Current operational phase of tracking."""
        return self._phase

    def pop_transition_to_approach(self) -> bool:
        """Query and clear the flag indicating a transition from CENTERING to APPROACHING."""
        transitioned = self._just_transitioned_to_approach
        self._just_transitioned_to_approach = False
        return transitioned

    def reset(self) -> None:
        """Reset PID states, phase machine, and integrators."""
        self.pid_gimbal.reset()
        self.pid_lateral.reset()
        self._phase = TrackingPhase.CENTERING
        self._consecutive_centered_frames = 0
        self._consecutive_decentered_frames = 0
        self._just_transitioned_to_approach = False

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
        # If PID was on its initial update (dt == 0) and returned zero, provide direct proportional response
        if vy_cmd == 0.0 and abs(norm_err_x) > self.lateral_pid_cfg.deadband:
            vy_cmd = max(
                self.lateral_pid_cfg.output_limits[0],
                min(self.lateral_pid_cfg.output_limits[1], -self.lateral_pid_cfg.kp * norm_err_x),
            )

        # Overcome Bebop 2 internal optical flow deadband when correction is needed
        deadband_px = frame_cx * self.lateral_pid_cfg.deadband
        if abs(err_x) > deadband_px and abs(vy_cmd) > 1e-4:
            if abs(vy_cmd) < self.min_effective_velocity:
                vy_cmd = math.copysign(self.min_effective_velocity, vy_cmd)

        vx_cmd = 0.0
        next_tilt_deg = current_tilt_deg
        is_nadir_aligned = False

        # Phase 1: CENTERING (Stationary Optical Centering in Middle of Camera)
        if self._phase == TrackingPhase.CENTERING:
            vx_cmd = 0.0

            # Keep gimbal at search tilt (or gently center within narrow band)
            gimbal_delta_deg = self.pid_gimbal.update(norm_err_y)
            if gimbal_delta_deg == 0.0 and abs(norm_err_y) > 0.01:
                gimbal_delta_deg = max(
                    self.gimbal_pid_cfg.output_limits[0],
                    min(self.gimbal_pid_cfg.output_limits[1], -self.gimbal_pid_cfg.kp * norm_err_y),
                )
            desired_tilt_deg = current_tilt_deg + gimbal_delta_deg
            next_tilt_deg = max(
                self.gimbal_cfg.search_tilt_deg - 5.0,
                min(self.gimbal_cfg.search_tilt_deg + 5.0, desired_tilt_deg),
            )

            # Centering criterion: target must be inside optical tolerance for consecutive frames
            is_centered_x = abs(err_x) <= self.vision_cfg.optical_center_tolerance_px
            is_centered_y = abs(err_y) <= (self.vision_cfg.optical_center_tolerance_px * 1.5)

            if is_centered_x and is_centered_y:
                self._consecutive_centered_frames += 1
                if self._consecutive_centered_frames >= self.vision_cfg.confirmation_frames:
                    logger.info(
                        "Target optical center locked (err_x=%.1f px, err_y=%.1f px). "
                        "Transitioning from CENTERING to APPROACHING phase.",
                        err_x,
                        err_y,
                    )
                    self._phase = TrackingPhase.APPROACHING
                    self._just_transitioned_to_approach = True
                    self._consecutive_decentered_frames = 0
            else:
                self._consecutive_centered_frames = 0

            return vx_cmd, vy_cmd, next_tilt_deg, total_pixel_error, False

        # Phase 2: APPROACHING (Coupled Forward Advance & Gimbal Pitch Downward)
        if self._phase == TrackingPhase.APPROACHING:
            # Camera gimbal pitch PID progresses toward nadir (-80 deg)
            gimbal_delta_deg = self.pid_gimbal.update(norm_err_y)
            if gimbal_delta_deg == 0.0 and abs(norm_err_y) > 0.01:
                gimbal_delta_deg = max(
                    self.gimbal_pid_cfg.output_limits[0],
                    min(self.gimbal_pid_cfg.output_limits[1], -self.gimbal_pid_cfg.kp * norm_err_y),
                )

            # Check lateral corridor gate first
            corridor_tol = self.vision_cfg.optical_center_tolerance_px * 1.6
            is_in_corridor = abs(err_x) <= corridor_tol

            # When advancing forward in corridor, ensure steady progression toward nadir
            if is_in_corridor:
                gimbal_delta_deg = min(gimbal_delta_deg, -2.0)

            desired_tilt_deg = current_tilt_deg + gimbal_delta_deg
            next_tilt_deg = max(
                self.gimbal_cfg.nadir_tilt_deg,
                min(self.gimbal_cfg.search_tilt_deg, desired_tilt_deg),
            )

            # Check if camera reached nadir position
            if next_tilt_deg <= (self.gimbal_cfg.nadir_tilt_deg + 2.5):
                self._phase = TrackingPhase.NADIR

            # Longitudinal velocity proportional to remaining angular distance to nadir
            nadir_range = self.gimbal_cfg.search_tilt_deg - self.gimbal_cfg.nadir_tilt_deg
            if nadir_range > 0.0:
                nadir_progress = (next_tilt_deg - self.gimbal_cfg.nadir_tilt_deg) / nadir_range
                nadir_progress = max(0.20, min(1.0, nadir_progress))
            else:
                nadir_progress = 0.20

            max_spd = self.kinematics_cfg.max_approach_forward_speed
            base_vx = max_spd * nadir_progress

            # Lateral Corridor Gate: If target drifts laterally, halt forward motion
            if not is_in_corridor:
                vx_cmd = 0.0
                self._consecutive_decentered_frames += 1
                if self._consecutive_decentered_frames >= 10:
                    logger.warning(
                        "Target drifted significantly (err_x=%.1f px). Reverting to CENTERING phase.",
                        err_x,
                    )
                    self._phase = TrackingPhase.CENTERING
                    self._consecutive_centered_frames = 0
                    self._consecutive_decentered_frames = 0
            else:
                vx_cmd = base_vx
                self._consecutive_decentered_frames = 0

            return vx_cmd, vy_cmd, next_tilt_deg, total_pixel_error, False

        # Phase 3: NADIR (Fine Positioning Directly Over Target)
        next_tilt_deg = self.gimbal_cfg.nadir_tilt_deg
        fine_speed = self.kinematics_cfg.max_approach_forward_speed * 0.40
        vx_cmd = -1.0 * fine_speed * max(-1.0, min(1.0, norm_err_y))
        if abs(err_y) < (self.vision_cfg.optical_center_tolerance_px * 0.50):
            vx_cmd = 0.0
        if abs(err_x) < (self.vision_cfg.optical_center_tolerance_px * 0.50):
            vy_cmd = 0.0

        is_nadir_aligned = total_pixel_error <= (self.vision_cfg.optical_center_tolerance_px * 1.5)
        return vx_cmd, vy_cmd, next_tilt_deg, total_pixel_error, is_nadir_aligned
