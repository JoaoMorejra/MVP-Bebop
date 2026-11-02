"""The keyboard nudge inside the mission: additive, scoped, never in control of the flow."""

from __future__ import annotations

import threading
import types
from dataclasses import replace
from typing import List, Tuple

import pytest

from mvp_mission_bebop.actuators.nudge_overlay import NudgeOverlay
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.parameters import NudgeConfig
from mvp_mission_bebop.steps.base import StepStatus, governed_vz
from test_stage_altitude_wiring import Ctx, Drone


class Recorder:
    """Stands in for the kinematic simulator: receives every transmitted Twist."""

    def __init__(self) -> None:
        self.commands: List[Tuple[float, float, float, float]] = []

    def command(self, vx, vy, vz, vyaw) -> None:
        self.commands.append((vx, vy, vz, vyaw))

    def land(self) -> None:
        pass

    def takeoff(self, altitude) -> None:
        pass


#: A deadman long enough for a synchronous step to run with the key held.
HELD = replace(NudgeConfig(), deadman_sec=60.0)


def proxied(overlay=None):
    recorder = Recorder()
    proxy = BenchtopDroneProxy(Drone(), no_fly=True, simulator=recorder, nudge_overlay=overlay)
    return proxy, recorder


class Tracker:
    def __init__(self) -> None:
        self.calls = []

    def command(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None) -> None:
        self.calls.append((vx, vy, vz, vyaw))


def run_search(nudged: bool):
    from mvp_mission_bebop.steps.search import ForwardSearchStep

    ctx = Ctx(detections=False)
    overlay = NudgeOverlay(HELD)
    if nudged:
        overlay.arm()
        overlay.set_intent(1.0, 0.0, 0.0)
    ctx.drone, recorder = proxied(overlay)
    ctx.nudge = overlay
    status = ForwardSearchStep().execute(ctx)
    return ctx, status, recorder


def test_nudge_never_interrupts_the_mission():
    plain_ctx, plain_status, _ = run_search(nudged=False)
    ctx, status, recorder = run_search(nudged=True)

    assert status is plain_status
    assert not ctx.interrupted()
    assert not ctx.stage_jump_event.is_set()
    assert not ctx.emergency_event.is_set()
    assert ctx.failsafe.failsafe_active is False
    assert any(vx > 0.0 for vx, _vy, _vz, _vyaw in recorder.commands), "the nudge never reached the airframe"
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in run_search(nudged=False)[2].commands)


def test_no_effect_before_takeoff_is_complete():
    overlay = NudgeOverlay(HELD)
    proxy, recorder = proxied(overlay)
    overlay.set_intent(1.0, 0.0, 0.0)
    for _ in range(10):
        proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
    assert all(c == (0.0, 0.0, 0.0, 0.0) for c in recorder.commands)


def test_takeoff_arms_the_nudge_only_on_success(monkeypatch):
    from test_takeoff_acquisition import hovering_context, pad_params

    from mvp_mission_bebop.steps.takeoff import TakeoffStep

    ctx, _ = hovering_context(monkeypatch, pad_params(1.0, 0.0))
    ctx.nudge = NudgeOverlay(HELD)
    assert TakeoffStep()._finalize(ctx) is StepStatus.SUCCESS
    assert ctx.nudge.armed is True

    failed, _ = hovering_context(monkeypatch, pad_params(1.0, 0.0))
    failed.nudge = NudgeOverlay(HELD)
    failed.emergency_event.set()
    TakeoffStep().execute(failed)
    assert failed.nudge.armed is False


def _permit_ctx(stage=2, **blackboard):
    board = types.SimpleNamespace(landing_started=False, precision_landing_active=False, nudge_suspended=False)
    for key, value in blackboard.items():
        setattr(board, key, value)
    return types.SimpleNamespace(
        failsafe=types.SimpleNamespace(failsafe_active=False),
        emergency_event=threading.Event(),
        blackboard=board,
        current_stage=stage,
        flying_state=3,
    )


def test_no_effect_on_ground_or_during_final_landing():
    from mvp_mission_bebop.mission import nudge_permit

    cfg = NudgeConfig()
    assert nudge_permit(_permit_ctx(), cfg)() is True
    assert nudge_permit(_permit_ctx(landing_started=True), cfg)() is False
    assert nudge_permit(_permit_ctx(precision_landing_active=True), cfg)() is False
    assert nudge_permit(_permit_ctx(precision_landing_active=True), replace(cfg, allow_in_precision_landing=True))() is True
    grounded = _permit_ctx()
    grounded.flying_state = 0
    assert nudge_permit(grounded, cfg)() is False
    landing = _permit_ctx()
    landing.flying_state = 4
    assert nudge_permit(landing, cfg)() is False
    assert nudge_permit(_permit_ctx(stage=1), cfg)() is False
    emergency = _permit_ctx()
    emergency.emergency_event.set()
    assert nudge_permit(emergency, cfg)() is False
    failed = _permit_ctx()
    failed.failsafe.failsafe_active = True
    assert nudge_permit(failed, cfg)() is False

    ctx = _permit_ctx(landing_started=True)
    overlay = NudgeOverlay(HELD, permit=nudge_permit(ctx, cfg))
    overlay.arm()
    proxy, recorder = proxied(overlay)
    overlay.set_intent(1.0, 1.0, 1.0)
    for _ in range(10):
        proxy.move_velocity(0.0, -0.1, 0.0, 0.0)
    assert all(c == (0.0, -0.1, 0.0, 0.0) for c in recorder.commands)


def test_stage4_allows_nudge_by_default_and_never_captures_while_active():
    from mvp_mission_bebop.mission import nudge_permit
    from mvp_mission_bebop.steps.inspection import nudge_active

    cfg = NudgeConfig()
    assert nudge_permit(_permit_ctx(stage=4), cfg)() is True
    assert nudge_permit(_permit_ctx(stage=4), replace(cfg, allow_in_inspection=False))() is False
    assert nudge_permit(_permit_ctx(stage=4, nudge_suspended=True), cfg)() is False

    overlay = NudgeOverlay(HELD)
    overlay.arm()
    ctx = types.SimpleNamespace(nudge=overlay)
    assert nudge_active(ctx) is False
    overlay.set_intent(1.0, 0.0, 0.0)
    proxy, _ = proxied(overlay)
    proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
    import time

    time.sleep(0.05)
    proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
    assert nudge_active(ctx) is True
    assert nudge_active(types.SimpleNamespace()) is False


def test_inspection_waits_for_the_nudge_to_release_before_the_capture(monkeypatch):
    from mvp_mission_bebop.steps.inspection import NadirInspectionStep as InspectionStep

    ctx = Ctx(detections=True)
    ctx.blackboard.nudge_suspended = False
    overlay = NudgeOverlay(HELD, permit=lambda: not ctx.blackboard.nudge_suspended)
    overlay.arm()
    ctx.drone, _ = proxied(overlay)
    ctx.nudge = overlay
    applied_at_capture = []

    def snapshot():
        applied_at_capture.append(overlay.snapshot().applied)

    monkeypatch.setattr(ctx.drone, "snapshot", snapshot)
    overlay.set_intent(1.0, 0.0, 0.0)
    for _ in range(20):
        ctx.drone.move_velocity(0.0, 0.0, 0.0, 0.0)
    InspectionStep()._capture(ctx, settled=True)

    assert applied_at_capture == [(0.0, 0.0, 0.0)]
    assert ctx.blackboard.nudge_suspended is False


def test_emergency_landing_ignores_nudge():
    from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

    ctx = Ctx(detections=False)
    overlay = NudgeOverlay(HELD)
    overlay.arm()
    proxy, recorder = proxied(overlay)
    overlay.set_intent(1.0, 1.0, 1.0)
    for _ in range(10):
        proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
    assert recorder.commands[-1] != (0.0, 0.0, 0.0, 0.0)
    failsafe = FailsafeSupervisor(proxy, ctx.odom_supervisor, ctx.params.timeouts, ctx.params.kinematics)
    recorder.commands.clear()
    failsafe.trigger_emergency_land("test")
    assert recorder.commands and all(c == (0.0, 0.0, 0.0, 0.0) for c in recorder.commands)
    assert overlay.armed is False

    from mvp_mission_bebop.engine.runner import MissionRunner

    overlay.arm()
    overlay.set_intent(1.0, 1.0, 1.0)
    for _ in range(10):
        proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
    ctx.drone = proxy
    ctx.nudge = overlay
    ctx.detection_reveal_enabled = True
    recorder.commands.clear()
    MissionRunner(ctx, [], stage_numbers=[])._transmit_emergency_landing(0.0)
    assert recorder.commands and all(c == (0.0, 0.0, 0.0, 0.0) for c in recorder.commands)


def test_governor_feedforward_sees_the_nudge():
    from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
    from mvp_mission_bebop.parameters import AltitudeGovernorConfig

    def regime(magnitude: float) -> float:
        governor = AltitudeHoldGovernor(1.0, AltitudeGovernorConfig(feedforward_gain=0.2))
        ctx = types.SimpleNamespace(
            governor=governor,
            odom_supervisor=types.SimpleNamespace(),
            nudge=types.SimpleNamespace(horizontal_magnitude=lambda: magnitude),
        )
        values = [governed_vz(ctx, 1.0, 1.0 / 15.0, vx_commanded=0.0) for _ in range(200)]
        return sum(values[-60:]) / 60.0

    assert regime(0.08) - regime(0.0) == pytest.approx(0.2 * 0.08, abs=0.002)


def test_dead_reckoning_receives_the_total_command():
    overlay = NudgeOverlay(HELD)
    overlay.arm()
    tracker = Tracker()
    recorder = Recorder()
    proxy = BenchtopDroneProxy(Drone(), no_fly=True, simulator=recorder, motion_tracker=tracker, nudge_overlay=overlay)
    overlay.set_intent(0.0, 1.0, 0.0)
    import time

    for _ in range(10):
        proxy.move_velocity(0.0, 0.0, 0.0, 0.0)
        time.sleep(0.03)
    assert tracker.calls[-1] == recorder.commands[-1]
    assert tracker.calls[-1][1] > 0.0
