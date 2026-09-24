"""Unit tests for multivariate settlement detection."""

import math

import pytest

from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector

CRITERIA = SettlementCriteria(
    window_sec=1.0,
    min_samples=5,
    max_speed=0.05,
    max_position_sigma=0.06,
    max_distance=0.20,
)
STEP = 0.066


def drive(detector, generator, count=40):
    report = None
    timestamp = 0.0
    for index in range(count):
        x, y, speed, distance = generator(index)
        report = detector.update(
            x=x, y=y, speed=speed, distance=distance, timestamp=timestamp
        )
        timestamp += STEP
    return report


def test_rejects_invalid_criteria():
    with pytest.raises(ValueError):
        SettlementCriteria(window_sec=0.0, min_samples=5, max_speed=0.05, max_position_sigma=0.06)
    with pytest.raises(ValueError):
        SettlementCriteria(window_sec=1.0, min_samples=1, max_speed=0.05, max_position_sigma=0.06)


def test_stationary_vehicle_settles():
    report = drive(SettlementDetector(CRITERIA), lambda i: (0.01, -0.005, 0.01, 0.012))
    assert report.settled, report.reason


def test_orbiting_inside_the_radius_does_not_settle():
    """The defect the consecutive-cycle counter could not see.

    A drone circling within the arrival radius keeps every distance sample
    inside tolerance, so a counter that only checks distance increments happily.
    Positional variance is what separates orbiting from stopping.
    """
    report = drive(
        SettlementDetector(CRITERIA),
        lambda i: (0.15 * math.cos(i * 0.4), 0.15 * math.sin(i * 0.4), 0.30, 0.15),
    )
    assert not report.settled
    assert "sigma" in report.reason
    assert report.position_sigma > CRITERIA.max_position_sigma


def test_moving_vehicle_does_not_settle():
    report = drive(SettlementDetector(CRITERIA), lambda i: (0.01, -0.005, 0.09, 0.012))
    assert not report.settled
    assert "speed" in report.reason


def test_excursion_outside_the_radius_does_not_settle():
    report = drive(SettlementDetector(CRITERIA), lambda i: (0.40, 0.0, 0.005, 0.40))
    assert not report.settled
    assert "excursion" in report.reason


def test_a_single_excursion_breaks_settlement():
    detector = SettlementDetector(CRITERIA)
    drive(detector, lambda i: (0.01, 0.0, 0.01, 0.01), count=30)
    report = detector.update(x=0.01, y=0.0, speed=0.01, distance=0.5, timestamp=99.0)
    assert not report.settled


def test_requires_a_minimum_sample_count():
    detector = SettlementDetector(CRITERIA)
    report = detector.update(x=0.0, y=0.0, speed=0.0, distance=0.0, timestamp=0.0)
    assert not report.settled
    assert "samples" in report.reason


def test_window_span_must_be_covered():
    """Settlement must not be declared from a burst of samples in a short span."""
    detector = SettlementDetector(CRITERIA)
    report = None
    for index in range(20):
        report = detector.update(
            x=0.0, y=0.0, speed=0.0, distance=0.0, timestamp=index * 0.01
        )
    assert not report.settled
    assert "span" in report.reason


def test_reset_clears_the_window():
    detector = SettlementDetector(CRITERIA)
    drive(detector, lambda i: (0.0, 0.0, 0.0, 0.0))
    assert detector.sample_count > 0
    detector.reset()
    assert detector.sample_count == 0


def test_criteria_without_a_radius_ignores_distance():
    criteria = SettlementCriteria(
        window_sec=1.0, min_samples=5, max_speed=0.05, max_position_sigma=0.06
    )
    report = drive(SettlementDetector(criteria), lambda i: (0.0, 0.0, 0.0, 99.0))
    assert report.settled


def test_vertical_speed_criterion_is_optional_and_defaults_off():
    """Existing callers that never pass `vz` are unaffected."""
    criteria = SettlementCriteria(
        window_sec=1.0, min_samples=5, max_speed=0.05, max_position_sigma=0.06
    )
    report = drive(SettlementDetector(criteria), lambda i: (0.01, -0.005, 0.01, 0.012))
    assert report.settled, report.reason


def test_a_governor_still_actively_climbing_blocks_settlement():
    """The Ponto 3 defect: horizontally still, vertically still correcting."""
    criteria = SettlementCriteria(
        window_sec=1.0,
        min_samples=5,
        max_speed=0.05,
        max_position_sigma=0.06,
        max_vertical_speed=0.02,
    )
    detector = SettlementDetector(criteria)
    report = None
    timestamp = 0.0
    for _ in range(40):
        report = detector.update(
            x=0.01, y=-0.005, speed=0.01, vz=0.06, timestamp=timestamp
        )
        timestamp += STEP
    assert not report.settled
    assert "vertical" in report.reason


def test_settlement_resumes_once_the_governor_stops_correcting():
    criteria = SettlementCriteria(
        window_sec=1.0,
        min_samples=5,
        max_speed=0.05,
        max_position_sigma=0.06,
        max_vertical_speed=0.02,
    )
    detector = SettlementDetector(criteria)
    timestamp = 0.0
    for _ in range(20):
        detector.update(x=0.01, y=-0.005, speed=0.01, vz=0.06, timestamp=timestamp)
        timestamp += STEP
    report = None
    for _ in range(20):
        report = detector.update(x=0.01, y=-0.005, speed=0.01, vz=0.0, timestamp=timestamp)
        timestamp += STEP
    assert report.settled, report.reason
