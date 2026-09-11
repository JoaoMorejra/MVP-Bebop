"""Unit tests for normalized-command to metres-per-second calibration."""

import pytest

from mvp_mission_bebop.estimation.calibration import SpeedCalibration, SpeedGainEstimator


def test_rejects_non_positive_gain():
    with pytest.raises(ValueError):
        SpeedCalibration(normalized_to_mps=0.0)
    with pytest.raises(ValueError):
        SpeedCalibration(normalized_to_mps=-1.0)


def test_conversions_are_inverses():
    calibration = SpeedCalibration(normalized_to_mps=2.5)
    assert calibration.to_mps(0.10) == pytest.approx(0.25)
    assert calibration.to_normalized(0.25) == pytest.approx(0.10)
    assert calibration.to_normalized(calibration.to_mps(0.07)) == pytest.approx(0.07)


def test_identity_calibration_is_detected():
    assert SpeedCalibration(normalized_to_mps=1.0).is_identity
    assert not SpeedCalibration(normalized_to_mps=1.4).is_identity


def test_identity_calibration_reproduces_historical_behaviour():
    """The default must be a no-op so an uncalibrated build flies as before."""
    calibration = SpeedCalibration(normalized_to_mps=1.0)
    for value in (0.0, 0.035, 0.10, -0.22):
        assert calibration.to_mps(value) == pytest.approx(value)
        assert calibration.to_normalized(value) == pytest.approx(value)


def test_estimator_recovers_a_known_gain():
    estimator = SpeedGainEstimator()
    true_gain = 1.8
    for index in range(40):
        command = 0.05 + 0.002 * index
        estimator.observe(command, command * true_gain)

    result = estimator.estimate()
    assert result is not None
    gain, sigma = result
    assert gain == pytest.approx(true_gain, rel=0.01)
    assert sigma == pytest.approx(0.0, abs=1e-6)


def test_estimator_ignores_low_authority_commands():
    estimator = SpeedGainEstimator()
    for _ in range(50):
        estimator.observe(0.005, 0.009)
    assert estimator.sample_count == 0
    assert estimator.estimate() is None


def test_estimator_needs_enough_samples():
    estimator = SpeedGainEstimator()
    for _ in range(5):
        estimator.observe(0.10, 0.18)
    assert estimator.estimate() is None


def test_estimator_rejects_implausible_gains():
    """A blocked drone reports near-zero speed under full command."""
    estimator = SpeedGainEstimator()
    for _ in range(30):
        estimator.observe(0.20, 0.0001)
    assert estimator.estimate() is None


def test_estimator_respects_capacity():
    estimator = SpeedGainEstimator(capacity=10)
    for index in range(50):
        estimator.observe(0.10, 0.18 + index * 0.0001)
    assert estimator.sample_count == 10


def test_report_is_informative_in_both_states():
    estimator = SpeedGainEstimator()
    assert "not identifiable" in estimator.report()
    for _ in range(30):
        estimator.observe(0.10, 0.15)
    assert "normalized_to_mps" in estimator.report()
