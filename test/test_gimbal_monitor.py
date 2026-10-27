"""Gimbal state readback: what the camera actually reports, and the wait that checks it.

The mission commands the gimbal open loop: ``camera_control`` publishes on
``/bebop/move_camera`` and nothing on the airframe acknowledges it. The driver
now republishes the camera orientation the aircraft reports, and
``GimbalStateMonitor`` turns that into a verdict a step can act on.
"""

from __future__ import annotations

import math
import types
from typing import Callable, List, Optional

import pytest

from mvp_mission_bebop.telemetry.gimbal import GimbalOutcome, GimbalStateMonitor


class FakeClock:
    """A monotonic clock that only moves when the test sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _orientation(tilt: float, pan: float = 0.0):
    return types.SimpleNamespace(x=tilt, y=pan, z=0.0)


def _limits(*values: float):
    return types.SimpleNamespace(data=list(values))


def _monitor(clock: FakeClock, stale: float = 2.0) -> GimbalStateMonitor:
    return GimbalStateMonitor(stale_timeout_sec=stale, clock=clock)


def test_no_reading_is_not_a_tilt() -> None:
    clock = FakeClock()
    assert _monitor(clock).current_tilt() is None


def test_fresh_reading_is_reported_and_goes_stale() -> None:
    clock = FakeClock()
    monitor = _monitor(clock, stale=2.0)
    monitor.orientation_callback(_orientation(-79.5))
    assert monitor.current_tilt() == pytest.approx(-79.5)
    clock.now += 1.9
    assert monitor.current_tilt() == pytest.approx(-79.5)
    clock.now += 0.2
    assert monitor.current_tilt() is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_orientation_is_ignored(bad: float) -> None:
    clock = FakeClock()
    monitor = _monitor(clock)
    monitor.orientation_callback(_orientation(-40.0))
    monitor.orientation_callback(_orientation(bad))
    assert monitor.current_tilt() == pytest.approx(-40.0)


def test_limits_are_kept_and_malformed_ones_ignored() -> None:
    monitor = _monitor(FakeClock())
    assert monitor.tilt_limits() is None
    monitor.limits_callback(_limits(-83.0, 17.0, -35.0, 35.0))
    assert monitor.tilt_limits() == (-83.0, 17.0)
    monitor.limits_callback(_limits(1.0, 2.0))
    monitor.limits_callback(_limits(math.nan, 17.0, -35.0, 35.0))
    monitor.limits_callback(_limits(10.0, -10.0, -35.0, 35.0))
    assert monitor.tilt_limits() == (-83.0, 17.0)


def _wait(
    monitor: GimbalStateMonitor,
    clock: FakeClock,
    asserts: List[float],
    interrupted: Callable[[], bool] = lambda: False,
    target: float = -80.0,
    tolerance: float = 3.0,
    timeout: float = 3.0,
) -> GimbalOutcome:
    return monitor.wait_for_tilt(
        target,
        tolerance,
        timeout,
        lambda: asserts.append(clock.now),
        interrupted,
        period=0.25,
        sleep=clock.sleep,
    )


def test_wait_returns_as_soon_as_the_camera_is_at_the_target() -> None:
    clock = FakeClock()
    monitor = _monitor(clock)
    path = iter([-41.0, -55.0, -70.0, -79.0])

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        monitor.orientation_callback(_orientation(next(path)))

    monitor.orientation_callback(_orientation(-40.0))
    asserts: List[float] = []
    outcome = monitor.wait_for_tilt(-80.0, 3.0, 3.0, lambda: asserts.append(clock.now), lambda: False, 0.25, sleep=sleep)
    assert outcome.reached and outcome.telemetry and not outcome.interrupted
    assert outcome.actual == pytest.approx(-79.0)
    assert outcome.waited == pytest.approx(1.0)
    assert len(asserts) == 4


def test_wait_reports_a_camera_that_stops_short() -> None:
    clock = FakeClock()
    monitor = _monitor(clock)
    monitor.limits_callback(_limits(-83.0, 17.0, -35.0, 35.0))
    monitor.orientation_callback(_orientation(-52.0))

    def refresh(seconds: float) -> None:
        clock.sleep(seconds)
        monitor.orientation_callback(_orientation(-52.0))

    asserts: List[float] = []
    outcome = monitor.wait_for_tilt(-80.0, 3.0, 3.0, lambda: asserts.append(clock.now), lambda: False, 0.25, sleep=refresh)
    assert not outcome.reached and outcome.telemetry
    assert outcome.actual == pytest.approx(-52.0)
    assert outcome.waited == pytest.approx(3.0)
    assert len(asserts) == 12


def test_wait_without_telemetry_returns_at_once() -> None:
    clock = FakeClock()
    asserts: List[float] = []
    outcome = _wait(_monitor(clock), clock, asserts)
    assert not outcome.reached and not outcome.telemetry
    assert outcome.actual is None and outcome.waited == 0.0
    assert clock.sleeps == [] and asserts == []


def test_wait_stops_when_the_step_is_interrupted() -> None:
    clock = FakeClock()
    monitor = _monitor(clock)
    monitor.orientation_callback(_orientation(-40.0))
    calls = {"n": 0}

    def interrupted() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        monitor.orientation_callback(_orientation(-40.0))

    asserts: List[float] = []
    outcome = monitor.wait_for_tilt(-80.0, 3.0, 3.0, lambda: asserts.append(clock.now), interrupted, 0.25, sleep=sleep)
    assert outcome.interrupted and not outcome.reached
    assert outcome.waited < 3.0


def test_a_failing_assert_does_not_end_the_wait() -> None:
    clock = FakeClock()
    monitor = _monitor(clock)
    monitor.orientation_callback(_orientation(-79.0))

    def boom() -> None:
        raise RuntimeError("publisher gone")

    outcome = monitor.wait_for_tilt(-80.0, 3.0, 3.0, boom, lambda: False, 0.25, sleep=clock.sleep)
    assert outcome.reached


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"stale_timeout_sec": 0.0}, ValueError),
        ({"stale_timeout_sec": "2"}, TypeError),
        ({"clock": 3}, TypeError),
    ],
)
def test_constructor_rejects_bad_arguments(kwargs, error) -> None:
    with pytest.raises(error):
        GimbalStateMonitor(**kwargs)


@pytest.mark.parametrize(
    "args, error",
    [
        ((math.nan, 3.0, 3.0), ValueError),
        ((-80.0, -1.0, 3.0), ValueError),
        ((-80.0, 3.0, 0.0), ValueError),
        (("x", 3.0, 3.0), TypeError),
    ],
)
def test_wait_rejects_bad_arguments(args, error) -> None:
    monitor = _monitor(FakeClock())
    with pytest.raises(error):
        monitor.wait_for_tilt(*args, lambda: None, lambda: False, 0.25)


def test_outcome_describes_itself_for_the_log() -> None:
    assert "-79.0" in GimbalOutcome(True, -79.0, 1.0, True, False).describe(-80.0)
    assert "no gimbal telemetry" in GimbalOutcome(False, None, 0.0, False, False).describe(-80.0)
    assert "-52.0" in GimbalOutcome(False, -52.0, 3.0, True, False).describe(-80.0)
