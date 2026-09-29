"""Unit tests for the Stage 1 pre-arm battery gate.

The gate refuses a launch whose charge is already at or below
``battery.land_pct`` -- the threshold the in-flight net lands on -- and
reports it on the same channel as the ground-calibration refusal.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import List

import pytest

from mvp_mission_bebop.parameters import BatteryConfig, MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.battery import BatterySupervisor
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class RecordingDrone:
    def __init__(self, no_fly: bool) -> None:
        self.no_fly = no_fly
        self.calls: List[str] = []

    def camera_control(self, tilt, pan=0.0):
        self.calls.append("camera_control")

    def flat_trim(self):
        self.calls.append("flat_trim")

    def delay(self, seconds):
        self.calls.append("delay")

    def takeoff(self, altitude):
        self.calls.append("takeoff")
        return True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.calls.append("move_velocity")

    def land(self):
        self.calls.append("land")


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def alerts(monkeypatch):
    """Alert lines, as the station receives them under a GCS session."""
    monkeypatch.setenv("BMG_GCS_SESSION", "1")
    handler = Records()
    target = logging.getLogger("Milestone")
    target.addHandler(handler)
    yield handler
    target.removeHandler(handler)


@pytest.fixture
def step_log():
    handler = Records()
    target = logging.getLogger("Step1Takeoff")
    target.addHandler(handler)
    yield handler
    target.removeHandler(handler)


def context(land_pct: float, reading=None, *, no_fly: bool = False):
    params = MissionParameters()
    params.battery.land_pct = land_pct
    params.no_fly = no_fly
    odometry = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)
    battery = BatterySupervisor(params.battery)
    if reading is not None:
        battery.inject_synthetic_reading(reading)
    drone = RecordingDrone(no_fly)
    failsafe = FailsafeSupervisor(
        drone, odometry, params.timeouts, params.kinematics, battery_supervisor=battery
    )
    return SimpleNamespace(
        params=params,
        drone=drone,
        failsafe=failsafe,
        odom_supervisor=odometry,
        current_tilt_deg=0.0,
    )


@pytest.fixture
def stop_after_gate(monkeypatch):
    """End ``execute`` at ground calibration, which runs right after the gate."""
    reached: List[bool] = []

    def calibrate(self, ctx):
        reached.append(True)
        return False

    monkeypatch.setattr(TakeoffStep, "_calibrate", calibrate)
    return reached


@pytest.mark.parametrize("reading", [22.0, 15.0, 0.0])
def test_launch_is_refused_at_or_below_the_land_threshold(reading, alerts, stop_after_gate):
    ctx = context(land_pct=22.0, reading=reading)

    status = TakeoffStep().execute(ctx)

    assert status is StepStatus.FAILURE
    assert ctx.drone.calls == [], "nothing may be commanded before the refusal"
    assert stop_after_gate == []
    refusals = [m for m in alerts.messages if m.startswith("[ALERT mission.takeoff_failed]")]
    assert len(refusals) == 1
    assert '"priority":"CRITICAL"' in refusals[0]
    assert "Falha na decolagem" in refusals[0]


def test_the_refusal_is_logged_with_the_reading_and_the_threshold(alerts, step_log, stop_after_gate):
    TakeoffStep().execute(context(land_pct=22.0, reading=18.0))

    assert any(
        "Battery at 18%" in m and "22% land threshold" in m and "Refusing to launch" in m
        for m in step_log.messages
    )


def test_launch_proceeds_above_the_land_threshold(alerts, stop_after_gate):
    ctx = context(land_pct=22.0, reading=23.0)

    TakeoffStep().execute(ctx)

    assert stop_after_gate == [True]
    assert "flat_trim" in ctx.drone.calls
    assert not any("mission.takeoff_failed" in m for m in alerts.messages)


def test_launch_proceeds_without_a_battery_reading(alerts, stop_after_gate):
    ctx = context(land_pct=22.0, reading=None)

    TakeoffStep().execute(ctx)

    assert stop_after_gate == [True]
    assert not any("mission.takeoff_failed" in m for m in alerts.messages)


def test_the_gate_uses_battery_land_pct_and_no_second_threshold():
    below = context(land_pct=40.0, reading=39.0)
    above = context(land_pct=38.0, reading=39.0)

    assert TakeoffStep()._check_prearm_battery(below) is False
    assert TakeoffStep()._check_prearm_battery(above) is True


def test_the_bench_continues_on_a_low_reading(alerts, stop_after_gate):
    ctx = context(land_pct=22.0, reading=10.0, no_fly=True)

    TakeoffStep().execute(ctx)

    assert stop_after_gate == [True]
    assert not any("mission.takeoff_failed" in m for m in alerts.messages)


def test_a_failsafe_without_a_battery_supervisor_does_not_gate():
    ctx = context(land_pct=22.0)
    ctx.failsafe.battery_supervisor = None
    assert TakeoffStep()._check_prearm_battery(ctx) is True
