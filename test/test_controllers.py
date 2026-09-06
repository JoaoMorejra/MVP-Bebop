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


def test_visual_servoing_controller_bounds():
    gimbal_cfg = GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0)
    gimbal_pid = GimbalPIDConfig(kp=10.0, ki=0.0, kd=0.5, output_limits=(-10.0, 10.0))
    lateral_pid = LateralPIDConfig(kp=0.1, ki=0.0, kd=0.0, output_limits=(-0.05, 0.05), deadband=0.005)
    vision_cfg = VisionConfig(optical_center_tolerance_px=25.0, approach_centering_tolerance_px=20.0)
    kinematics_cfg = FlightKinematicsConfig(max_approach_forward_speed=0.04)

    controller = VisualServoingController(
        gimbal_config=gimbal_cfg,
        gimbal_pid_cfg=gimbal_pid,
        lateral_pid_cfg=lateral_pid,
        vision_cfg=vision_cfg,
        kinematics_cfg=kinematics_cfg,
    )

    frame_dims = (640, 480)
    # Target right at optical center (320, 240)
    vx, vy, next_tilt, err_px, is_nadir = controller.compute_control(
        target_center=(320.0, 240.0),
        frame_dimensions=frame_dims,
        current_tilt_deg=-20.0,
    )

    assert err_px == pytest.approx(0.0, abs=1e-3)
    assert -80.0 <= next_tilt <= -20.0
    assert not is_nadir  # At -20 deg, not yet aligned at nadir (-80)
