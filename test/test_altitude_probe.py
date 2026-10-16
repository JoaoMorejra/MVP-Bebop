"""The altitude probe: the instrument every field question about the pad edge depends on.

``/bebop/states/altitude`` is the firmware's own altitude above the takeoff
point (``ardrone3.xml`` ``AltitudeChanged``). Nothing in the mission subscribed
to it. It is now recorded next to the odometric altitude, once a second, as a
log line and nothing else.
"""

from __future__ import annotations

import contextlib
import logging
import math
import types
from typing import List

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry import odometry as odometry_module
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def supervisor_log():
    handler = _Lines()
    target = logging.getLogger("OdometrySupervisor")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(odometry_module, "time", types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


def supervisor(probe: bool = True) -> OdometrySupervisor:
    params = MissionParameters()
    params.calibration.altitude_probe_enabled = probe
    return OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)


def altitude_msg(value: float):
    return types.SimpleNamespace(data=value)


def probe_lines(lines: List[str]) -> List[str]:
    return [line for line in lines if line.startswith("[ALT_PROBE ")]


def test_the_firmware_altitude_is_kept_with_its_arrival_instant(clock):
    odom = supervisor()
    odom.altitude_state_callback(altitude_msg(0.82))

    assert odom.snapshot().firmware_altitude_m == pytest.approx(0.82)
    assert odom.firmware_altitude_stamp == clock.now


def test_a_nan_firmware_altitude_is_ignored(clock):
    """The driver publishes NaN with no link; it is an absent sample, not a value."""
    odom = supervisor()
    odom.altitude_state_callback(altitude_msg(0.82))
    clock.now += 1.0
    odom.altitude_state_callback(altitude_msg(math.nan))

    assert odom.snapshot().firmware_altitude_m == pytest.approx(0.82)
    assert odom.firmware_altitude_stamp == 100.0


def test_no_firmware_altitude_reads_as_none():
    assert supervisor().snapshot().firmware_altitude_m is None


def test_the_probe_logs_once_a_second(clock):
    odom = supervisor()
    odom.altitude_state_callback(altitude_msg(0.81))
    with supervisor_log() as log:
        for _ in range(31):
            odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8, vz=0.01)
            clock.now += 0.1

    lines = probe_lines(log.lines)
    assert len(lines) == 4
    first = lines[0]
    for field in ("t=", "s=", "state=", "z_rel=0.800", "z_est=0.800", "fw_alt=0.810", "vz=0.010", "guard="):
        assert field in first, f"{field!r} missing from {first!r}"
    assert first.endswith("]")


def test_the_probe_reports_a_missing_firmware_altitude(clock):
    odom = supervisor()
    with supervisor_log() as log:
        odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8)
    assert "fw_alt=na" in probe_lines(log.lines)[0]


def test_a_disabled_probe_logs_nothing(clock):
    odom = supervisor(probe=False)
    with supervisor_log() as log:
        for _ in range(30):
            odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8)
            clock.now += 0.1
    assert probe_lines(log.lines) == []
