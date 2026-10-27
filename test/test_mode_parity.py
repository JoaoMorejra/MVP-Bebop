"""The safety envelope is the same in the two profiles."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.controllers.yaw_pattern import YawServoingController
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from test_simple_mode_steps import YawDrone, inspection_ctx, simple_ctx, simulated_time  # noqa: F401
from test_stage_altitude_wiring import Ctx


class UnhealthyOdometry:
    """Delegates to the shared fake and reports a stale telemetry stream."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def telemetry_health():
        from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

        return TelemetryHealth.STALE


def make_ctx(mode: MissionMode, *, detections: bool = True) -> Ctx:
    ctx = Ctx(detections=detections)
    if mode is MissionMode.SIMPLE:
        ctx = simple_ctx(detections=detections)
    ctx.params.mission_mode = mode.value
    return ctx


# ------------------------------------------------ the health check has no mode


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_search_stage_lands_on_a_health_fault_in_every_profile(mode):
    ctx = make_ctx(mode, detections=False)
    ctx.odom_supervisor = UnhealthyOdometry(ctx.odom_supervisor)
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    ctx.drone.no_fly = False

    assert LateralSearchStep(mode=mode).execute(ctx) is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True


@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_tracking_stage_lands_on_a_health_fault_in_every_profile(mode):
    ctx = make_ctx(mode)
    ctx.blackboard.target_confirmed = True
    ctx.odom_supervisor = UnhealthyOdometry(ctx.odom_supervisor)
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    ctx.drone.no_fly = False

    assert VisualServoingStep(mode=mode).execute(ctx) is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True


@pytest.mark.parametrize("mode", list(MissionMode))
def test_an_abort_stops_the_search_with_a_zero_last_command_in_every_profile(mode):
    ctx = make_ctx(mode, detections=False)
    ctx.emergency_event.set()

    assert LateralSearchStep(mode=mode).execute(ctx) is StepStatus.ABORTED
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands[-1:])


# ----------------------------------------------- the new yaw loop is guarded too


def test_the_heading_restore_lands_on_a_health_fault(monkeypatch):
    ctx = inspection_ctx(offset_deg=120.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.drone.no_fly = False
    original = NadirInspectionStep._restore_heading

    def restore_with_stale_odometry(self, step_ctx):
        step_ctx.failsafe.odom_supervisor = UnhealthyOdometry(step_ctx.failsafe.odom_supervisor)
        return original(self, step_ctx)

    monkeypatch.setattr(NadirInspectionStep, "_restore_heading", restore_with_stale_odometry)

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True
    assert not ctx.failsafe.yaw_authority.is_open
    assert ctx.drone.commands[-1][-1] == 0.0


def test_a_stage_jump_during_the_heading_restore_aborts_with_zero_yaw(monkeypatch):
    """The critical-charge return reaches the Simple heading restore as a jump, never a landing."""
    ctx = inspection_ctx(offset_deg=120.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.drone.no_fly = False
    landings = []
    ctx.drone.land = lambda: landings.append(True)
    original_move = ctx.drone.move_velocity

    def jump_after_the_first_turn(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        original_move(vx, vy, vz, vyaw, duration)
        if vyaw != 0.0 and not ctx.stage_jump_event.is_set():
            ctx.requested_stage = 5
            ctx.stage_jump_event.set()

    ctx.drone.move_velocity = jump_after_the_first_turn

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.ABORTED
    assert ctx.drone.commands[-1][3] == 0.0
    assert not ctx.failsafe.yaw_authority.is_open
    assert ctx.failsafe.failsafe_active is False
    assert landings == []


def test_the_heading_restore_ignores_the_suspended_video_stream(monkeypatch):
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    ctx.drone.no_fly = False
    original = NadirInspectionStep._restore_heading

    def restore_after_vision_suspended(self, step_ctx):
        step_ctx.failsafe.last_valid_frame_timestamp -= (
            10.0 * step_ctx.params.timeouts.video_stream_timeout_sec
        )
        return original(self, step_ctx)

    monkeypatch.setattr(NadirInspectionStep, "_restore_heading", restore_after_vision_suspended)

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.failsafe.failsafe_active is False


def test_the_health_check_can_skip_only_the_video_item():
    ctx = Ctx(detections=False)
    ctx.failsafe.last_valid_frame_timestamp -= 100.0
    healthy_without_video, _ = ctx.failsafe.evaluate_system_health(check_video=False)
    healthy_with_video, reason = ctx.failsafe.evaluate_system_health()
    assert healthy_without_video is True
    assert healthy_with_video is False and "stream" in reason.lower()


@pytest.mark.parametrize("mode, rtl_enabled", [(MissionMode.ADVANCED, False), (MissionMode.SIMPLE, True)])
def test_an_abort_during_the_final_stage_still_commands_the_landing(mode, rtl_enabled):
    import logging

    from mvp_mission_bebop.steps import rtl as rtl_module
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
    from test_simple_mode_steps import land_ctx

    drone, ctx = land_ctx(mode.value)
    ctx.params.rtl.enabled = rtl_enabled
    ctx.emergency_event.set()

    step = ClosedLoopRTLStep(mode=mode, rtl_enabled=rtl_enabled)
    step._build_sensor = lambda _ctx: None
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture(level=logging.WARNING)
    rtl_module.logger.addHandler(handler)
    try:
        step.execute(ctx)
    finally:
        rtl_module.logger.removeHandler(handler)

    assert "Emergency raised during touchdown; continuing to command land." in records
    assert drone.land_calls >= 1
    assert ctx.blackboard.touchdown_confirmed is not True
