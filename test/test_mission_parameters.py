"""The ``mission.parameters`` milestone: the single source of spoken numbers (D2).

Every number the copilot speaks that corresponds to a mission parameter is the
value this mission launched with, after the file -> ``--params-json`` merge,
never telemetry and never a frontend default.
"""

from __future__ import annotations

import pytest

from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry.mission_parameters import (
    SPOKEN_PARAMETER_KEYS,
    spoken_parameters,
)

SPEC_KEYS = (
    "target_altitude_m",
    "cruise_mps",
    "rtl_mps",
    "search_timeout_sec",
    "hover_duration_sec",
    "confidence",
    "arrival_radius_m",
    "battery_land_pct",
    "countdown_sec",
)


def merged(document):
    params = MissionParameters()
    params.update_from_dict(document)
    return params


def test_the_payload_carries_exactly_the_specified_keys():
    params = MissionParameters()
    payload = spoken_parameters(params, SpeedCalibration.from_kinematics(params.kinematics))
    assert tuple(payload) == SPEC_KEYS == SPOKEN_PARAMETER_KEYS


def test_values_are_the_merged_document_the_steps_fly():
    params = merged(
        {
            "kinematics": {
                "target_altitude_m": 2.3,
                "forward_cruise_velocity": 0.35,
                "hover_duration_sec": 6.0,
                "countdown_sec": 4.0,
            },
            "rtl": {"max_speed": 0.12, "arrival_radius_m": 0.3},
            "timeouts": {"search_timeout_sec": 42.0},
            "vision": {"confidence_threshold": 0.6},
            "battery": {"land_pct": 25.0},
        }
    )
    calibration = SpeedCalibration.from_kinematics(params.kinematics)
    payload = spoken_parameters(params, calibration)
    assert payload["target_altitude_m"] == pytest.approx(2.3)
    assert payload["cruise_mps"] == pytest.approx(calibration.to_mps(0.35), abs=1e-3)
    assert payload["rtl_mps"] == pytest.approx(calibration.to_mps(0.12), abs=1e-3)
    assert payload["search_timeout_sec"] == pytest.approx(42.0)
    assert payload["hover_duration_sec"] == pytest.approx(6.0)
    assert payload["confidence"] == pytest.approx(0.6)
    assert payload["arrival_radius_m"] == pytest.approx(0.3)
    assert payload["battery_land_pct"] == pytest.approx(25.0)
    assert payload["countdown_sec"] == pytest.approx(4.0)


def test_every_value_is_a_finite_float():
    params = MissionParameters()
    payload = spoken_parameters(params, SpeedCalibration.from_kinematics(params.kinematics))
    assert all(isinstance(value, float) for value in payload.values())


def test_mission_parameters_is_a_milestone_key():
    from mvp_mission_bebop.telemetry.milestones import MILESTONE_KEYS

    assert "mission.parameters" in MILESTONE_KEYS
