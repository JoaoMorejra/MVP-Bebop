"""Altitude envelope of a launch document per launch surface (D1)."""

from __future__ import annotations

import types

import pytest

from mvp_mission_bebop.launch_envelope import (
    ALTITUDE_MAX_M,
    ALTITUDE_MIN_M,
    ALTITUDE_STEP_M,
    PAD_CLEARANCE_M,
    VENUE_HEADROOM_M,
    LaunchEnvelopeError,
    altitude_envelope,
    launches_from_ground,
    pad_clearance_m,
    validate_launch_envelope,
)
from mvp_mission_bebop.parameters import MissionParameters


def params_for(altitude: float, pad: float = 1.0, venue=2.5) -> MissionParameters:
    params = MissionParameters()
    params.kinematics.target_altitude_m = altitude
    params.platform.height_m = pad
    params.platform.venue_ceiling_agl_m = venue
    return params


def test_the_constants_are_the_ones_the_station_mirrors():
    assert (ALTITUDE_MIN_M, ALTITUDE_MAX_M, ALTITUDE_STEP_M) == (0.10, 2.00, 0.05)
    assert (PAD_CLEARANCE_M, VENUE_HEADROOM_M) == (0.30, 0.10)


def test_the_pad_clearance_is_the_factory_settle_floor():
    assert PAD_CLEARANCE_M == MissionParameters.factory().kinematics.takeoff_settle_min_altitude_m


def test_the_pad_top_raises_the_minimum_to_1_30_m_with_the_defaults():
    assert altitude_envelope(params_for(1.8)) == pytest.approx((1.30, 2.00))


def test_the_floor_keeps_the_absolute_minimum():
    assert altitude_envelope(params_for(0.5, pad=0.0)) == pytest.approx((0.10, 2.00))


def test_a_raised_settle_floor_raises_the_clearance():
    params = params_for(1.8)
    params.kinematics.takeoff_settle_min_altitude_m = 0.45
    assert pad_clearance_m(params) == pytest.approx(0.45)
    assert altitude_envelope(params)[0] == pytest.approx(1.45)


@pytest.mark.parametrize("venue, maximum", [(None, 2.0), (float("nan"), 2.0), (2.5, 2.0), (1.9, 1.8)])
def test_the_venue_ceiling_caps_the_maximum(venue, maximum):
    assert altitude_envelope(params_for(1.5, venue=venue))[1] == pytest.approx(maximum)


def test_a_zero_pad_is_a_launch_from_the_floor():
    assert launches_from_ground(params_for(1.0, pad=0.0))
    assert not launches_from_ground(params_for(1.8, pad=1.0))


@pytest.mark.parametrize("altitude", [1.3, 1.8, 2.0])
def test_the_pad_top_accepts_the_envelope_inclusive(altitude):
    validate_launch_envelope(params_for(altitude))


@pytest.mark.parametrize("altitude", [0.1, 1.0, 1.25, 2.05])
def test_the_pad_top_refuses_outside_the_envelope_and_names_the_field(altitude):
    with pytest.raises(LaunchEnvelopeError) as raised:
        validate_launch_envelope(params_for(altitude))
    assert raised.value.field == "kinematics.target_altitude_m"
    assert isinstance(raised.value, ValueError)


def test_the_low_bound_reason_cites_the_pad_minimum():
    with pytest.raises(LaunchEnvelopeError, match="1.30 m sobre o totem"):
        validate_launch_envelope(params_for(1.0))


@pytest.mark.parametrize("altitude", [float("nan"), float("inf"), "1.8", True, None])
def test_a_target_that_is_not_a_finite_number_is_refused(altitude):
    with pytest.raises(LaunchEnvelopeError) as raised:
        validate_launch_envelope(params_for(altitude))
    assert raised.value.field == "kinematics.target_altitude_m"


@pytest.mark.parametrize("pad", [-0.1, float("nan"), "1.0"])
def test_a_malformed_pad_height_is_refused(pad):
    with pytest.raises(LaunchEnvelopeError) as raised:
        validate_launch_envelope(params_for(1.8, pad=pad))
    assert raised.value.field == "platform.height_m"


@pytest.mark.parametrize(
    "section, name, path",
    [("rtl", "enabled", "rtl.enabled"), ("platform", "takeoff_from_ground", "platform.takeoff_from_ground")],
)
@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_the_boolean_fields_accept_only_bool(section, name, path, value):
    params = params_for(1.8)
    setattr(getattr(params, section), name, value)
    with pytest.raises(LaunchEnvelopeError) as raised:
        validate_launch_envelope(params)
    assert raised.value.field == path


def test_the_validation_reads_the_document_and_never_writes_it():
    params = params_for(1.8)
    before = params.to_dict()
    validate_launch_envelope(params)
    assert params.to_dict() == before


def test_the_module_reads_parameters_by_attribute():
    params = types.SimpleNamespace(
        kinematics=types.SimpleNamespace(target_altitude_m=0.5),
        platform=types.SimpleNamespace(height_m=0.0),
        rtl=types.SimpleNamespace(),
    )
    validate_launch_envelope(params)
    assert altitude_envelope(params) == pytest.approx((0.10, 2.00))
