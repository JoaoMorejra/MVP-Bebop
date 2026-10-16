"""Regression guard: ``RTLGuidanceController.compute`` publishes its own law.

The method used to call the along-track channel with the cross-track error and
vice versa, discard both results, and publish ``vx = -ey * 0.0469`` and
``vy = ex * 0.0469`` instead -- a bare proportional term with no braking
profile, no jerk limit and no quantization shaper. These tests rebuild the
expected command from the controller's own channel methods on a twin instance
and require the published command to match it cycle by cycle.

In the lateral flight pattern the return channel is body Y (``_compute_lateral``,
which also carries the overshoot note) and body X is the off-track trim
(``_compute_longitudinal``).
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from mvp_mission_bebop.controllers.rtl_guidance import (
    RTLGuidanceController,
    RTLPhase,
    STATION_KEEPING_SPEED_RATIO,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot

DT = 1.0 / 15.0


def _snapshot(x: float, y: float, yaw: float = 0.0) -> OdometrySnapshot:
    return OdometrySnapshot(
        x=x,
        y=y,
        raw_altitude=1.0,
        relative_altitude=1.0,
        vx=0.0,
        vy=0.0,
        vz=0.0,
        yaw=yaw,
        timestamp=0.0,
        sample_count=1,
        ground_reference=0.0,
        takeoff_x=0.0,
        takeoff_y=0.0,
    )


def _pair(normalized_to_mps: float = 1.0) -> Tuple[RTLGuidanceController, RTLGuidanceController]:
    rtl = ReturnToLaunchConfig()
    kinematics = FlightKinematicsConfig()
    calibration = SpeedCalibration(normalized_to_mps)
    return (
        RTLGuidanceController(rtl, kinematics, calibration),
        RTLGuidanceController(rtl, kinematics, calibration),
    )


def _expected(
    twin: RTLGuidanceController, snap: OdometrySnapshot, phase: RTLPhase
) -> Tuple[float, float, float]:
    """Command the corrected law must publish, built from the channel methods."""
    ex, ey, _ = snap.body_frame_launch_error()
    speed_cap = twin.cruise_speed_mps
    if phase is RTLPhase.STATION_KEEPING:
        speed_cap *= STATION_KEEPING_SPEED_RATIO
    lateral_mps, _ = twin._compute_lateral(ey, DT, speed_cap)
    longitudinal_mps = twin._compute_longitudinal(ex, DT, speed_cap)
    vx = twin._shaper_x.shape(twin.calibration.to_normalized(longitudinal_mps), DT)
    vy = twin._shaper_y.shape(twin.calibration.to_normalized(lateral_mps, axis="lateral"), DT)
    trim_cap = twin.rtl_cfg.max_lateral_speed
    vx = max(-trim_cap, min(trim_cap, vx))
    vy = max(-1.0, min(twin.rtl_cfg.overshoot_recovery_speed, vy))
    return vx, vy, lateral_mps


@pytest.mark.parametrize(
    "position",
    [(0.0, 2.0), (0.6, 2.0), (-0.8, 1.2), (0.3, 0.4), (0.1, -0.2)],
)
@pytest.mark.parametrize("phase", [RTLPhase.NAVIGATING, RTLPhase.STATION_KEEPING])
def test_published_command_is_the_shaped_output_of_the_channel_laws(position, phase):
    controller, twin = _pair()
    x, y = position
    for cycle in range(60):
        snap = _snapshot(x, y - 0.01 * cycle)
        command = controller.compute(snap, 0.0, DT, phase=phase)
        vx, vy, lateral_mps = _expected(twin, snap, phase)
        assert command.vx == pytest.approx(vx, abs=1e-12), f"cycle {cycle}"
        assert command.vy == pytest.approx(vy, abs=1e-12), f"cycle {cycle}"
        assert command.target_speed_mps == pytest.approx(lateral_mps, abs=1e-12)


def test_the_return_channel_drives_vy_and_the_off_track_channel_drives_vx():
    """A pure return-track error must not leak into ``vx``, nor off-track into ``vy``."""
    returning, _ = _pair()
    vy_history: List[float] = []
    for _ in range(45):
        command = returning.compute(_snapshot(0.0, 2.0), 0.0, DT)
        vy_history.append(command.vy)
        assert command.vx == 0.0
    assert min(vy_history) < 0.0, "a drone left of the origin must be commanded right"

    off_track, _ = _pair()
    vx_history: List[float] = []
    for _ in range(45):
        command = off_track.compute(_snapshot(0.8, 0.0), 0.0, DT)
        vx_history.append(command.vx)
        assert command.vy == 0.0
    assert min(vx_history) < 0.0, "a drone ahead of the origin must be trimmed aft"


def test_the_first_cycle_is_jerk_limited_rather_than_a_proportional_step():
    """The removed ``-ey * 0.0469`` term had no profile and stepped on cycle one."""
    controller, _ = _pair()
    command = controller.compute(_snapshot(0.0, 3.0), 0.0, DT)
    rtl = ReturnToLaunchConfig()
    assert abs(command.vy) <= rtl.max_jerk_mps3 * DT * DT + rtl.quantization_step


def test_the_calibration_gain_is_applied_at_the_output():
    """The law works in m/s; a non-unit gain must scale the normalized command."""
    controller, twin = _pair(normalized_to_mps=2.0)
    for cycle in range(40):
        snap = _snapshot(0.4, 2.5 - 0.02 * cycle)
        command = controller.compute(snap, 0.0, DT)
        vx, vy, _ = _expected(twin, snap, RTLPhase.NAVIGATING)
        assert command.vx == pytest.approx(vx, abs=1e-12)
        assert command.vy == pytest.approx(vy, abs=1e-12)
