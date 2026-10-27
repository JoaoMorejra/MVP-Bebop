"""Launch surface and pad resolution of a flight (sections 3.1.2 and 3.3)."""

from __future__ import annotations

import pytest

from mvp_mission_bebop.actuators.simulator import FIRMWARE_HOVER_ALTITUDE_M
from mvp_mission_bebop.launch_envelope import altitude_envelope, launches_from_ground, validate_launch_envelope
from mvp_mission_bebop import mission_mode as mission_mode_module
from mvp_mission_bebop.mission_mode import (
    NATIVE_HOVER_HEIGHT_M,
    MissionMode,
    apply_launch_surface,
    apply_mission_mode,
)
from mvp_mission_bebop.parameter_conflicts import FIRMWARE_HOVER_HEIGHT_M
from mvp_mission_bebop.parameters import MissionParameters, ParameterTypeError


def test_the_native_height_agrees_with_the_simulator_and_the_conflict_rules():
    assert NATIVE_HOVER_HEIGHT_M == FIRMWARE_HOVER_ALTITUDE_M == FIRMWARE_HOVER_HEIGHT_M


def surface_lines(monkeypatch):
    lines = []
    monkeypatch.setattr(mission_mode_module.logger, "info", lambda fmt, *args: lines.append(fmt % args))
    return lines


def flight(mode: str, target: float, *, pad: float = 1.0, ground: bool = False) -> MissionParameters:
    params = MissionParameters()
    params.mission_mode = mode
    params.kinematics.target_altitude_m = target
    params.platform.height_m = pad
    params.platform.takeoff_from_ground = ground
    params.platform.venue_ceiling_agl_m = 2.5
    params.platform.edge_ahead_m = 0.3
    apply_mission_mode(params, None)
    validate_launch_envelope(params)
    return params


def test_the_factory_takes_off_from_the_pad_top_flag_and_the_pad_of_zero_is_the_floor():
    params = MissionParameters()
    assert params.platform.takeoff_from_ground is False
    assert launches_from_ground(params)


@pytest.mark.parametrize("mode", list(MissionMode))
def test_bug_1_1_the_station_document_is_accepted_and_flies_0_8_m_above_the_pad(mode):
    params = flight(mode.value, 1.8)
    assert params.relative_target_altitude_m == pytest.approx(0.8)


@pytest.mark.parametrize("mode", list(MissionMode))
def test_from_the_ground_the_pad_takes_no_part_in_the_flight(mode, monkeypatch):
    params = flight(mode.value, 0.5, ground=True)
    assert params.relative_target_altitude_m == pytest.approx(0.5)
    lines = surface_lines(monkeypatch)
    apply_launch_surface(params)
    assert params.platform.height_m == 0.0
    assert params.platform.edge_ahead_m is None
    assert params.launch_pad_height_m_original == 1.0
    assert params.relative_target_altitude_m == pytest.approx(0.5)
    assert lines == ["[SURFACE] takeoff=ground pad=0.00 target_floor=0.50 target_rel=0.50"]


def test_the_simple_profile_on_the_pad_keeps_the_pad_and_drops_the_edge_guard(monkeypatch):
    params = flight("simple", 1.8)
    lines = surface_lines(monkeypatch)
    apply_launch_surface(params)
    assert params.platform.height_m == 1.0
    assert params.platform.edge_ahead_m is None
    assert params.relative_target_altitude_m == pytest.approx(0.8)
    assert lines == ["[SURFACE] takeoff=platform pad=1.00 target_floor=1.80 target_rel=0.80"]


def test_the_advanced_profile_on_the_pad_keeps_the_edge_guard():
    params = flight("advanced", 1.8)
    apply_launch_surface(params)
    assert params.platform.height_m == 1.0
    assert params.platform.edge_ahead_m == 0.3


def test_the_surface_exists_only_in_memory_after_the_persisted_snapshot():
    params = flight("simple", 1.8, ground=True)
    keys = set(params.to_dict())
    snapshot = params.to_dict()
    platform, kinematics = params.platform, params.kinematics
    apply_launch_surface(params)
    assert snapshot["platform"]["height_m"] == 1.0
    assert snapshot["platform"]["takeoff_from_ground"] is True
    assert set(params.to_dict()) == keys
    assert "launch_pad_height_m_original" not in params.to_dict()
    assert params.platform is platform and params.kinematics is kinematics


def test_the_ground_flag_selects_the_floor_envelope_with_the_physical_pad_kept():
    params = flight("advanced", 0.5, ground=True)
    assert altitude_envelope(params) == pytest.approx((0.10, 2.40))
    params.platform.takeoff_from_ground = False
    assert altitude_envelope(params) == pytest.approx((1.30, 2.40))


@pytest.mark.parametrize("value", ["true", 1, 0, None])
def test_the_ground_flag_accepts_only_a_boolean(value):
    params = MissionParameters()
    with pytest.raises(ParameterTypeError) as refused:
        params.update_from_dict({"platform": {"takeoff_from_ground": value}})
    assert refused.value.field == "platform.takeoff_from_ground"


@pytest.mark.parametrize("mode", ["simple", "advanced"])
def test_the_profile_resolution_never_touches_the_altitude_or_the_margin(mode):
    params = MissionParameters()
    params.mission_mode = mode
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.25
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == 1.8
    assert params.kinematics.altitude_ceiling_margin_m == 0.25
