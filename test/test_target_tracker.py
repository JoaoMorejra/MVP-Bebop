"""Unit tests for alpha-beta visual target tracking."""

import pytest

from mvp_mission_bebop.estimation.target_tracker import (
    ConstantVelocityTracker,
    TrackerGains,
)

DT = 1.0 / 15.0


def test_rejects_invalid_gains():
    with pytest.raises(ValueError):
        TrackerGains(alpha=0.0, beta=0.2)
    with pytest.raises(ValueError):
        TrackerGains(alpha=1.5, beta=0.2)
    with pytest.raises(ValueError):
        TrackerGains(alpha=0.6, beta=-0.1)


def test_rejects_invalid_coast_horizon():
    with pytest.raises(ValueError):
        ConstantVelocityTracker(max_coast_sec=0.0)


def test_uninitialised_tracker_cannot_coast():
    tracker = ConstantVelocityTracker()
    assert not tracker.initialized
    assert tracker.coast(DT) is None


def test_first_measurement_seeds_position_with_zero_velocity():
    tracker = ConstantVelocityTracker()
    estimate = tracker.update((320.0, 240.0), DT)
    assert estimate.center == pytest.approx((320.0, 240.0))
    assert estimate.speed == pytest.approx(0.0)
    assert tracker.initialized


def test_converges_on_a_constant_velocity_track():
    tracker = ConstantVelocityTracker()
    pixels_per_frame = 6.0
    for index in range(40):
        tracker.update((300.0 + index * pixels_per_frame, 240.0), DT)

    estimate = tracker.update((300.0 + 40 * pixels_per_frame, 240.0), DT)
    assert estimate.vx == pytest.approx(pixels_per_frame / DT, rel=0.05)
    assert estimate.vy == pytest.approx(0.0, abs=1.0)


def test_coasting_extrapolates_through_a_dropout():
    tracker = ConstantVelocityTracker()
    pixels_per_frame = 6.0
    for index in range(30):
        tracker.update((300.0 + index * pixels_per_frame, 240.0), DT)

    last_x = 300.0 + 29 * pixels_per_frame
    first = tracker.coast(DT)
    second = tracker.coast(DT)

    assert first.trustworthy and second.trustworthy
    assert second.x == pytest.approx(last_x + 2 * pixels_per_frame, rel=0.02)
    assert second.coast_sec == pytest.approx(2 * DT)


def test_coasting_past_the_horizon_is_flagged_untrustworthy():
    tracker = ConstantVelocityTracker(max_coast_sec=0.5)
    for _ in range(10):
        tracker.update((320.0, 240.0), DT)

    estimate = None
    for _ in range(20):
        estimate = tracker.coast(DT)

    assert estimate.coast_sec > 0.5
    assert not estimate.trustworthy


def test_a_measurement_clears_the_coast_counter():
    tracker = ConstantVelocityTracker()
    tracker.update((320.0, 240.0), DT)
    tracker.coast(DT)
    tracker.coast(DT)
    assert tracker.coast_sec > 0.0

    estimate = tracker.update((322.0, 240.0), DT)
    assert estimate.coast_sec == 0.0
    assert estimate.trustworthy


def test_smoothing_attenuates_measurement_jitter():
    """A stationary target with alternating noise must not be tracked as moving."""
    tracker = ConstantVelocityTracker(TrackerGains(alpha=0.35, beta=0.05))
    estimate = None
    for index in range(60):
        jitter = 8.0 if index % 2 == 0 else -8.0
        estimate = tracker.update((320.0 + jitter, 240.0), DT)

    assert estimate.x == pytest.approx(320.0, abs=8.0)
    assert abs(estimate.vx) < 8.0 / DT


def test_reset_forgets_the_track():
    tracker = ConstantVelocityTracker()
    tracker.update((320.0, 240.0), DT)
    tracker.reset()
    assert not tracker.initialized
    assert tracker.coast(DT) is None
