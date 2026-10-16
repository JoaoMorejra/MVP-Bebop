"""Simple profile: the firmware's native takeoff, no corrective ascent, fixed ceiling margin."""

from __future__ import annotations

import types

import pytest

from mvp_mission_bebop.actuators.simulator import FIRMWARE_HOVER_ALTITUDE_M
from mvp_mission_bebop.blackboard import MissionBlackboard
from mvp_mission_bebop.mission_mode import (
    NATIVE_HOVER_HEIGHT_M,
    SIMPLE_CEILING_MARGIN_M,
    MissionMode,
    apply_mission_mode,
    neutralize_launch_pad,
)
from mvp_mission_bebop.parameter_conflicts import FIRMWARE_HOVER_HEIGHT_M
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import takeoff as takeoff_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.pipeline import build_steps
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


def test_the_native_height_agrees_with_the_simulator_and_the_conflict_rules():
    assert NATIVE_HOVER_HEIGHT_M == FIRMWARE_HOVER_ALTITUDE_M == FIRMWARE_HOVER_HEIGHT_M


def test_the_ceiling_margin_constant_is_the_factory_value():
    assert SIMPLE_CEILING_MARGIN_M == MissionParameters.factory().kinematics.altitude_ceiling_margin_m


# ------------------------------------------------------------- normalisation


def simple_params(**kinematics) -> MissionParameters:
    params = MissionParameters()
    params.mission_mode = "simple"
    for key, value in kinematics.items():
        setattr(params.kinematics, key, value)
    return params


def test_the_simple_profile_pins_the_ceiling_margin():
    params = simple_params(altitude_ceiling_margin_m=0.05, target_altitude_m=0.9)
    assert apply_mission_mode(params, None) is MissionMode.SIMPLE
    assert params.kinematics.altitude_ceiling_margin_m == SIMPLE_CEILING_MARGIN_M
    assert params.kinematics.target_altitude_m == 0.9


@pytest.mark.parametrize("pad", [0.0, 1.0])
@pytest.mark.parametrize("altitude", [0.1, 0.5, NATIVE_HOVER_HEIGHT_M])
def test_the_simple_profile_accepts_the_native_takeoff_range(altitude, pad):
    params = simple_params(target_altitude_m=altitude)
    params.platform.height_m = pad
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == altitude


@pytest.mark.parametrize("pad", [0.0, 1.0])
@pytest.mark.parametrize("altitude", [0.05, 1.05, 1.4, float("nan"), float("inf")])
def test_the_simple_profile_refuses_outside_the_native_takeoff_range(altitude, pad):
    params = simple_params(target_altitude_m=altitude)
    params.platform.height_m = pad
    with pytest.raises(ValueError, match="kinematics.target_altitude_m"):
        apply_mission_mode(params, None)


def test_the_pad_height_takes_no_part_in_the_check():
    params = simple_params(target_altitude_m=0.5)
    params.platform.height_m = float("nan")
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == 0.5


# ------------------------------------------------------------ neutralisation


def odometry_ceiling(params: MissionParameters) -> float:
    return OdometrySupervisor(
        params.kinematics, params.timeouts, params.calibration, params.platform
    ).altitude_ceiling


@pytest.mark.parametrize("target", [0.3, 0.7, 1.0])
def test_neutralisation_anchors_the_ceiling_on_the_native_height(target):
    params = simple_params(target_altitude_m=target)
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = 0.3
    apply_mission_mode(params, None)
    supervisor = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    neutralize_launch_pad(params)
    assert params.platform.height_m == 0.0
    assert params.platform.edge_ahead_m is None
    expected = NATIVE_HOVER_HEIGHT_M + SIMPLE_CEILING_MARGIN_M
    assert params.kinematics.target_altitude_m + params.kinematics.altitude_ceiling_margin_m == pytest.approx(expected)
    assert supervisor.altitude_ceiling == pytest.approx(expected)
    assert odometry_ceiling(params) == pytest.approx(expected)


def test_neutralisation_makes_the_relative_target_the_plain_target():
    params = simple_params(target_altitude_m=0.7)
    params.platform.height_m = 1.0
    with pytest.raises(ValueError):
        params.relative_target_altitude_m
    apply_mission_mode(params, None)
    neutralize_launch_pad(params)
    assert params.relative_target_altitude_m == 0.7


def test_neutralisation_keeps_the_dataclass_objects_and_records_the_original_pad():
    params = simple_params(target_altitude_m=0.7)
    params.platform.height_m = 1.0
    platform, kinematics = params.platform, params.kinematics
    apply_mission_mode(params, None)
    neutralize_launch_pad(params)
    assert params.platform is platform and params.kinematics is kinematics
    assert params.launch_pad_height_m_original == 1.0


def test_neutralisation_exists_only_in_memory_after_the_persisted_snapshot():
    params = simple_params(target_altitude_m=0.7)
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = 0.3
    keys = set(params.to_dict())
    apply_mission_mode(params, None)
    snapshot = params.to_dict()
    neutralize_launch_pad(params)
    assert snapshot["platform"]["height_m"] == 1.0
    assert snapshot["platform"]["edge_ahead_m"] == 0.3
    assert snapshot["kinematics"]["altitude_ceiling_margin_m"] == SIMPLE_CEILING_MARGIN_M
    assert set(params.to_dict()) == keys
    assert "launch_pad_height_m_original" not in params.to_dict()


@pytest.mark.parametrize("mode", ["intermediate", "advanced"])
def test_neutralisation_is_a_no_op_for_the_other_profiles(mode):
    params = MissionParameters()
    params.mission_mode = mode
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = 0.3
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.25
    before = params.to_dict()
    apply_mission_mode(params, None)
    neutralize_launch_pad(params)
    assert params.to_dict() == before | {"mission_mode": mode}
    assert not hasattr(params, "launch_pad_height_m_original")


@pytest.mark.parametrize("altitude", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_target_altitude_is_refused(altitude):
    params = simple_params(target_altitude_m=0.9)
    params.kinematics.target_altitude_m = altitude
    with pytest.raises(ValueError, match="kinematics.target_altitude_m"):
        apply_mission_mode(params, None)


@pytest.mark.parametrize("mode", ["intermediate", "advanced"])
def test_the_other_profiles_keep_their_altitude_and_margin(mode):
    params = MissionParameters()
    params.mission_mode = mode
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.25
    apply_mission_mode(params, None)
    assert params.kinematics.target_altitude_m == 1.8
    assert params.kinematics.altitude_ceiling_margin_m == 0.25


def test_the_cli_flag_selects_the_simple_normalisation_too():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.4
    with pytest.raises(ValueError):
        apply_mission_mode(params, "simple")


# ------------------------------------------------------------ takeoff routing


def flight_sequence(monkeypatch, mode: MissionMode):
    step = TakeoffStep(mode=mode)
    calls = []
    monkeypatch.setattr(step, "_confirm_takeoff", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_stabilize", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_ascend", lambda ctx: calls.append("ascend") or StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_finalize", lambda ctx: StepStatus.SUCCESS)
    ctx = types.SimpleNamespace(blackboard=MissionBlackboard())
    return step._flight_sequence(ctx), calls, ctx


def test_the_simple_profile_never_runs_the_corrective_ascent(monkeypatch):
    status, calls, ctx = flight_sequence(monkeypatch, MissionMode.SIMPLE)
    assert status is StepStatus.SUCCESS
    assert calls == []
    assert ctx.blackboard.takeoff_complete is True


@pytest.mark.parametrize("mode", [MissionMode.INTERMEDIATE, MissionMode.ADVANCED])
def test_the_other_profiles_still_run_the_ascent_stage(monkeypatch, mode):
    status, calls, _ctx = flight_sequence(monkeypatch, mode)
    assert status is StepStatus.SUCCESS
    assert calls == ["ascend"]


def test_the_takeoff_default_is_the_advanced_profile():
    assert TakeoffStep().mode is MissionMode.ADVANCED


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_pipeline_hands_the_profile_to_stage_one(mode):
    assert build_steps(mode)[1]().mode is mode


# ------------------------------------------------------------------ milestone


@pytest.mark.parametrize(
    "mode, native", [(MissionMode.SIMPLE, True), (MissionMode.INTERMEDIATE, False), (MissionMode.ADVANCED, False)]
)
def test_the_takeoff_milestone_says_whether_the_takeoff_is_native(monkeypatch, mode, native):
    emitted = []
    monkeypatch.setattr(takeoff_module, "emit_milestone", lambda key, payload=None: emitted.append((key, payload)))
    monkeypatch.setattr(takeoff_module, "takeoff_confirmed", lambda *args, **kwargs: True)
    ctx = types.SimpleNamespace(params=MissionParameters(), flying_state=2, blackboard=MissionBlackboard())
    step = TakeoffStep(mode=mode)
    step._takeoff_called = False

    step._call_takeoff_once_confirmed(ctx, 1.0)

    (key, payload), = emitted
    assert key == "mission.takeoff"
    assert payload.get("native", False) is native
