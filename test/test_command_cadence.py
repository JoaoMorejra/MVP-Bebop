"""Inter-command cadence measured at the single point every Twist passes.

The Stage 2 to 4 loops already report their own overruns; Stage 1 ascent and
the Stage 5 centring and touchdown loops did not, so the interval between two
commands actually leaving the proxy is the only measure common to all of them.
"""

from __future__ import annotations

import logging
from typing import List

import pytest

from mvp_mission_bebop.actuators.cadence import CommandCadenceMonitor


class _Clock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _logger() -> "tuple[logging.Logger, _Collector]":
    target = logging.getLogger("test.command_cadence")
    target.setLevel(logging.DEBUG)
    target.propagate = False
    handler = _Collector()
    target.handlers = [handler]
    return target, handler


def test_regular_cadence_reports_the_period() -> None:
    clock = _Clock()
    monitor = CommandCadenceMonitor(clock=clock)
    for _ in range(10):
        monitor.note(0.0, 0.1, 0.0, 0.0)
        clock.now += 0.0667
    assert monitor.p95_ms() == pytest.approx(66.7, abs=0.1)
    assert monitor.max_ms() == pytest.approx(66.7, abs=0.1)
    assert len(monitor.intervals_ms()) == 9


def test_gap_appears_in_max() -> None:
    clock = _Clock()
    monitor = CommandCadenceMonitor(clock=clock)
    monitor.note(0.0, 0.0, 0.0, 0.0)
    clock.now += 0.4
    monitor.note(0.0, 0.0, 0.0, 0.0)
    clock.now += 0.0667
    monitor.note(0.0, 0.0, 0.0, 0.0)
    assert monitor.max_ms() >= 400.0


def test_maybe_log_is_rate_limited() -> None:
    clock = _Clock()
    monitor = CommandCadenceMonitor(clock=clock)
    target, handler = _logger()
    for _ in range(30):
        monitor.note(0.0, 0.1, 0.0, 0.0)
        monitor.maybe_log(target, period_sec=1.0)
        clock.now += 0.0667
    assert 1 <= len(handler.messages) <= 2
    assert handler.messages[0].startswith("[CMD t=")
    for field in ("vx=", "vy=", "vz=", "vyaw=", "dt_ms=", "p95_ms=", "max_ms="):
        assert field in handler.messages[0]


def test_nonfinite_command_is_counted_not_raised() -> None:
    clock = _Clock()
    monitor = CommandCadenceMonitor(clock=clock)
    monitor.note(float("nan"), 0.0, float("inf"), 0.0)
    clock.now += 0.0667
    monitor.note(0.0, 0.0, 0.0, 0.0)
    assert monitor.nonfinite == 1
    assert "nonfinite=1" in monitor.report()


def test_window_is_bounded() -> None:
    clock = _Clock()
    monitor = CommandCadenceMonitor(clock=clock, window=8)
    for _ in range(50):
        monitor.note(0.0, 0.0, 0.0, 0.0)
        clock.now += 0.05
    assert len(monitor.intervals_ms()) == 8


def test_invalid_window_raises() -> None:
    with pytest.raises(ValueError):
        CommandCadenceMonitor(window=1)
