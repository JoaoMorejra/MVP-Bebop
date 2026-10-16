"""The pad-edge guard measures the outbound track along the body left axis."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.estimation.platform_monitor import PlatformState, outbound_track_m
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

TARGET = 0.8
EDGE_M = 0.25
PAD_HEIGHT_M = 1.0


def test_a_displacement_to_the_left_is_positive():
    assert outbound_track_m(0.0, -0.5, 0.0) == pytest.approx(0.5)


def test_a_forward_displacement_does_not_count():
    assert outbound_track_m(-0.5, 0.0, 0.0) == pytest.approx(0.0)


def test_a_displacement_to_the_right_is_negative():
    assert outbound_track_m(0.0, 0.3, 0.0) == pytest.approx(-0.3)


def test_the_projection_follows_the_heading():
    # At yaw = pi/2 the body left axis points along world -x.
    assert outbound_track_m(0.5, 0.0, math.pi / 2.0) == pytest.approx(0.5)
    assert outbound_track_m(-0.5, 0.0, math.pi / 2.0) == pytest.approx(-0.5)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("slot", [0, 1, 2])
def test_non_finite_arguments_are_rejected(bad, slot):
    args = [0.0, 0.0, 0.0]
    args[slot] = bad
    with pytest.raises(ValueError):
        outbound_track_m(*args)


def _supervisor() -> OdometrySupervisor:
    params = MissionParameters()
    params.kinematics.target_altitude_m = TARGET
    params.platform.height_m = PAD_HEIGHT_M
    params.platform.edge_ahead_m = EDGE_M
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    for _ in range(params.calibration.min_ground_samples):
        odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.0)
    assert odom.calibrate_ground_reference()
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=TARGET)
    odom.freeze_hover_takeoff_origin()
    return odom


def _s_star() -> float:
    half = math.radians(MissionParameters().platform.sonar_half_angle_deg)
    return EDGE_M + TARGET * math.tan(half)


def _guard_exit() -> float:
    cfg = MissionParameters().platform
    return _s_star() + cfg.edge_uncertainty_m + cfg.guard_extent_m


def test_walking_left_crosses_the_pad_edge():
    odom = _supervisor()
    seen = []
    steps = int((_guard_exit() + 0.3) / 0.01)
    for k in range(steps):
        odom.inject_synthetic_sample(x=0.0, y=0.01 * k, z=TARGET)
        state = odom.platform_report().state
        if not seen or seen[-1] is not state:
            seen.append(state)
    assert seen == [PlatformState.ON, PlatformState.LEAVING, PlatformState.OFF]
    assert odom.snapshot().along_track_m > 0.0


def test_walking_forward_stays_over_the_pad():
    odom = _supervisor()
    for k in range(int((_guard_exit() + 0.3) / 0.01)):
        odom.inject_synthetic_sample(x=0.01 * k, y=0.0, z=TARGET)
        assert odom.platform_report().state is PlatformState.ON
