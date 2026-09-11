"""Unit tests for odometry ingestion, calibration, and body-frame projection."""

import math
import statistics

import pytest

from mvp_mission_bebop.parameters import (
    CalibrationConfig,
    FlightKinematicsConfig,
    TimeoutsConfig,
)
from mvp_mission_bebop.telemetry.odometry import (
    OdometrySupervisor,
    TelemetryHealth,
    robust_center,
)


def supervisor(**calibration_overrides):
    return OdometrySupervisor(
        FlightKinematicsConfig(target_altitude_m=1.0, altitude_ceiling_margin_m=0.25),
        TimeoutsConfig(),
        CalibrationConfig(**calibration_overrides),
    )


def feed(sup, count, *, z=1.0, x=0.0, y=0.0):
    for _ in range(count):
        sup.inject_synthetic_sample(x=x, y=y, z=z)


# --------------------------------------------------------------- robust_center


def test_robust_center_rejects_an_outlier_that_wrecks_the_mean():
    clean = [1.00, 1.01, 0.99, 1.00, 1.02, 0.98, 1.01, 0.99, 1.00, 1.01, 1.00, 0.99]
    poisoned = clean + [9.5]

    center, _ = robust_center(poisoned)
    assert center == pytest.approx(1.0, abs=0.02)
    # The plain arithmetic mean the previous implementation used is far off.
    assert statistics.fmean(poisoned) > 1.6


def test_robust_center_handles_degenerate_input():
    assert robust_center([2.5]) == (2.5, 0.0)
    center, sigma = robust_center([3.0, 3.0, 3.0, 3.0])
    assert center == pytest.approx(3.0)
    assert sigma == 0.0


def test_robust_center_requires_samples():
    with pytest.raises(ValueError):
        robust_center([])


# -------------------------------------------------------------------- health


def test_never_received_is_distinct_from_stale():
    """The defect this replaces: 'no odometry ever arrived' read as healthy.

    A ``/bebop/odom`` topic that never started used to pass every health gate,
    so the RTL flew a closed loop against a position that was structurally zero.
    """
    sup = supervisor()
    assert sup.telemetry_health() is TelemetryHealth.NEVER_RECEIVED
    assert not sup.is_telemetry_healthy()

    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    assert sup.telemetry_health() is TelemetryHealth.HEALTHY
    assert sup.is_telemetry_healthy()


def test_stale_stream_is_reported():
    sup = supervisor()
    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    assert sup.telemetry_health(timeout_sec=-1.0) is TelemetryHealth.STALE


def test_health_enum_exposes_a_single_healthy_state():
    assert TelemetryHealth.HEALTHY.is_healthy
    assert not TelemetryHealth.STALE.is_healthy
    assert not TelemetryHealth.NEVER_RECEIVED.is_healthy


# --------------------------------------------------------------- calibration


def test_calibration_is_refused_without_samples():
    sup = supervisor()
    assert sup.calibrate_ground_reference() is False
    assert sup.ground_reference_altitude is None


def test_calibration_succeeds_and_rejects_outliers():
    sup = supervisor(min_ground_samples=10)
    for value in [1.00, 1.01, 0.99, 1.00, 1.02, 0.98, 1.01, 0.99, 1.00, 1.01, 1.00, 9.5]:
        sup.inject_synthetic_sample(x=0.0, y=0.0, z=value)

    assert sup.calibrate_ground_reference() is True
    assert sup.ground_reference_altitude == pytest.approx(1.0, abs=0.02)


def test_calibration_is_refused_when_the_surface_is_not_level():
    sup = supervisor(min_ground_samples=10, max_ground_dispersion_m=0.05)
    for index in range(20):
        sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0 + 0.30 * (index % 2))

    assert sup.calibrate_ground_reference() is False
    assert sup.ground_reference_altitude is None


def test_calibration_falls_back_on_a_short_buffer():
    sup = supervisor(min_ground_samples=50)
    feed(sup, 3, z=1.25)
    assert sup.calibrate_ground_reference() is True
    assert sup.ground_reference_altitude == pytest.approx(1.25)


def test_buffer_stops_growing_once_calibrated():
    sup = supervisor(min_ground_samples=5, sample_buffer_size=8)
    feed(sup, 40, z=1.0)
    assert sup.calibrate_ground_reference() is True
    before = sup.snapshot().sample_count
    feed(sup, 10, z=5.0)
    # Post-calibration samples update state but must not re-enter the buffer,
    # so the frozen ground reference cannot drift.
    assert sup.ground_reference_altitude == pytest.approx(1.0)
    assert sup.snapshot().sample_count == before + 10


def test_relative_altitude_is_measured_against_the_reference():
    sup = supervisor(min_ground_samples=5)
    feed(sup, 10, z=0.40)
    assert sup.calibrate_ground_reference() is True

    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.35)
    assert sup.snapshot().relative_altitude == pytest.approx(0.95)


def test_hover_origin_is_frozen_separately_from_the_ground_reference():
    """Ground z0 must be measured on the ground, the horizontal origin in hover.

    Freezing both together bakes the lift-off transient into the coordinate the
    drone later returns to.
    """
    sup = supervisor(min_ground_samples=5)
    feed(sup, 10, z=0.0, x=0.0, y=0.0)
    sup.calibrate_ground_reference()

    sup.inject_synthetic_sample(x=0.12, y=-0.08, z=1.0)
    sup.freeze_hover_takeoff_origin()

    snap = sup.snapshot()
    assert snap.takeoff_x == pytest.approx(0.12)
    assert snap.takeoff_y == pytest.approx(-0.08)
    assert snap.ground_reference == pytest.approx(0.0)


# ------------------------------------------------------------------ snapshot


def test_snapshot_is_immutable():
    sup = supervisor()
    sup.inject_synthetic_sample(x=1.0, y=2.0, z=3.0)
    snap = sup.snapshot()
    with pytest.raises(Exception):
        snap.x = 9.0


def test_snapshot_derives_speed_from_one_consistent_sample():
    sup = supervisor()
    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0, vx=0.30, vy=0.40, vz=0.50)
    snap = sup.snapshot()
    assert snap.horizontal_speed == pytest.approx(0.5)
    assert snap.speed == pytest.approx(math.sqrt(0.5))


def test_body_frame_error_without_an_origin_is_zero():
    sup = supervisor()
    sup.inject_synthetic_sample(x=5.0, y=5.0, z=1.0)
    assert sup.snapshot().body_frame_launch_error() == (0.0, 0.0, 0.0)
    assert not sup.snapshot().has_launch_origin


def test_body_frame_error_points_backward_after_flying_forward():
    sup = supervisor()
    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    sup.freeze_hover_takeoff_origin()
    sup.inject_synthetic_sample(x=2.0, y=0.5, z=1.0, yaw=0.0)

    ex, ey, distance = sup.snapshot().body_frame_launch_error()
    assert ex == pytest.approx(-2.0)      # origin is behind: fly backward
    assert ey == pytest.approx(-0.5)      # origin is to the right
    assert distance == pytest.approx(math.hypot(2.0, 0.5))


def test_body_frame_error_rotates_with_yaw():
    sup = supervisor()
    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    sup.freeze_hover_takeoff_origin()
    # Displaced along +x, but nose turned 90 deg: the origin is now to starboard.
    sup.inject_synthetic_sample(x=2.0, y=0.0, z=1.0, yaw=math.pi / 2.0)

    ex, ey, _ = sup.snapshot().body_frame_launch_error()
    assert ex == pytest.approx(0.0, abs=1e-9)
    assert ey == pytest.approx(2.0)


# ------------------------------------------------------------------- ceiling


def test_ceiling_tracks_a_changed_target_altitude():
    """Derived on read, so a target altitude changed later is not ignored."""
    sup = supervisor()
    assert sup.altitude_ceiling == pytest.approx(1.25)
    sup.kinematics_cfg.target_altitude_m = 2.0
    assert sup.altitude_ceiling == pytest.approx(2.25)


def test_ceiling_breach_is_detected():
    sup = supervisor(min_ground_samples=5)
    feed(sup, 10, z=0.0)
    sup.calibrate_ground_reference()

    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.10)
    assert not sup.is_ceiling_breached()
    sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.40)
    assert sup.is_ceiling_breached()
