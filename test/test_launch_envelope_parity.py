"""The station and the mission accept the same altitudes (BUG 1.1).

Reads ``test/fixtures/altitude_envelope_cases.json``, the file
``bebop_mission_control/src/lib/altitudeEnvelope.test.ts`` reads too, and runs
each document through the mission's own resolution: ``update_from_dict``,
``apply_mission_mode``, ``validate_launch_envelope`` and, when accepted,
``relative_target_altitude_m``.
"""

from __future__ import annotations

import json
import os

import pytest

from mvp_mission_bebop.estimation.ceiling_schedule import CeilingSchedule
from mvp_mission_bebop.launch_envelope import LaunchEnvelopeError, altitude_envelope, validate_launch_envelope
from mvp_mission_bebop.mission_mode import apply_launch_surface, apply_mission_mode
from mvp_mission_bebop.parameters import MissionParameters, ParameterTypeError

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "altitude_envelope_cases.json")

with open(_FIXTURE, encoding="utf-8") as _handle:
    CASES = json.load(_handle)["cases"]


def resolved(document) -> MissionParameters:
    params = MissionParameters()
    params.update_from_dict(document)
    apply_mission_mode(params, None)
    return params


def refused_field(document):
    """The field the mission refuses ``document`` on, or ``None``: the path of ``mission.launch_refused``."""
    try:
        validate_launch_envelope(resolved(document))
    except (LaunchEnvelopeError, ParameterTypeError) as error:
        return error.field
    return None


def test_the_fixture_covers_the_grid():
    names = {case["name"] for case in CASES}
    assert len(names) == len(CASES)
    for mode in ("simple", "advanced"):
        for surface in ("platform", "ground"):
            for rtl in ("true", "false"):
                for altitude in ("0.05", "0.1", "0.35", "1", "1.25", "1.3", "1.8", "2", "2.05"):
                    assert f"{mode}/{surface}/rtl={rtl}/{altitude}" in names


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_the_mission_refuses_exactly_the_cases_the_fixture_refuses(case):
    assert refused_field(case["document"]) == case["expected"]["field"]


@pytest.mark.parametrize("case", [c for c in CASES if c["expected"]["field"] in (None, "kinematics.target_altitude_m")], ids=lambda c: c["name"])
def test_the_mission_computes_the_fixture_envelope(case):
    expected = case["expected"]
    assert altitude_envelope(resolved(case["document"])) == pytest.approx((expected["min"], expected["max"]))


@pytest.mark.parametrize("case", [c for c in CASES if c["expected"]["field"] in (None, "kinematics.target_altitude_m")], ids=lambda c: c["name"])
def test_the_mission_computes_the_fixture_ceiling_over_the_floor(case):
    params = resolved(case["document"])
    if case["expected"]["accepted"]:
        apply_launch_surface(params)
    schedule = CeilingSchedule(params.kinematics, params.platform)
    pad = schedule.pad_height_m
    assert min(schedule.nominal_m(), schedule.venue_limit_m) + pad == pytest.approx(case["expected"]["ceiling_floor_m"])


@pytest.mark.parametrize("case", [c for c in CASES if c["expected"]["accepted"]], ids=lambda c: c["name"])
def test_an_accepted_document_flies_the_fixture_target_and_never_raises_in_flight(case):
    params = resolved(case["document"])
    validate_launch_envelope(params)
    assert params.relative_target_altitude_m == pytest.approx(case["expected"]["relative_target_m"])
    apply_launch_surface(params)
    assert params.relative_target_altitude_m == pytest.approx(case["expected"]["relative_target_m"])
