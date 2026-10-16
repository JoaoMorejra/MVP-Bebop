"""Mission profile selection: parsing, precedence and the factory document."""

from __future__ import annotations

import json
import os
import re

import pytest

from mvp_mission_bebop.mission_mode import (
    DEFAULT_MODE,
    MissionMode,
    apply_mission_mode,
    parse_mission_mode,
)
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.pipeline import build_steps

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mission_factory_document.json")


def test_the_default_profile_is_the_full_mission():
    assert DEFAULT_MODE is MissionMode.ADVANCED
    assert MissionParameters().mission_mode == "advanced"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_or_blank_value_is_the_default(raw):
    assert parse_mission_mode(raw) is MissionMode.ADVANCED


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("simple", MissionMode.SIMPLE),
        ("Simple", MissionMode.SIMPLE),
        (" INTERMEDIATE ", MissionMode.INTERMEDIATE),
        ("advanced", MissionMode.ADVANCED),
        (MissionMode.SIMPLE, MissionMode.SIMPLE),
    ],
)
def test_parsing_is_case_and_whitespace_insensitive(raw, expected):
    assert parse_mission_mode(raw) is expected


def test_an_unknown_name_is_refused_not_defaulted():
    with pytest.raises(ValueError, match="expert"):
        parse_mission_mode("expert")


@pytest.mark.parametrize("raw", [3, 1.5, ["simple"], True])
def test_a_non_string_is_a_type_error(raw):
    with pytest.raises(TypeError):
        parse_mission_mode(raw)


def test_the_cli_flag_wins_over_the_stored_value():
    params = MissionParameters()
    params.mission_mode = "advanced"
    assert apply_mission_mode(params, "simple") is MissionMode.SIMPLE
    assert params.mission_mode == "simple"


def test_the_stored_value_is_normalised_in_place():
    params = MissionParameters()
    params.mission_mode = " Intermediate "
    assert apply_mission_mode(params, None) is MissionMode.INTERMEDIATE
    assert params.mission_mode == "intermediate"


def test_an_invalid_stored_value_raises_instead_of_flying_the_default():
    params = MissionParameters()
    params.mission_mode = "expert"
    with pytest.raises(ValueError):
        apply_mission_mode(params, None)


def test_the_launch_document_carries_the_mode_through_update_from_dict():
    params = MissionParameters()
    params.update_from_dict({"mission_mode": "simple", "yaw_pattern": {"scan_vyaw": 0.2}})
    assert params.mission_mode == "simple"
    assert params.yaw_pattern.scan_vyaw == 0.2


def test_the_factory_document_fixture_lists_the_new_fields():
    with open(_FIXTURE, encoding="utf-8") as handle:
        document = json.load(handle)
    assert document["mission_mode"] == "advanced"
    assert "yaw_pattern" in document
    assert document == json.loads(json.dumps(MissionParameters.factory().to_dict()))


@pytest.mark.parametrize("mode", list(MissionMode))
def test_every_profile_builds_five_steps_with_the_stage_marker_prefix(mode):
    factories = build_steps(mode)
    assert list(factories) == [1, 2, 3, 4, 5]
    for number, factory in factories.items():
        step = factory()
        assert re.match(rf"STEP {number}: ", step.name), step.name


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_steps_that_differ_by_profile_carry_it(mode):
    steps = {number: factory() for number, factory in build_steps(mode).items()}
    for number in (2, 3, 4, 5):
        assert steps[number].mode is mode


def test_the_advanced_names_are_the_historical_ones():
    steps = {number: factory() for number, factory in build_steps(MissionMode.ADVANCED).items()}
    assert steps[2].name == "STEP 2: Lateral Left Search"
    assert steps[3].name == "STEP 3: Visual Servoing & Coupled Guidance"
    assert steps[4].name == "STEP 4: Motionless Nadir Hover & Evidence Capture"
    assert steps[5].name == "STEP 5: ArUco RTL & Precision Landing"


def test_a_step_built_without_a_mode_is_the_advanced_one():
    from mvp_mission_bebop.steps import ClosedLoopRTLStep, LateralSearchStep, NadirInspectionStep, VisualServoingStep

    for cls in (LateralSearchStep, VisualServoingStep, NadirInspectionStep, ClosedLoopRTLStep):
        assert cls().mode is MissionMode.ADVANCED


def test_the_blackboard_starts_without_a_takeoff_heading():
    from mvp_mission_bebop.blackboard import MissionBlackboard

    assert MissionBlackboard().initial_yaw_rad is None


def _resolve(monkeypatch, tmp_path, argv):
    import sys

    from mvp_mission_bebop import mission

    config = tmp_path / "mission_config.json"
    monkeypatch.setattr(sys, "argv", ["mission.py", "--config", str(config), *argv])
    args = mission.parse_arguments(MissionParameters())
    return mission.resolve_parameters(args), config


def test_an_unknown_mode_in_the_launch_document_stops_the_mission_before_anything_is_persisted(
    monkeypatch, tmp_path
):
    document = json.dumps({"mission_mode": "expert"})
    with pytest.raises(SystemExit) as refused:
        _resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", document])
    assert str(refused.value.code).startswith("mission_mode:"), refused.value.code
    assert not (tmp_path / "mission_config.json").exists()


def test_a_non_canonical_mode_in_the_launch_document_is_flown_normalised(monkeypatch, tmp_path):
    document = json.dumps({"mission_mode": " Simple "})
    params, _config = _resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", document])
    assert params.mission_mode == "simple"


def test_the_mode_flag_offers_exactly_the_three_profiles(monkeypatch, tmp_path, capsys):
    import sys

    from mvp_mission_bebop import mission

    monkeypatch.setattr(sys, "argv", ["mission.py", "--help"])
    with pytest.raises(SystemExit):
        mission.parse_arguments(MissionParameters())
    assert "--mode {simple,intermediate,advanced}" in capsys.readouterr().out

    for mode in ("simple", "intermediate", "advanced"):
        monkeypatch.setattr(sys, "argv", ["mission.py", "--mode", mode])
        assert mission.parse_arguments(MissionParameters()).mode == mode
    for refused in ("expert", "Simple"):
        monkeypatch.setattr(sys, "argv", ["mission.py", "--mode", refused])
        with pytest.raises(SystemExit):
            mission.parse_arguments(MissionParameters())
