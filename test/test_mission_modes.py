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
from mvp_mission_bebop.parameters import MissionParameters, ParameterTypeError, migrate_legacy_mission_mode
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
        (" ADVANCED ", MissionMode.ADVANCED),
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
    params.mission_mode = " Simple "
    assert apply_mission_mode(params, None) is MissionMode.SIMPLE
    assert params.mission_mode == "simple"


def test_only_the_two_profiles_remain():
    assert [mode.value for mode in MissionMode] == ["simple", "advanced"]


@pytest.mark.parametrize("raw", ["intermediate", " Intermediate "])
def test_the_removed_profile_is_refused_with_the_migration_hint(raw):
    with pytest.raises(ValueError) as refused:
        parse_mission_mode(raw)
    assert str(refused.value) == (
        "mission mode 'intermediate' was removed; stored documents migrate to 'advanced' with rtl.enabled=false"
    )


# ------------------------------------------------------- legacy migration (D5)


def test_the_migration_turns_intermediate_into_advanced_without_the_return():
    raw = {"mission_mode": " Intermediate ", "rtl": {"tag_size": 0.2}}
    migrated = migrate_legacy_mission_mode(raw)
    assert migrated == {"mission_mode": "advanced", "rtl": {"tag_size": 0.2, "enabled": False}}
    assert raw == {"mission_mode": " Intermediate ", "rtl": {"tag_size": 0.2}}


def test_the_migration_keeps_an_explicit_return_plan_and_is_idempotent():
    migrated = migrate_legacy_mission_mode({"mission_mode": "intermediate", "rtl": {"enabled": True}})
    assert migrated["rtl"]["enabled"] is True
    assert migrate_legacy_mission_mode(migrated) is migrated


@pytest.mark.parametrize("raw", [{}, {"mission_mode": "simple"}, {"mission_mode": "advanced", "rtl": {"enabled": False}}])
def test_the_migration_leaves_current_documents_alone(raw):
    assert migrate_legacy_mission_mode(raw) is raw


def test_the_migration_applies_through_update_from_dict():
    params = MissionParameters()
    params.update_from_dict({"mission_mode": "intermediate"})
    assert params.mission_mode == "advanced"
    assert params.rtl.enabled is False


def test_the_migration_applies_when_the_store_is_loaded(tmp_path):
    store = tmp_path / "mission_config.json"
    store.write_text(json.dumps({"mission_mode": "intermediate", "kinematics": {"target_altitude_m": 1.6}}))
    params = MissionParameters.load_from_file(str(store))
    assert (params.mission_mode, params.rtl.enabled) == ("advanced", False)
    assert params.kinematics.target_altitude_m == 1.6


def test_the_migration_applies_to_a_standby_go(monkeypatch, tmp_path):
    params, _ = _resolve(monkeypatch, tmp_path, ["--no-fly"])
    params.update_from_dict({"mission_mode": "intermediate"})
    assert apply_mission_mode(params, None) is MissionMode.ADVANCED
    assert params.rtl.enabled is False


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_the_return_plan_accepts_only_a_boolean(value):
    params = MissionParameters()
    with pytest.raises(ParameterTypeError) as refused:
        params.update_from_dict({"rtl": {"enabled": value}, "kinematics": {"target_altitude_m": 1.7}})
    assert refused.value.field == "rtl.enabled"
    assert params.rtl.enabled is True
    assert params.kinematics.target_altitude_m == MissionParameters().kinematics.target_altitude_m


def test_a_store_with_a_non_boolean_return_plan_is_refused_not_defaulted(tmp_path):
    store = tmp_path / "mission_config.json"
    store.write_text(json.dumps({"rtl": {"enabled": "false"}}))
    with pytest.raises(ParameterTypeError):
        MissionParameters.load_from_file(str(store))


def test_a_cold_start_with_a_string_return_plan_exits_78(monkeypatch, tmp_path, capsys):
    from mvp_mission_bebop.engine.exit_codes import EXIT_LAUNCH_REFUSED

    with pytest.raises(SystemExit) as refused:
        _resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", json.dumps({"rtl": {"enabled": "false"}})])
    assert refused.value.code == EXIT_LAUNCH_REFUSED
    assert "[LAUNCH_REFUSED] rtl.enabled: " in capsys.readouterr().out


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


@pytest.mark.parametrize(
    "mode, rtl_enabled, name",
    [
        (MissionMode.ADVANCED, True, "STEP 5: ArUco RTL & Precision Landing"),
        (MissionMode.SIMPLE, True, "STEP 5: ArUco Centering & Base Landing"),
        (MissionMode.ADVANCED, False, "STEP 5: Landing at Inspection Point"),
        (MissionMode.SIMPLE, False, "STEP 5: Landing at Inspection Point"),
    ],
)
def test_the_stage_five_name_follows_the_profile_and_the_return_plan(mode, rtl_enabled, name):
    assert build_steps(mode, rtl_enabled)[5]().name == name


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
    monkeypatch, tmp_path, capsys
):
    from mvp_mission_bebop.engine.exit_codes import EXIT_LAUNCH_REFUSED

    document = json.dumps({"mission_mode": "expert"})
    with pytest.raises(SystemExit) as refused:
        _resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", document])
    assert refused.value.code == EXIT_LAUNCH_REFUSED
    assert "[LAUNCH_REFUSED] mission_mode: " in capsys.readouterr().out
    assert not (tmp_path / "mission_config.json").exists()


def test_a_non_canonical_mode_in_the_launch_document_is_flown_normalised(monkeypatch, tmp_path):
    document = json.dumps({"mission_mode": " Simple "})
    params, _config = _resolve(monkeypatch, tmp_path, ["--no-fly", "--params-json", document])
    assert params.mission_mode == "simple"


def test_the_mode_flag_offers_exactly_the_two_profiles(monkeypatch, tmp_path, capsys):
    import sys

    from mvp_mission_bebop import mission

    monkeypatch.setattr(sys, "argv", ["mission.py", "--help"])
    with pytest.raises(SystemExit):
        mission.parse_arguments(MissionParameters())
    assert "--mode {simple,advanced}" in capsys.readouterr().out

    for mode in ("simple", "advanced"):
        monkeypatch.setattr(sys, "argv", ["mission.py", "--mode", mode])
        assert mission.parse_arguments(MissionParameters()).mode == mode
    for refused in ("expert", "Simple", "intermediate"):
        monkeypatch.setattr(sys, "argv", ["mission.py", "--mode", refused])
        with pytest.raises(SystemExit):
            mission.parse_arguments(MissionParameters())
