"""``rtl.enabled``: the end of flight is a per-flight choice in both profiles (section 3.7)."""

from __future__ import annotations

import math
import types

import pytest

from mvp_mission_bebop.mission_mode import MissionMode, stage_five_name
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import STOW_GIMBAL_TILT_DEG, NadirInspectionStep
from mvp_mission_bebop.steps.pipeline import build_steps
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from test_simple_mode_steps import inspection_ctx, land_ctx, simulated_time  # noqa: F401

CASES = [(mode, rtl) for mode in MissionMode for rtl in (True, False)]
_RETURN_WORDS = ("base", "retorno", "marcador")


def run_stage_five(monkeypatch, mode: MissionMode, rtl_enabled: bool):
    drone, ctx = land_ctx(mode.value)
    ctx.params.rtl.enabled = rtl_enabled
    spoken, milestones, branches = [], [], []
    monkeypatch.setattr(ClosedLoopRTLStep, "_announce", staticmethod(lambda action, detail: spoken.append((action, detail))))
    monkeypatch.setattr(rtl_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    step = ClosedLoopRTLStep(mode=mode, rtl_enabled=rtl_enabled)
    step._build_sensor = lambda _ctx: None
    for branch in ("_land_in_place", "_land_over_base"):
        original = getattr(step, branch)

        def traced(*args, _name=branch, _original=original, **kwargs):
            branches.append(_name)
            return _original(*args, **kwargs)

        setattr(step, branch, traced)
    if rtl_enabled and mode is MissionMode.ADVANCED:
        # The full return is covered by test_aruco_rtl.py; here only the branch
        # is checked, so the flight stops where the return leg would begin.
        ctx.blackboard.target_confirmed = False

        def reached(_ctx):
            raise _ReturnLegReached

        step._engage_return_attitude = reached
        try:
            step.execute(ctx)
        except _ReturnLegReached:
            branches.append("return_leg")
        return None, drone, ctx, spoken, milestones, branches
    status = step.execute(ctx)
    return status, drone, ctx, spoken, milestones, branches


class _ReturnLegReached(Exception):
    """Raised where the Advanced return leg starts."""


@pytest.mark.parametrize("mode, rtl_enabled", CASES)
def test_the_stage_five_name_is_a_function_of_profile_and_plan(mode, rtl_enabled):
    step = build_steps(mode, rtl_enabled)[5]()
    assert step.name == f"STEP 5: {stage_five_name(mode, rtl_enabled)}"
    assert step.name.startswith("STEP 5: ")
    if not rtl_enabled:
        assert step.name == "STEP 5: Landing at Inspection Point"


@pytest.mark.parametrize("mode, rtl_enabled", CASES)
def test_execute_takes_the_branch_of_the_plan(monkeypatch, mode, rtl_enabled):
    status, drone, _ctx, _spoken, milestones, branches = run_stage_five(monkeypatch, mode, rtl_enabled)
    keys = [key for key, _payload in milestones]
    if not rtl_enabled:
        assert branches == ["_land_in_place"]
        assert "mission.rtl_start" not in keys
        assert status is StepStatus.SUCCESS and drone.land_calls >= 1
    elif mode is MissionMode.SIMPLE:
        assert branches == ["_land_over_base"]
        assert drone.land_calls >= 1
    else:
        assert branches == ["return_leg"]
        assert "mission.rtl_start" in keys


@pytest.mark.parametrize("mode", list(MissionMode))
def test_without_the_return_nothing_spoken_names_the_base_the_return_or_the_marker(monkeypatch, mode):
    status, _drone, _ctx, spoken, milestones, _branches = run_stage_five(monkeypatch, mode, False)
    assert status is StepStatus.SUCCESS
    assert spoken, "the landing in place was not announced"
    for action, detail in spoken:
        text = f"{action} {detail}".lower()
        assert not any(word in text for word in _RETURN_WORDS), text
    assert ("Pouso no local", "inspeção concluída, executando pouso no local da inspeção") in spoken
    for key, payload in milestones:
        if key in ("mission.landing", "mission.touchdown"):
            assert payload["at_base"] is False


def test_simple_without_the_return_restores_the_heading_then_stows_the_gimbal(simulated_time):
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.params.rtl.enabled = False
    ctx.params.rtl.camera_tilt_deg = -45.0

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS

    error = abs((ctx.odom_supervisor.yaw - 0.5 + math.pi) % (2 * math.pi) - math.pi)
    assert error <= math.radians(ctx.params.yaw_pattern.return_tolerance_deg) + 1e-6
    assert ctx.drone.camera_calls[-1] == STOW_GIMBAL_TILT_DEG == -80.0


def test_simple_with_the_return_lowers_the_gimbal_to_the_marker_tilt(simulated_time):
    ctx = inspection_ctx(offset_deg=20.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.params.rtl.camera_tilt_deg = -65.0

    NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.drone.camera_calls[-1] == -65.0


def test_simple_without_the_return_lands_on_the_pad_with_the_undiscounted_threshold():
    _drone, ctx = land_ctx("simple")
    ctx.params.rtl.enabled = False
    ctx.params.platform.height_m = 1.0
    ctx.params.platform.edge_ahead_m = None
    snapshot = types.SimpleNamespace(platform_state="unknown")
    threshold = ClosedLoopRTLStep._grounded_altitude(ctx, snapshot, at_base=False)
    assert threshold == ctx.params.rtl.touchdown_altitude_m
