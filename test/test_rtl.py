"""Unit tests for Step 5: Closed-Loop Odometry Return-to-Launch (RTL)."""

import math
import pytest

from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    ReturnToLaunchConfig,
    TimeoutsConfig,
)
from mvp_mission_bebop.steps.rtl import (
    compute_lateral_command,
    compute_longitudinal_command,
)
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


@pytest.fixture
def rtl_config() -> ReturnToLaunchConfig:
    return ReturnToLaunchConfig(
        max_speed=0.10,
        kp=0.15,
        kd=0.01,
        lateral_kp=0.18,
        max_lateral_speed=0.05,
        braking_distance_m=0.45,
        deadband_m=0.03,
        min_effective_speed=0.035,
        settle_cycles=3,
        arrival_radius_m=0.20,
        timeout_sec=60.0,
        final_hover_delay_sec=2.0,
    )


def test_longitudinal_deadband(rtl_config: ReturnToLaunchConfig):
    """Command must be exactly 0 inside deadband to avoid micro-hunting."""
    assert compute_longitudinal_command(0.0, rtl_config) == 0.0
    assert compute_longitudinal_command(0.015, rtl_config) == 0.0
    assert compute_longitudinal_command(-0.025, rtl_config) == 0.0


def test_longitudinal_anti_stall_floor(rtl_config: ReturnToLaunchConfig):
    """Small position errors outside deadband must respect anti-stall floor.

    Ensures pitch commands sent to Bebop driver are at least min_effective_speed
    (3.5%), preventing aerodynamic stall before reaching the arrival radius.
    """
    # 5cm ahead of target (ex_body = -0.05m -> target is behind)
    cmd_neg = compute_longitudinal_command(-0.05, rtl_config)
    assert cmd_neg < 0.0
    assert abs(cmd_neg) >= rtl_config.min_effective_speed
    assert abs(cmd_neg) <= rtl_config.max_speed

    # 5cm behind target (ex_body = +0.05m -> target is ahead)
    cmd_pos = compute_longitudinal_command(0.05, rtl_config)
    assert cmd_pos > 0.0
    assert cmd_pos >= rtl_config.min_effective_speed
    assert cmd_pos <= rtl_config.max_speed


def test_longitudinal_cruise_and_deceleration_ramp(rtl_config: ReturnToLaunchConfig):
    """Command must be full max_speed outside braking distance and ramp down smoothly."""
    # Far outside braking distance (2.0m)
    assert compute_longitudinal_command(-2.0, rtl_config) == -rtl_config.max_speed
    assert compute_longitudinal_command(2.0, rtl_config) == rtl_config.max_speed

    # At exact braking boundary (0.45m)
    cmd_boundary = compute_longitudinal_command(-0.45, rtl_config)
    assert pytest.approx(cmd_boundary, abs=1e-3) == -rtl_config.max_speed

    # Midway inside braking distance (0.24m)
    cmd_midway = compute_longitudinal_command(-0.24, rtl_config)
    assert cmd_midway < 0.0
    assert abs(cmd_midway) > rtl_config.min_effective_speed
    assert abs(cmd_midway) < rtl_config.max_speed


def test_lateral_deadband(rtl_config: ReturnToLaunchConfig):
    """Lateral command must be 0 inside deadband."""
    assert compute_lateral_command(0.0, rtl_config) == 0.0
    assert compute_lateral_command(0.02, rtl_config) == 0.0
    assert compute_lateral_command(-0.025, rtl_config) == 0.0


def test_lateral_unquantized_anti_truncation_floor(rtl_config: ReturnToLaunchConfig):
    """Lateral command must enforce min_effective_speed to avoid driver int8 truncation.

    Without the floor, lateral_kp * ey = 0.18 * 0.05 = 0.009 (0.9%), which Bebop's
    C++ driver truncates to int8(0.009 * 100) = 0, completely nullifying lateral correction.
    """
    # 5cm deviation to the left (ey = +0.05 -> origin is left)
    vy_cmd = compute_lateral_command(0.05, rtl_config)
    assert vy_cmd > 0.0
    assert vy_cmd >= rtl_config.min_effective_speed
    # Must produce at least 3% when multiplied by 100 for Bebop driver
    assert int(vy_cmd * 100.0) >= 3

    # 5cm deviation to the right (ey = -0.05 -> origin is right)
    vy_cmd_neg = compute_lateral_command(-0.05, rtl_config)
    assert vy_cmd_neg < 0.0
    assert abs(vy_cmd_neg) >= rtl_config.min_effective_speed
    assert int(vy_cmd_neg * 100.0) <= -3


def test_lateral_clamping(rtl_config: ReturnToLaunchConfig):
    """Large lateral deviations must clamp to max_lateral_speed without rolling aggressively."""
    vy_large = compute_lateral_command(1.5, rtl_config)
    assert vy_large == rtl_config.max_lateral_speed

    vy_large_neg = compute_lateral_command(-1.5, rtl_config)
    assert vy_large_neg == -rtl_config.max_lateral_speed


def test_body_frame_launch_error_transform():
    """Verify coordinate transformation from odom to Body Frame (FLU)."""
    kin_cfg = FlightKinematicsConfig()
    time_cfg = TimeoutsConfig()
    odom_sup = OdometrySupervisor(kinematics_cfg=kin_cfg, timeouts_cfg=time_cfg)

    # Calibrate takeoff origin at (0, 0)
    odom_sup.takeoff_x = 0.0
    odom_sup.takeoff_y = 0.0

    # Scenario 1: Drone flew forward 2.0m, drifted 0.5m left, heading 0 rad
    odom_sup.current_x = 2.0
    odom_sup.current_y = 0.5
    odom_sup.current_yaw = 0.0

    ex, ey, dist = odom_sup.get_body_frame_launch_error()
    assert pytest.approx(dist, abs=1e-3) == math.hypot(-2.0, -0.5)
    # Target is behind (negative x) and to the right (negative y)
    assert pytest.approx(ex, abs=1e-3) == -2.0
    assert pytest.approx(ey, abs=1e-3) == -0.5

    # Scenario 2: Drone rotated 90 degrees left (yaw = +pi/2)
    odom_sup.current_yaw = math.pi / 2.0
    ex_rot, ey_rot, dist_rot = odom_sup.get_body_frame_launch_error()
    assert pytest.approx(dist_rot, abs=1e-3) == dist
    # In FLU with yaw = +pi/2 (facing +y world):
    # World dx = -2.0 (target is at -x world), World dy = -0.5 (target is at -y world)
    # ex_body = cos(pi/2)*(-2) + sin(pi/2)*(-0.5) = -0.5 (behind)
    # ey_body = -sin(pi/2)*(-2) + cos(pi/2)*(-0.5) = +2.0 (left)
    assert pytest.approx(ex_rot, abs=1e-3) == -0.5
    assert pytest.approx(ey_rot, abs=1e-3) == 2.0
