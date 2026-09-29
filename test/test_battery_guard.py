"""Unit tests for the onboard battery safety net (``telemetry.battery``).

The ground station owns the battery failsafe; this net covers the runs it is
not in command of. Its contract is narrow: silence without a fresh reading, a
one-shot warning at a fixed 10 %, and a land in place at ``battery.land_pct``
that goes through the existing ``trigger_emergency_land`` and nothing else.
"""

from __future__ import annotations

import inspect
import logging
import math
import time
from types import SimpleNamespace
from typing import List

import pytest

from mvp_mission_bebop.parameters import (
    BatteryConfig,
    FlightKinematicsConfig,
    MissionParameters,
    TimeoutsConfig,
)
from mvp_mission_bebop.telemetry import battery as battery_module
from mvp_mission_bebop.telemetry.battery import (
    BATTERY_WARNING_PCT,
    BatterySupervisor,
    normalize_percentage,
)
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor, TelemetryHealth


class DummyActuator:
    def __init__(self) -> None:
        self.landed = 0
        self.velocities: List[tuple] = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.velocities.append((vx, vy, vz, vyaw))

    def land(self):
        self.landed += 1


class Records(logging.Handler):
    """``caplog`` does not capture on this host; attach to the named logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, level: int = logging.DEBUG) -> List[str]:
        return [r.getMessage() for r in self.records if r.levelno >= level]


@pytest.fixture
def records():
    handler = Records()
    loggers = [logging.getLogger(name) for name in ("BatterySupervisor", "Milestone")]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    yield handler
    for lg, level in zip(loggers, previous):
        lg.removeHandler(handler)
        lg.setLevel(level)


@pytest.fixture(autouse=True)
def station_session(monkeypatch):
    """Route the failsafe's announcement to an alert line instead of audio."""
    monkeypatch.setenv("BMG_GCS_SESSION", "1")


def battery_msg(percentage: float, present: bool = True):
    return SimpleNamespace(present=present, percentage=percentage)


def build(land_pct: float = 20.0, stale_timeout_sec: float = 3.0):
    kinematics = FlightKinematicsConfig(target_altitude_m=1.0)
    timeouts = TimeoutsConfig()
    odometry = OdometrySupervisor(kinematics, timeouts)
    odometry.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    battery = BatterySupervisor(BatteryConfig(land_pct=land_pct), stale_timeout_sec)
    actuator = DummyActuator()
    failsafe = FailsafeSupervisor(
        actuator, odometry, timeouts, kinematics, battery_supervisor=battery
    )
    failsafe.notify_frame_received()
    return failsafe, battery, actuator


def milestone_lines(handler: Records) -> List[str]:
    return [m for m in handler.messages() if "[MILESTONE mission.battery_warning]" in m]


# ------------------------------------------------------------ absent / stale


def test_no_battery_telemetry_is_silent_and_never_lands(records):
    failsafe, battery, actuator = build()

    assert battery.telemetry_health() is TelemetryHealth.NEVER_RECEIVED
    assert battery.current_percentage() is None
    assert battery.land_reason() is None
    assert failsafe.evaluate_system_health() == (True, "Nominal")
    assert actuator.landed == 0
    assert records.messages(logging.WARNING) == []


@pytest.mark.parametrize(
    "message",
    [
        battery_msg(float("nan"), present=False),
        battery_msg(0.05, present=False),
        battery_msg(float("nan")),
        battery_msg(1.5),
        battery_msg(-0.1),
    ],
)
def test_samples_the_driver_marks_absent_or_corrupt_are_not_readings(message, records):
    failsafe, battery, _ = build(land_pct=50.0)

    battery.battery_callback(message)

    assert battery.telemetry_health() is TelemetryHealth.NEVER_RECEIVED
    assert failsafe.evaluate_system_health()[0] is True
    assert records.messages(logging.WARNING) == []


def test_a_stale_reading_is_not_acted_on(records):
    failsafe, battery, actuator = build(land_pct=30.0, stale_timeout_sec=0.02)
    battery.battery_callback(battery_msg(0.25))
    time.sleep(0.05)
    failsafe.notify_frame_received()
    records.records.clear()

    assert battery.telemetry_health() is TelemetryHealth.STALE
    assert battery.current_percentage() is None
    assert battery.land_reason() is None
    assert failsafe.evaluate_system_health() == (True, "Nominal")
    assert actuator.landed == 0
    assert records.messages(logging.WARNING) == []


def test_a_fresh_sample_after_a_stale_one_restores_the_guard():
    failsafe, battery, _ = build(land_pct=30.0, stale_timeout_sec=0.02)
    battery.battery_callback(battery_msg(0.25))
    time.sleep(0.05)
    assert failsafe.evaluate_system_health()[0] is True

    battery.battery_callback(battery_msg(0.25))
    assert failsafe.evaluate_system_health()[0] is False


# ------------------------------------------------------------------ warning


def test_the_warning_threshold_is_fixed_at_ten_percent():
    assert BATTERY_WARNING_PCT == 10.0


def test_warning_fires_at_exactly_ten_percent_and_not_before(records):
    failsafe, battery, actuator = build(land_pct=5.0)

    for fraction in (0.50, 0.11, 0.105, 0.1001):
        battery.battery_callback(battery_msg(fraction))
        assert not battery.warning_emitted, f"warned at {fraction * 100:.2f}%"
    assert milestone_lines(records) == []

    battery.battery_callback(battery_msg(0.10))

    assert battery.warning_emitted
    lines = milestone_lines(records)
    assert len(lines) == 1
    assert '"battery_pct":10.0' in lines[0]
    assert failsafe.evaluate_system_health() == (True, "Nominal")
    assert actuator.landed == 0


def test_warning_is_emitted_once_and_the_mission_continues(records):
    failsafe, battery, actuator = build(land_pct=5.0)

    for fraction in (0.10, 0.09, 0.08, 0.10, 0.07):
        battery.battery_callback(battery_msg(fraction))
        assert failsafe.evaluate_system_health()[0] is True

    assert len(milestone_lines(records)) == 1
    assert actuator.landed == 0
    assert actuator.velocities == []


def test_the_warning_milestone_is_a_valid_contract_line():
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    line = encode_milestone("mission.battery_warning", {"battery_pct": 10.0})
    assert line.startswith("[MILESTONE mission.battery_warning] ")


# --------------------------------------------------------------------- land


def test_land_fires_at_the_configured_threshold_and_not_above():
    failsafe, battery, _ = build(land_pct=37.0)

    battery.battery_callback(battery_msg(0.38))
    assert failsafe.evaluate_system_health() == (True, "Nominal")

    battery.battery_callback(battery_msg(0.37))
    healthy, reason = failsafe.evaluate_system_health()

    assert healthy is False
    assert "Battery at 37%" in reason and "37%" in reason


def test_land_below_the_threshold_is_also_reported():
    failsafe, battery, _ = build(land_pct=37.0)
    battery.inject_synthetic_reading(12)
    assert failsafe.evaluate_system_health()[0] is False


def test_the_land_goes_through_the_existing_trigger_emergency_land(monkeypatch):
    """The step call-site pattern: evaluate, then hand the reason to the failsafe."""
    failsafe, battery, actuator = build(land_pct=37.0)
    calls: List[str] = []
    original = FailsafeSupervisor.trigger_emergency_land

    def spy(self, reason):
        calls.append(reason)
        return original(self, reason)

    monkeypatch.setattr(FailsafeSupervisor, "trigger_emergency_land", spy)

    battery.battery_callback(battery_msg(0.30))
    healthy, reason = failsafe.evaluate_system_health()
    if not healthy:
        failsafe.trigger_emergency_land(reason)

    assert calls == [reason]
    assert actuator.velocities == [(0.0, 0.0, 0.0, 0.0)]
    assert actuator.landed == 1
    assert failsafe.failsafe_active is True


def test_the_battery_module_carries_no_landing_logic_of_its_own():
    source = inspect.getsource(battery_module)
    assert ".land(" not in source
    assert "move_velocity" not in source
    assert "goto_stage" not in source and "request_stage" not in source
    supervisor = BatterySupervisor(BatteryConfig())
    assert not any(hasattr(supervisor, name) for name in ("actuator", "drone", "failsafe"))


# ---------------------------------------------------------------- parameter


def test_land_pct_falls_back_to_twenty_when_absent_from_params_json():
    params = MissionParameters()
    params.update_from_dict({"kinematics": {"target_altitude_m": 1.4}, "no_fly": True})

    assert params.battery_land_pct == 20.0
    assert params.to_dict()["battery"] == {"land_pct": 20.0}


def test_land_pct_is_read_from_the_station_payload():
    params = MissionParameters()
    params.update_from_dict({"battery": {"land_pct": 33}})

    assert params.battery_land_pct == 33.0
    assert BatterySupervisor(params.battery).land_pct == 33.0


def test_battery_land_pct_is_not_a_serialized_field():
    assert "battery_land_pct" not in MissionParameters().to_dict()


@pytest.mark.parametrize("value, expected", [(0, 0.0), (100, 100.0), ("25", 25.0), (12.5, 12.5)])
def test_normalize_percentage_accepts_numbers_and_numeric_strings(value, expected):
    assert normalize_percentage(value) == expected


@pytest.mark.parametrize(
    "value, error",
    [
        (None, TypeError),
        (True, TypeError),
        ([20], TypeError),
        ("abc", ValueError),
        (float("nan"), ValueError),
        (math.inf, ValueError),
        (-1, ValueError),
        (100.5, ValueError),
    ],
)
def test_normalize_percentage_rejects_invalid_values(value, error):
    with pytest.raises(error):
        normalize_percentage(value, "battery.land_pct")


def test_a_malformed_threshold_fails_at_construction():
    with pytest.raises(ValueError):
        BatterySupervisor(BatteryConfig(land_pct=150.0))
    with pytest.raises(ValueError):
        BatterySupervisor(BatteryConfig(), stale_timeout_sec=0.0)


def test_a_failsafe_without_a_battery_supervisor_keeps_its_behaviour():
    kinematics = FlightKinematicsConfig(target_altitude_m=1.0)
    timeouts = TimeoutsConfig()
    odometry = OdometrySupervisor(kinematics, timeouts)
    odometry.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    failsafe = FailsafeSupervisor(DummyActuator(), odometry, timeouts, kinematics)

    assert failsafe.battery_supervisor is None
    assert failsafe.evaluate_system_health() == (True, "Nominal")
