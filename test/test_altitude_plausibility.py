"""Unit tests for the sonar false-climb plausibility gate."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.estimation.altitude_plausibility import (
    AltitudePlausibilityFilter,
    PlausibilityLimits,
)

LIMITS = PlausibilityLimits(max_speed_mps=0.40, max_accel_mps2=0.40, reject_streak=3)
DT = 1.0 / 15.0


def test_rejects_invalid_limits():
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.0, max_accel_mps2=0.4)
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.4, max_accel_mps2=0.0)
    with pytest.raises(ValueError):
        PlausibilityLimits(max_speed_mps=0.4, max_accel_mps2=0.4, reject_streak=0)


def test_a_smooth_climb_passes_through_unchanged():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.0)
    altitude = 1.0
    for _ in range(30):
        altitude += 0.05 * DT  # 5 cm/s, well inside the envelope
        trusted = filt.update(altitude, DT)
        assert trusted == pytest.approx(altitude)


def test_a_sonar_dropout_spike_is_held_at_the_last_trusted_value():
    """The failure this filter exists for: one obstacle-shortened range sample."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    trusted = filt.update(1.55, DT)
    assert trusted == pytest.approx(1.55)

    # A jump of 1.2 m in one 1/15 s cycle is not achievable at 0.40 m/s^2.
    spike = filt.update(2.75, DT)
    assert spike == pytest.approx(1.55), "a single-cycle spike must be rejected"


def test_a_single_spike_does_not_start_a_permanent_rejection():
    """One outlier only. The next plausible sample is trusted normally."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    filt.update(2.75, DT)
    recovered = filt.update(1.56, DT)
    assert recovered == pytest.approx(1.56)


def test_a_sustained_new_regime_is_eventually_accepted():
    """The drone actually descending onto a landing pad, not a sonar artefact."""
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    trusted = 1.55
    for _ in range(LIMITS.reject_streak):
        trusted = filt.update(0.05, DT)
    assert trusted == pytest.approx(0.05), "a sustained run must be accepted as real"


def test_non_finite_sample_is_ignored():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    assert filt.update(math.nan, DT) == pytest.approx(1.55)
    assert filt.last_trusted == pytest.approx(1.55)


def test_zero_or_negative_dt_is_ignored():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    assert filt.update(1.90, 0.0) == pytest.approx(1.55)
    assert filt.update(1.90, -0.01) == pytest.approx(1.55)


def test_reset_reseeds_the_trusted_value():
    filt = AltitudePlausibilityFilter(LIMITS, initial_altitude=1.55)
    filt.update(2.75, DT)
    filt.reset(0.10)
    assert filt.last_trusted == pytest.approx(0.10)
    assert filt.update(0.11, DT) == pytest.approx(0.11)
