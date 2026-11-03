"""Vertical state estimation in the vz domain (P1, behind ``calibration.altitude_estimator``).

``/bebop/odom.z`` is not a sensor: the driver integrates the vertical speed
into it. A spike in ``vz`` therefore becomes a permanent step in ``z``, and the
legacy position gate, which holds two samples and adopts the third, adopts the
step. Gating the speed before integrating is what keeps the glitch out of the
integrated state.
"""

from __future__ import annotations

import math
import types

import pytest

from mvp_mission_bebop.estimation.altitude_plausibility import (
    AltitudePlausibilityFilter,
    PlausibilityLimits,
)
from mvp_mission_bebop.estimation.vertical_estimator import VerticalStateEstimator
from mvp_mission_bebop.parameters import CalibrationConfig, MissionParameters
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor, normalize_altitude_estimator

DT = 0.2


def estimator(**overrides) -> VerticalStateEstimator:
    cfg = CalibrationConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    est = VerticalStateEstimator(cfg)
    est.reset(1.0)
    return est


def feed(est: VerticalStateEstimator, speeds, start: float = 0.0):
    t = start
    out = []
    for vz in speeds:
        t += DT
        out.append(est.update_velocity(vz, t))
    return out, t


def test_a_single_vz_spike_leaves_the_estimate_unchanged():
    est = estimator()
    feed(est, [0.0] * 5)
    out, _ = feed(est, [6.0, 0.0, 0.0], start=5 * DT)
    assert out[-1] == pytest.approx(1.0)


def test_a_sustained_regime_is_accepted_after_the_reject_streak():
    est = estimator()
    feed(est, [0.0] * 3)
    out, _ = feed(est, [1.2] * 6, start=3 * DT)
    assert out[1] == pytest.approx(1.0)
    assert out[-1] > 1.0 + 1.2 * DT * 3


def test_a_rejected_sample_keeps_the_previous_speed_not_zero():
    est = estimator()
    feed(est, [0.5] * 4)
    before = est.z_est
    est.update_velocity(6.0, 5 * DT)
    assert est.z_est == pytest.approx(before + 0.5 * DT)


@pytest.mark.parametrize("stamp", [0.6, 0.2])
def test_a_non_increasing_stamp_is_an_absent_sample(stamp):
    est = estimator()
    feed(est, [0.5] * 3)
    before = est.z_est
    assert est.update_velocity(0.5, stamp) == before


def test_the_integration_step_is_capped_like_the_drivers():
    est = estimator()
    est.update_velocity(0.0, 0.1)
    est.update_velocity(1.0, 0.3)
    z = est.z_est
    est.update_velocity(1.0, 5.0)
    assert est.z_est == pytest.approx(z + 1.0 * 0.5)


def test_regression_the_legacy_position_gate_adopts_an_integrated_spike():
    """Defect 1.2: a vz spike is a permanent z step, which the position gate adopts."""
    cfg = CalibrationConfig()
    legacy = AltitudePlausibilityFilter(
        PlausibilityLimits(
            cfg.plausibility_max_speed_mps, cfg.plausibility_max_accel_mps2, cfg.plausibility_reject_streak
        ),
        initial_altitude=1.0,
    )
    est = estimator()
    speeds = [0.0] * 5 + [6.0] + [0.0] * 10
    z_driver = 1.0
    t = 0.0
    for vz in speeds:
        t += DT
        z_driver += vz * DT
        legacy_z = legacy.update(z_driver, DT)
        new_z = est.update_velocity(vz, t)

    assert legacy_z == pytest.approx(1.0 + 6.0 * DT)
    assert new_z == pytest.approx(1.0)


def test_the_anchor_inside_its_gate_converges_with_tau():
    est = estimator(altitude_anchor_tau_sec=6.0, altitude_anchor_gate_m=0.25)
    t = 0.0
    for _ in range(60):
        t += 0.5
        est.update_velocity(0.0, t)
        est.update_anchor(1.2, t)
    residual = 1.2 - est.z_est
    assert residual == pytest.approx(0.2 * math.exp(-29.5 / 6.0), rel=0.05)
    assert est.datum_disagreement is False


def test_the_anchor_outside_its_gate_does_not_correct_and_flags_after_three_seconds():
    est = estimator(altitude_anchor_gate_m=0.25)
    t = 0.0
    flags = []
    for _ in range(10):
        t += 0.5
        est.update_velocity(0.0, t)
        est.update_anchor(2.0, t)
        flags.append(est.datum_disagreement)
    assert est.z_est == pytest.approx(1.0)
    assert flags[4] is False
    assert flags[-1] is True


def test_the_disagreement_clears_when_the_datum_agrees_again():
    est = estimator()
    t = 0.0
    for _ in range(10):
        t += 0.5
        est.update_anchor(2.0, t)
    est.update_anchor(1.05, t + 0.5)
    assert est.datum_disagreement is False


# ------------------------------------------------------------------ supervisor


def odom_msg(z: float, vz: float, stamp: float):
    point = types.SimpleNamespace(x=0.0, y=0.0, z=z)
    return types.SimpleNamespace(
        header=types.SimpleNamespace(
            stamp=types.SimpleNamespace(sec=int(stamp), nanosec=int(round((stamp % 1.0) * 1e9)))
        ),
        pose=types.SimpleNamespace(
            pose=types.SimpleNamespace(position=point, orientation=types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0))
        ),
        twist=types.SimpleNamespace(twist=types.SimpleNamespace(linear=types.SimpleNamespace(x=0.0, y=0.0, z=vz))),
    )


def supervisor(mode: str) -> OdometrySupervisor:
    params = MissionParameters()
    params.calibration.altitude_estimator = mode
    params.calibration.altitude_probe_enabled = False
    return OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)


def test_the_default_estimator_is_terrain_kf():
    assert CalibrationConfig().altitude_estimator == "terrain_kf"
    assert normalize_altitude_estimator("legacy") == "legacy"
    assert normalize_altitude_estimator("vz_gated") == "vz_gated"


@pytest.mark.parametrize("value", ["kalman", "", None, 3])
def test_an_unknown_estimator_is_refused(value):
    with pytest.raises(ValueError):
        normalize_altitude_estimator(value)
    params = MissionParameters()
    params.calibration.altitude_estimator = value
    with pytest.raises(ValueError):
        OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)


def test_vz_gated_integrates_on_the_header_stamp():
    odom = supervisor("vz_gated")
    odom.odometry_callback(odom_msg(z=0.5, vz=0.0, stamp=100.0))
    odom.odometry_callback(odom_msg(z=0.5, vz=0.4, stamp=100.25))
    assert odom.snapshot().raw_altitude == pytest.approx(0.5 + 0.4 * 0.25)


def test_vz_gated_ignores_a_spike_the_driver_integrated_into_pose():
    odom = supervisor("vz_gated")
    z = 0.5
    for k in range(6):
        odom.odometry_callback(odom_msg(z=z, vz=0.0, stamp=100.0 + 0.2 * k))
    z += 6.0 * 0.2
    odom.odometry_callback(odom_msg(z=z, vz=6.0, stamp=101.2))
    for k in range(5):
        odom.odometry_callback(odom_msg(z=z, vz=0.0, stamp=101.4 + 0.2 * k))
    assert odom.snapshot().raw_altitude == pytest.approx(0.5)


def test_vz_gated_falls_back_to_arrival_time_on_a_zero_stamp(monkeypatch):
    from mvp_mission_bebop.telemetry import odometry as odometry_module

    clock = types.SimpleNamespace(now=50.0)
    monkeypatch.setattr(odometry_module, "time", types.SimpleNamespace(monotonic=lambda: clock.now))
    odom = supervisor("vz_gated")
    odom.odometry_callback(odom_msg(z=0.5, vz=0.0, stamp=0.0))
    clock.now += 0.2
    odom.odometry_callback(odom_msg(z=0.5, vz=0.5, stamp=0.0))
    assert odom.snapshot().raw_altitude == pytest.approx(0.5 + 0.5 * 0.2)


def test_legacy_keeps_the_position_gate():
    odom = supervisor("legacy")
    odom.odometry_callback(odom_msg(z=0.5, vz=0.0, stamp=100.0))
    odom.odometry_callback(odom_msg(z=0.5, vz=0.4, stamp=100.25))
    assert odom.snapshot().raw_altitude == pytest.approx(0.5)


def test_synthetic_samples_stay_exempt_from_the_estimator():
    odom = supervisor("vz_gated")
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.3, vz=6.0)
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=1.7, vz=0.0)
    assert odom.snapshot().raw_altitude == pytest.approx(1.7)


@pytest.mark.parametrize("enabled", [True, False])
def test_the_anchor_pulls_the_estimate_only_when_enabled(monkeypatch, enabled):
    from mvp_mission_bebop.telemetry import odometry as odometry_module

    clock = types.SimpleNamespace(now=10.0)
    monkeypatch.setattr(odometry_module, "time", types.SimpleNamespace(monotonic=lambda: clock.now))
    params = MissionParameters()
    params.calibration.altitude_estimator = "vz_gated"
    params.calibration.altitude_anchor_enabled = enabled
    params.calibration.altitude_probe_enabled = False
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    for k in range(params.calibration.min_ground_samples):
        clock.now += 0.2
        odom.odometry_callback(odom_msg(z=0.2, vz=0.0, stamp=100.0 + 0.2 * k))
    assert odom.calibrate_ground_reference()

    for k in range(40):
        clock.now += 0.5
        odom.odometry_callback(odom_msg(z=0.2, vz=0.0, stamp=103.0 + 0.5 * k))
        odom.altitude_state_callback(types.SimpleNamespace(data=0.1))

    relative = odom.snapshot().relative_altitude
    if enabled:
        assert relative == pytest.approx(0.1, abs=0.02)
    else:
        assert relative == pytest.approx(0.0, abs=1e-9)
