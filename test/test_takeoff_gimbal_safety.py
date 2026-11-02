"""Tests for gimbal pre-flight safety verification before takeoff."""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Tuple

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.gimbal import GimbalStateMonitor


class _DummyDrone:
    def __init__(self) -> None:
        self.camera_commands: List[Tuple[float, float]] = []

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        self.camera_commands.append((tilt, pan))


def _make_context(no_fly: bool = False, search_tilt: float = -20.0):
    params = MissionParameters()
    params.no_fly = no_fly
    params.gimbal.search_tilt_deg = search_tilt
    drone = _DummyDrone()
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


def test_verify_gimbal_real_flight_passes_when_monitor_confirms():
    ctx = _make_context(no_fly=False, search_tilt=-30.0)
    monitor = GimbalStateMonitor()
    # Mock Vector3 message with x = -30.0
    monitor.orientation_callback(SimpleNamespace(x=-30.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is True
    assert ctx.current_tilt_deg == -30.0
    assert len(ctx.milestones) == 0


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


def test_verify_gimbal_passes_when_clamped_by_ground_limit():
    ctx = _make_context(no_fly=False, search_tilt=-26.0)
    monitor = GimbalStateMonitor()
    # Real Bebop 2 on ground: clamped at -13.0 deg
    monitor.orientation_callback(SimpleNamespace(x=-13.0, y=0.0))
    ctx.gimbal_monitor = monitor

    step = TakeoffStep()
    assert step._verify_gimbal(ctx) is True
    assert len(ctx.milestones) == 0

