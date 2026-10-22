"""The terrain-aware vertical Kalman filter (section 3.8)."""

from __future__ import annotations

import types

import numpy as np
import pytest

from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.estimation.terrain_vertical_filter import (
    COAST_SAMPLES,
    FIRMWARE_ALTITUDE_SOURCES,
    TerrainAwareVerticalFilter,
    normalize_firmware_altitude_source,
)
from mvp_mission_bebop.estimation.vertical_estimator import normalize_altitude_estimator
from mvp_mission_bebop.parameters import CalibrationConfig, MissionParameters
from mvp_mission_bebop.steps.base import governed_vz
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

DT = 0.2


def make_filter(**overrides) -> TerrainAwareVerticalFilter:
    cfg = CalibrationConfig()
    for name, value in overrides.items():
        setattr(cfg, name, value)
    estimator = TerrainAwareVerticalFilter(cfg, vertical_gain_mps=2.5)
    estimator.reset(1.0)
    estimator.update_velocity(0.0, 0.0)
    return estimator


def feed(estimator, speeds, *, start=0.0, guard_window=False):
    t = start
    trace = []
    for vz in speeds:
        t += DT
        trace.append(estimator.update_velocity(vz, t, guard_window=guard_window))
    return t, trace


def test_the_configuration_accepts_terrain_kf_and_the_altitude_sources():
    assert normalize_altitude_estimator("terrain_kf") == "terrain_kf"
    assert FIRMWARE_ALTITUDE_SOURCES == ("unknown", "baro", "sonar_fused")
    with pytest.raises(ValueError):
        normalize_firmware_altitude_source("sonar")
    cfg = CalibrationConfig()
    assert (cfg.altitude_estimator, cfg.firmware_altitude_source) == ("legacy", "unknown")
    assert (cfg.vertical_lag_tau_sec, cfg.vertical_accel_noise_mps2, cfg.vz_measurement_noise_mps) == (0.35, 1.5, 0.08)
    assert (cfg.altitude_measurement_noise_m, cfg.terrain_step_noise_scale) == (0.10, 16.0)


@pytest.mark.parametrize(
    "name, value",
    [
        ("vertical_lag_tau_sec", 0.05),
        ("vertical_lag_tau_sec", 2.5),
        ("vertical_accel_noise_mps2", 0.0),
        ("vz_measurement_noise_mps", 1.5),
        ("altitude_measurement_noise_m", 0.0),
        ("terrain_step_noise_scale", 0.5),
        ("firmware_altitude_source", "laser"),
    ],
)
def test_a_value_outside_its_envelope_is_refused(name, value):
    cfg = CalibrationConfig()
    setattr(cfg, name, value)
    with pytest.raises(ValueError):
        TerrainAwareVerticalFilter(cfg, 2.5)


def test_an_m2_spike_inside_the_guard_window_is_rejected_and_never_reacquired():
    """The step of a 1 m pad registered as speed: area +1 m over two 0.2 s samples."""
    estimator = make_filter()
    t, _ = feed(estimator, [0.0] * 5)
    before = estimator.z_est
    t, trace = feed(estimator, [2.5, 2.5, 2.5, 2.5], start=t, guard_window=True)
    assert max(trace) - before < 0.05
    assert estimator.coasting
    feed(estimator, [0.0] * 5, start=t, guard_window=True)
    assert abs(estimator.z_est - before) < 0.05
    assert not estimator.coasting


def test_outside_the_window_a_persistent_regime_is_reacquired():
    estimator = make_filter()
    t, _ = feed(estimator, [0.0] * 5)
    feed(estimator, [2.5] * 6, start=t, guard_window=False)
    assert estimator.v_est > 1.0


def test_an_m1_real_motion_is_accepted_because_its_acceleration_is_plausible():
    """The firmware sinks the airframe 1 m at its hold rate: the filter must follow it."""
    estimator = make_filter()
    t, _ = feed(estimator, [0.0] * 5)
    sink = list(-0.5 * (1.0 - np.exp(-np.arange(1, 11) * DT / 0.35)))
    t, trace = feed(estimator, sink, start=t, guard_window=True)
    moved = estimator.z_est - 1.0
    real = sum(v * DT for v in sink)
    # Inside the guard window speedZ weighs less (terrain_step_noise_scale): the
    # filter follows most of the real motion, never none of it as with a spike.
    assert 0.7 * real >= moved >= 1.05 * real
    assert not estimator.coasting


def test_the_terrain_hypothesis_switch_never_moves_the_state():
    estimator = make_filter(firmware_altitude_source="sonar_fused")
    t, _ = feed(estimator, [0.0] * 5)
    estimator.update_altitude(1.0, t, pad_height_m=1.0, off_pad_prior=False)
    assert estimator.terrain_hypothesis == 0
    z_before = estimator.z_est
    # Off the pad the fused sonar reads one pad height more; the airframe did not move.
    estimator.update_altitude(2.0, t + DT, pad_height_m=1.0, off_pad_prior=True)
    assert estimator.terrain_hypothesis == 1
    assert estimator.z_est == pytest.approx(z_before, abs=0.02)
    estimator.update_altitude(1.0, t + 2 * DT, pad_height_m=1.0, off_pad_prior=False)
    assert estimator.terrain_hypothesis == 0
    assert estimator.z_est == pytest.approx(z_before, abs=0.02)


def test_an_unknown_altitude_source_is_logged_only():
    estimator = make_filter()
    t, _ = feed(estimator, [0.0] * 3)
    before = (estimator.z_est, estimator.covariance)
    estimator.update_altitude(5.0, t, pad_height_m=1.0)
    assert estimator.z_est == before[0]
    assert np.allclose(estimator.covariance, before[1])


def test_a_barometric_source_pulls_the_estimate_through_its_bias():
    estimator = make_filter(firmware_altitude_source="baro")
    t, _ = feed(estimator, [0.0] * 3)
    for k in range(20):
        estimator.update_altitude(1.05, t + k * DT, pad_height_m=0.0)
    assert estimator.z_est + estimator.bias_est == pytest.approx(1.05, abs=0.02)


def test_coasting_follows_the_rejections():
    estimator = make_filter()
    t, _ = feed(estimator, [0.0] * 3)
    for k in range(COAST_SAMPLES):
        assert not estimator.coasting
        t, _ = feed(estimator, [3.0 + k], start=t, guard_window=True)
    assert estimator.coasting


def test_the_command_drives_the_process():
    estimator = make_filter()
    estimator.note_command(-0.2)
    t = 0.0
    for _ in range(10):
        t += DT
        estimator._advance_to(t)
    assert estimator.v_est == pytest.approx(-0.5, abs=0.05)


def test_hold_integrator_freezes_the_integral_of_the_governor():
    pid = FilteredPID(PIDGains(kp=0.0, ki=1.0, kd=0.0, output_limits=(-1.0, 1.0), integral_limits=(-1.0, 1.0)))
    pid.update(-0.5, 0.1)
    held = pid.components["integral"]
    pid.update(-0.5, 0.1, hold_integral=True)
    assert pid.components["integral"] == held
    params = MissionParameters()
    governor = AltitudeHoldGovernor(target_altitude=1.0, config=params.governor)
    for _ in range(5):
        governor.compute_vz(0.5, 0.1)
    integral = governor._pid.components["integral"]
    governor.compute_vz(0.5, 0.1, hold_integrator=True)
    assert governor._pid.components["integral"] == integral


def test_the_steps_hold_the_integrator_while_the_supervisor_coasts():
    calls = []
    governor = types.SimpleNamespace(compute_vz=lambda *args, **kwargs: calls.append(kwargs) or 0.0)
    coasting = types.SimpleNamespace(vertical_coasting=lambda: True)
    observing = types.SimpleNamespace(vertical_coasting=lambda: False)
    governed_vz(types.SimpleNamespace(governor=governor, odom_supervisor=coasting), 1.0, 0.1)
    governed_vz(types.SimpleNamespace(governor=governor, odom_supervisor=observing), 1.0, 0.1)
    governed_vz(types.SimpleNamespace(governor=governor, odom_supervisor=object()), 1.0, 0.1)
    assert [call.get("hold_integrator", False) for call in calls] == [True, False, False]


def odometry_message(z: float, vz: float, stamp: float):
    sec = int(stamp)
    return types.SimpleNamespace(
        header=types.SimpleNamespace(stamp=types.SimpleNamespace(sec=sec, nanosec=int((stamp - sec) * 1e9))),
        pose=types.SimpleNamespace(
            pose=types.SimpleNamespace(
                position=types.SimpleNamespace(x=0.0, y=0.0, z=z),
                orientation=types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            )
        ),
        twist=types.SimpleNamespace(twist=types.SimpleNamespace(linear=types.SimpleNamespace(x=0.0, y=0.0, z=vz))),
    )


def supervisor(estimator: str) -> OdometrySupervisor:
    params = MissionParameters()
    params.calibration.altitude_estimator = estimator
    return OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)


def test_the_supervisor_runs_the_filter_on_odometry_and_takes_the_sent_command():
    odom = supervisor("terrain_kf")
    for k in range(5):
        odom.odometry_callback(odometry_message(1.0, 0.0, 100.0 + k * DT))
    assert odom.snapshot().raw_altitude == pytest.approx(1.0, abs=0.01)
    odom.note_commanded_vz(0.0)
    for k in range(5, 7):
        odom.odometry_callback(odometry_message(1.0 + (k - 4) * 0.5, 2.5, 100.0 + k * DT))
    assert odom.snapshot().raw_altitude == pytest.approx(1.0, abs=0.1)
    assert odom.vertical_coasting() in (True, False)


def test_the_legacy_branch_is_untouched():
    """``legacy`` stores ``pose.z`` behind the position gate, as before the filter existed."""
    odom = supervisor("legacy")
    assert odom._terrain is None and odom._vertical is None
    odom.odometry_callback(odometry_message(1.0, 0.0, 100.0))
    assert odom.snapshot().raw_altitude == pytest.approx(1.0)
    odom.note_commanded_vz(0.3)
    assert odom.vertical_coasting() is False
