"""Backend parity of the station's parameter-conflict engine."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
from typing import Any, Dict, List

import pytest

from mvp_mission_bebop.config_audit import CONFLICT_TAG, log_envelope_divergences
from mvp_mission_bebop.parameter_conflicts import FIRMWARE_HOVER_HEIGHT_M, evaluate_parameter_conflicts
from mvp_mission_bebop.parameters import MissionParameters

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> Any:
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


SHARED = _load("parameter_conflict_cases.json")


def _apply(doc: Dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = doc
    for key in parents:
        node = node[key]
    if value is None:
        node.pop(leaf, None)
    else:
        node[leaf] = value


def case_document(overrides: Dict[str, Any]) -> Dict[str, Any]:
    doc = copy.deepcopy(_load("mission_factory_document.json"))
    for path, value in {**SHARED["base_overrides"], **overrides}.items():
        _apply(doc, path, value)
    return doc


@pytest.mark.parametrize("entry", SHARED["cases"], ids=[entry["name"] for entry in SHARED["cases"]])
def test_the_shared_cases_match_the_station(entry):
    found = evaluate_parameter_conflicts(case_document(entry["overrides"]))
    assert {
        "errors": [c.id for c in found if c.severity == "error"],
        "warnings": [c.id for c in found if c.severity == "warning"],
    } == entry["expect"]


def test_the_raw_dataclass_defaults_report_the_uncoupled_return():
    found = evaluate_parameter_conflicts(MissionParameters.factory().to_dict())
    assert [c.id for c in found] == ["E05"]


_RETIRED = {"W04", "W05", "W06"}
_SHIPPED_CONFIG = os.path.join(os.path.dirname(__file__), "..", "mvp_mission_bebop", "mission_config.json")


def _ids(doc: Dict[str, Any]) -> List[str]:
    return [c.id for c in evaluate_parameter_conflicts(doc)]


def test_the_retired_approach_rules_are_silent_for_the_shipped_configuration():
    with open(_SHIPPED_CONFIG, encoding="utf-8") as handle:
        shipped = json.load(handle)
    ids = _ids(shipped)
    assert not _RETIRED & set(ids)
    assert "W08" not in ids


@pytest.mark.parametrize("search", [0.0, -10.0, -30.0, -60.0])
@pytest.mark.parametrize("nadir", [-5.0, -25.0, -31.0, -90.0])
@pytest.mark.parametrize("approach_speed", [0.0, 0.02, 0.3])
def test_the_retired_approach_rules_never_fire(search, nadir, approach_speed):
    doc = case_document(
        {
            "gimbal.search_tilt_deg": search,
            "gimbal.nadir_tilt_deg": nadir,
            "kinematics.max_approach_forward_speed": approach_speed,
            "kinematics.target_altitude_m": 0.1,
        }
    )
    assert not _RETIRED & set(_ids(doc))


def test_w08_needs_only_the_lateral_cruise_of_the_search_timeout():
    doc = case_document({"kinematics.forward_cruise_velocity": 0.2, "timeouts.search_timeout_sec": 30.0})
    vf, vr, search = 0.2, abs(doc["rtl"]["reverse_cruise_velocity"]), 30.0
    needed = (vf * search) / vr
    doc["rtl"]["timeout_sec"] = needed + 0.5
    assert "W08" not in _ids(doc)
    doc["rtl"]["timeout_sec"] = needed - 0.5
    found = {c.id: c for c in evaluate_parameter_conflicts(doc)}
    assert "W08" in found
    assert f"{needed:.1f} s" in found["W08"].message
    assert found["W08"].fix.endswith("30 s.")


def test_w08_fix_does_not_round_float_noise_up_a_whole_second():
    doc = case_document(
        {
            "kinematics.forward_cruise_velocity": 0.2,
            "timeouts.search_timeout_sec": 42.0,
            "rtl.reverse_cruise_velocity": -0.3,
            "rtl.timeout_sec": 20.0,
        }
    )
    found = {c.id: c for c in evaluate_parameter_conflicts(doc)}
    assert found["W08"].fix == "Timeout de pelo menos 28 s."


def test_w08_ignores_the_search_tilt_geometry():
    doc = case_document({"gimbal.search_tilt_deg": 0.0, "rtl.timeout_sec": 60.0})
    assert "W08" not in _ids(doc)


def test_a_missing_document_reports_nothing():
    assert evaluate_parameter_conflicts({}) == []
    assert FIRMWARE_HOVER_HEIGHT_M == 1.0


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_the_audit_logs_each_conflict_as_one_json_line_and_never_raises():
    handler = Records()
    target = logging.getLogger("ConfigAudit")
    target.addHandler(handler)
    try:
        params = MissionParameters()
        params.kinematics.target_altitude_m = 0.1
        params.kinematics.altitude_ceiling_margin_m = 0.0
        log_envelope_divergences(params, lambda _d: 20)
    finally:
        target.removeHandler(handler)
    lines = [line for line in handler.lines if line.startswith(f"[{CONFLICT_TAG}] ")]
    records = [json.loads(line[len(CONFLICT_TAG) + 3 :]) for line in lines]
    assert [r["id"] for r in records if r["severity"] == "error"] == ["E03", "E04", "E05"]
    assert all({"id", "severity", "paths", "title", "message", "fix"} <= set(r) for r in records)


def test_the_shipped_defaults_stay_inside_every_envelope_despite_the_conflicts():
    from mvp_mission_bebop.config_audit import audit_safe_envelopes

    assert audit_safe_envelopes(MissionParameters(), lambda _d: 20) == []
