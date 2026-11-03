"""Tests for gimbal pre-flight safety verification before takeoff."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.gimbal import GimbalStateMonitor, actuation_travel


class _DummyDrone:
    """Records camera commands; a responsive one feeds them back as the reported tilt."""

    def __init__(self, monitor: Optional[GimbalStateMonitor] = None) -> None:
        self.camera_commands: List[Tuple[float, float]] = []
        self._monitor = monitor

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        self.camera_commands.append((tilt, pan))
        if self._monitor is not None:
            self._monitor.orientation_callback(SimpleNamespace(x=tilt, y=pan))


class _StopsShortDrone(_DummyDrone):
    """A gimbal that leaves its start but never travels past ``limit`` degrees."""

    def __init__(self, monitor: GimbalStateMonitor, limit: float) -> None:
        super().__init__()
        self._sink = monitor
        self._limit = limit

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        super().camera_control(tilt, pan)
        self._sink.orientation_callback(SimpleNamespace(x=min(tilt, self._limit), y=pan))


def _make_context(
    no_fly: bool = False,
    search_tilt: float = -20.0,
    responsive: Optional[GimbalStateMonitor] = None,
    drone: Optional[_DummyDrone] = None,
):
    params = MissionParameters()
    params.no_fly = no_fly
    params.gimbal.search_tilt_deg = search_tilt
    drone = drone if drone is not None else _DummyDrone(responsive)
    milestones: List[Tuple[str, str]] = []

    ctx = SimpleNamespace(
        params=params,
        drone=drone,
        current_tilt_deg=0.0,
        interrupted=lambda: False,
        emit_milestone=lambda key, text: milestones.append((key, text)),
        milestones=milestones,
    )
    return ctx


def test_verify_gimbal_in_no_fly_passes():
    ctx = _make_context(no_fly=True, search_tilt=-25.0)
    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is True
    assert ctx.current_tilt_deg == -25.0
    assert len(ctx.drone.camera_commands) >= 1
    assert ctx.drone.camera_commands[0][0] == -25.0


def test_verify_gimbal_real_flight_passes_when_gimbal_follows_commands():
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-80.0, y=0.0))
    ctx = _make_context(no_fly=False, search_tilt=-30.0, responsive=monitor)
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is True
    assert ctx.current_tilt_deg == -30.0
    assert monitor.current_tilt() == -30.0
    assert len(ctx.milestones) == 0


def test_verify_gimbal_commands_only_the_configured_tilt():
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-80.0, y=0.0))
    ctx = _make_context(no_fly=False, search_tilt=-30.0, responsive=monitor)
    ctx.gimbal_monitor = monitor

    assert TakeoffStep()._verify_gimbal(ctx) is True
    assert {tilt for tilt, _ in ctx.drone.camera_commands} == {-30.0}


def test_verify_gimbal_accepts_a_small_difference_when_the_reading_follows():
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-27.0, y=0.0))
    ctx = _make_context(no_fly=False, search_tilt=-30.0, responsive=monitor)
    ctx.gimbal_monitor = monitor

    assert TakeoffStep()._verify_gimbal(ctx) is True
    assert {tilt for tilt, _ in ctx.drone.camera_commands} == {-30.0}
    assert monitor.current_tilt() == -30.0


def test_verify_gimbal_passes_as_soon_as_the_gimbal_moves_even_short_of_the_target():
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-80.0, y=0.0))
    ctx = _make_context(no_fly=False, search_tilt=-30.0, drone=_StopsShortDrone(monitor, limit=-60.0))
    ctx.gimbal_monitor = monitor

    started = time.monotonic()
    assert TakeoffStep()._verify_gimbal(ctx) is True
    assert time.monotonic() - started < 1.0
    assert ctx.milestones == []


def test_verify_gimbal_fails_when_the_gimbal_moves_away_from_the_target():
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-40.0, y=0.0))
    drone = _DummyDrone()
    drone.camera_control = lambda tilt, pan=0.0: monitor.orientation_callback(SimpleNamespace(x=-50.0, y=pan))
    ctx = _make_context(no_fly=False, search_tilt=-30.0, drone=drone)
    ctx.gimbal_monitor = monitor

    assert TakeoffStep()._verify_gimbal(ctx) is False
    assert ctx.milestones[0][0] == "mission.abort"


def test_verify_gimbal_fails_when_a_mute_gimbal_already_reads_the_target():
    """Flight 20261102_171448: the reading was 1 deg from the target before any command, and never moved."""
    ctx = _make_context(no_fly=False, search_tilt=-30.0)
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-29.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is False
    assert ctx.milestones[0][0] == "mission.abort"


def test_verify_gimbal_fails_when_a_mute_gimbal_sits_past_the_target():
    """Flight 20261102_170317: the camera stayed at -80 deg from the previous flight."""
    ctx = _make_context(no_fly=False, search_tilt=-28.0)
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-80.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is False
    assert ctx.milestones[0][0] == "mission.abort"


def test_verify_gimbal_real_flight_fails_when_gimbal_unresponsive():
    ctx = _make_context(no_fly=False, search_tilt=-35.0)
    monitor = GimbalStateMonitor()
    # Unresponsive gimbal: camera stuck at 0.0 deg
    monitor.orientation_callback(SimpleNamespace(x=0.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is False
    assert len(ctx.milestones) == 1
    assert ctx.milestones[0][0] == "mission.abort"
    assert "Falha no controle do gimbal" in ctx.milestones[0][1]


def test_verify_gimbal_real_flight_fails_when_no_telemetry():
    ctx = _make_context(no_fly=False, search_tilt=-35.0)
    monitor = GimbalStateMonitor()
    # No orientation callback received at all
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is False
    assert len(ctx.milestones) == 1
    assert ctx.milestones[0][0] == "mission.abort"
    assert "Falha no controle do gimbal" in ctx.milestones[0][1]


def test_verify_gimbal_fails_when_stuck_at_minus_13():
    """Flight 20261102_174259: -13 deg against -30 deg passed the old gate and the camera never moved in flight."""
    ctx = _make_context(no_fly=False, search_tilt=-30.0)
    monitor = GimbalStateMonitor()
    monitor.orientation_callback(SimpleNamespace(x=-13.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is False
    assert ctx.milestones[0][0] == "mission.abort"


def test_actuation_travel_is_signed_toward_the_target():
    assert actuation_travel(start=-80.0, actual=-30.0, target=-30.0) == 50.0
    assert actuation_travel(start=-13.0, actual=-13.0, target=-30.0) == 0.0
    assert actuation_travel(start=-30.0, actual=-40.0, target=-15.0) == -10.0


def test_verify_gimbal_fails_without_a_monitor_in_a_real_flight():
    ctx = _make_context(no_fly=False, search_tilt=-30.0)

    assert TakeoffStep()._verify_gimbal(ctx) is False
    assert ctx.milestones[0][0] == "mission.abort"
