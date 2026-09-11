"""Unit tests for flight controllers and altitude governors."""

import pytest

from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.parameters import (
    AltitudeGovernorConfig,
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)


def test_altitude_anti_climb_governor_deadband():
    cfg = AltitudeGovernorConfig(deadband_m=0.05, kp=0.8, kd=0.02, max_descent_speed=0.08)
    gov = AltitudeAntiClimbGovernor(target_altitude=1.0, config=cfg)

    # Within deadband: alt=1.02m (err=0.02m <= 0.05m deadband)
    vz = gov.compute_vz(current_relative_alt=1.02)
    assert vz == 0.0

    # Below target: alt=0.90m
    vz_below = gov.compute_vz(current_relative_alt=0.90)
    assert vz_below == 0.0


def test_altitude_anti_climb_governor_corrective_descent():
    cfg = AltitudeGovernorConfig(deadband_m=0.03, kp=0.8, kd=0.0, max_descent_speed=0.08)
    gov = AltitudeAntiClimbGovernor(target_altitude=1.0, config=cfg)

    # Above target by 0.20m (target=1.0, current=1.20)
    vz = gov.compute_vz(current_relative_alt=1.20)
    assert vz < 0.0
    assert vz >= -0.08

    # Extreme altitude above target: should clamp to -max_descent_speed
    vz_clamped = gov.compute_vz(current_relative_alt=5.0)
    assert vz_clamped == -0.08


def test_visual_servoing_centering_phase_active_lateral_no_advance():
    """Verify that during Phase 1 (centering), vx == 0.0 and vy actively corrects."""
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=10.0, ki=0.0, kd=0.5, output_limits=(-10.0, 10.0))
    lateral_pid = LateralPIDConfig(kp=0.35, ki=0.0, kd=0.03, output_limits=(-0.22, 0.22), deadband=0.005, min_effective_velocity=0.06)
    vision_cfg = VisionConfig(optical_center_tolerance_px=35.0, confirmation_frames=3)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.15)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # Target off-center to the right (cx=480 > 320, err_x=+160 px)
    vx, vy, next_tilt, err_px, is_nadir = controller.compute_control(
        target_center=(480.0, 240.0),
        frame_dimensions=frame_dims,
        current_tilt_deg=-20.0,
    )

    # Phase 1: drone must NOT advance forward while decentered
    assert vx == 0.0
    # Must command lateral correction (negative vy moves right in body frame)
    assert vy < -0.05
    assert not is_nadir
    assert controller.phase.value == "centering"


def test_visual_servoing_two_phase_transition_after_centering():
    """Verify transition from CENTERING to APPROACHING after target is centered for consecutive frames."""
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=10.0, ki=0.0, kd=0.5, output_limits=(-10.0, 10.0))
    lateral_pid = LateralPIDConfig(kp=0.35, ki=0.0, kd=0.03, output_limits=(-0.22, 0.22), deadband=0.005, min_effective_velocity=0.06)
    vision_cfg = VisionConfig(optical_center_tolerance_px=35.0, confirmation_frames=3)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.15)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # Feed centered target (320, 240)
    # Frame 1: CENTERING
    vx1, vy1, _, _, _ = controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert vx1 == 0.0
    assert controller.phase.value == "centering"

    # Frame 2: CENTERING
    vx2, vy2, _, _, _ = controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert vx2 == 0.0
    assert controller.phase.value == "centering"

    # Frame 3: Reaches confirmation threshold -> transitions to APPROACHING
    vx3, vy3, _, _, _ = controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert controller.phase.value == "approaching"
    assert controller.pop_transition_to_approach() is True

    # Frame 4: In APPROACHING phase -> forward velocity is now active
    vx4, vy4, next_tilt4, _, _ = controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert vx4 > 0.0
    assert controller.phase.value == "approaching"


def test_visual_servoing_lateral_corridor_pauses_forward_motion():
    """Verify that lateral deviation during APPROACHING pauses vx to prevent losing target."""
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=10.0, ki=0.0, kd=0.5, output_limits=(-10.0, 10.0))
    lateral_pid = LateralPIDConfig(kp=0.35, ki=0.0, kd=0.03, output_limits=(-0.22, 0.22), deadband=0.005)
    vision_cfg = VisionConfig(optical_center_tolerance_px=35.0, confirmation_frames=2)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.15)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # Transition to APPROACHING
    for _ in range(2):
        controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert controller.phase.value == "approaching"

    # Target drifts laterally: cx = 400 (err_x = 80 > corridor_tol 56)
    vx_drift, vy_drift, _, _, _ = controller.compute_control(
        target_center=(400.0, 240.0),
        frame_dimensions=frame_dims,
        current_tilt_deg=-25.0,
    )
    # Forward advance must be paused (vx == 0) to allow lateral re-centering
    assert vx_drift == 0.0
    assert vy_drift < 0.0  # Lateral correction active


def test_visual_servoing_downward_tilt_progression():
    """Verify camera tilts downward in APPROACHING phase when target moves into lower frame."""
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=18.0, ki=0.0, kd=1.2, output_limits=(-18.0, 18.0))
    lateral_pid = LateralPIDConfig(kp=0.35, ki=0.0, kd=0.03, output_limits=(-0.22, 0.22), deadband=0.005)
    vision_cfg = VisionConfig(optical_center_tolerance_px=35.0, confirmation_frames=1)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.15)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # Immediately transition to APPROACHING
    controller.compute_control(target_center=(320.0, 240.0), frame_dimensions=frame_dims, current_tilt_deg=-20.0)
    assert controller.phase.value == "approaching"

    # Target in lower frame (cy=340 > frame_cy=240, err_y=+100 px)
    import time
    time.sleep(0.05)
    vx, vy, next_tilt, err_px, is_nadir = controller.compute_control(
        target_center=(320.0, 340.0),
        frame_dimensions=frame_dims,
        current_tilt_deg=-20.0,
    )
    assert next_tilt < -20.0  # Tilts downward (more negative toward nadir)


def test_visual_servoing_nadir_alignment():
    """Verify target aligned at nadir declares is_nadir_aligned True."""
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=18.0, ki=0.0, kd=1.2, output_limits=(-18.0, 18.0))
    lateral_pid = LateralPIDConfig(kp=0.35, ki=0.0, kd=0.03, output_limits=(-0.22, 0.22), deadband=0.005)
    vision_cfg = VisionConfig(optical_center_tolerance_px=35.0, confirmation_frames=1)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.15)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # In NADIR phase:
    controller._phase = controller.phase.__class__.NADIR
    vx, vy, next_tilt, err_px, is_nadir = controller.compute_control(
        target_center=(320.0, 240.0),
        frame_dimensions=frame_dims,
        current_tilt_deg=-80.0,
    )

    assert err_px < 5.0
    assert next_tilt == -80.0
    assert is_nadir is True
