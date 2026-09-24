"""Unit tests for loop pacing and deadline primitives."""

import pytest

from mvp_mission_bebop.engine.rate import (
    MAX_INTERVAL_SEC,
    MIN_INTERVAL_SEC,
    Deadline,
    LoopRate,
)


class FakeClock:
    """Manually advanced clock; sleeping advances it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def test_rejects_invalid_frequency():
    with pytest.raises(ValueError):
        LoopRate(0.0)
    with pytest.raises(ValueError):
        LoopRate(-5.0)


def test_paces_to_the_target_period():
    clock = FakeClock()
    rate = LoopRate(10.0, clock=clock, sleeper=clock.sleep)

    for _ in range(5):
        clock.advance(0.02)          # loop body cost
        dt = rate.tick()
        assert dt == pytest.approx(0.10)

    assert rate.overruns == 0
    assert rate.ticks == 5


def test_overrun_reports_the_real_interval_and_does_not_sleep():
    clock = FakeClock()
    rate = LoopRate(10.0, clock=clock, sleeper=clock.sleep)

    clock.advance(0.25)              # body overran the 0.1 s period
    dt = rate.tick()

    assert dt == pytest.approx(0.25)
    assert rate.overruns == 1


def test_interval_is_clamped_for_derivative_safety():
    clock = FakeClock()
    rate = LoopRate(10.0, clock=clock, sleeper=clock.sleep)

    # A blocking frame grab must not hand a controller a multi-second dt.
    clock.advance(30.0)
    assert rate.tick() == pytest.approx(MAX_INTERVAL_SEC)


def test_clamp_interval_bounds_both_ends():
    assert LoopRate.clamp_interval(0.0) == MIN_INTERVAL_SEC
    assert LoopRate.clamp_interval(-1.0) == MIN_INTERVAL_SEC
    assert LoopRate.clamp_interval(999.0) == MAX_INTERVAL_SEC
    assert LoopRate.clamp_interval(0.05) == pytest.approx(0.05)


def test_period_and_frequency_agree():
    rate = LoopRate(20.0)
    assert rate.period_sec == pytest.approx(0.05)
    assert rate.frequency_hz == pytest.approx(20.0)


def test_deadline_counts_down():
    clock = FakeClock()
    deadline = Deadline(5.0, clock=clock)

    assert deadline.active and not deadline.expired
    assert deadline.remaining_sec == pytest.approx(5.0)

    clock.advance(3.0)
    assert deadline.elapsed_sec == pytest.approx(3.0)
    assert deadline.remaining_sec == pytest.approx(2.0)
    assert deadline.active

    clock.advance(2.0)
    assert deadline.expired
    assert deadline.remaining_sec == 0.0   # floored, never negative


def test_zero_duration_deadline_is_immediately_expired():
    # The old RTL referenced a loop-local variable in its timeout branch, so a
    # zero-length window raised UnboundLocalError instead of ending cleanly.
    assert Deadline(0.0).expired


def test_deadline_rejects_negative_duration():
    with pytest.raises(ValueError):
        Deadline(-1.0)


def test_deadline_reset_restarts_the_countdown():
    clock = FakeClock()
    deadline = Deadline(2.0, clock=clock)
    clock.advance(3.0)
    assert deadline.expired
    deadline.reset()
    assert deadline.active


def test_the_cadence_report_counts_overruns():
    clock = {"now": 0.0}
    rate = LoopRate(
        10.0,
        clock=lambda: clock["now"],
        sleeper=lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )
    rate.tick()
    clock["now"] += 0.5
    rate.tick()

    assert rate.cadence_report() == "2 cycles at 10.0 Hz target, 1 overruns (50.0%)"


def test_the_cadence_report_before_any_tick():
    assert LoopRate(15.0).cadence_report() == "0 cycles at 15.0 Hz target, 0 overruns (0.0%)"
