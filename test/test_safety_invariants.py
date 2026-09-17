"""Regression tests for the pre-flight safety audit findings.

Each test here corresponds to a defect that was reachable in flight and that
failed *silently* -- no exception, no warning, just a control loop quietly doing
the wrong thing. Silence is what makes them worth pinning: none of them would
have been caught by watching the mission run.
"""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker, TrackerGains
from mvp_mission_bebop.parameters import FlightKinematicsConfig, MissionParameters

DT = 1.0 / 15.0


# ------------------------------------------------------- PID non-finite input


def rtl_longitudinal_pid():
    """The gains the RTL guidance law actually flies."""
    return FilteredPID(PIDGains(kp=0.15, ki=0.0, kd=0.01, output_limits=(-0.10, 0.10)))


def test_a_single_nan_measurement_does_not_saturate_the_controller():
    """One corrupt odometry sample used to pin the output at full authority.

    ``0.0 * nan`` is ``nan``, so ``ki = 0.0`` did not keep NaN out of the
    integral, and the anti-windup clamp then resolved it to the *upper* bound --
    ``min(1.0, nan)`` returns 1.0, because CPython keeps the first operand when
    the comparison is False. With no integral action left to unwind it, the
    saturation was permanent.
    """
    pid = rtl_longitudinal_pid()
    nominal = pid.update(-0.5, DT)

    assert pid.update(float("nan"), DT) == 0.0
    assert math.isfinite(pid.components["integral"])
    assert math.isfinite(pid.components["derivative"])

    recovered = [pid.update(-0.5, DT) for _ in range(5)]
    assert all(value == pytest.approx(nominal, abs=0.02) for value in recovered), (
        f"controller did not recover: {recovered}"
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_every_non_finite_measurement_is_rejected(bad):
    pid = rtl_longitudinal_pid()
    for _ in range(5):
        pid.update(-0.5, DT)
    assert pid.update(bad, DT) == 0.0
    assert math.isfinite(pid.update(-0.5, DT))


# ------------------------------------------- quantization shaper dt transient


def test_a_slow_cycle_cannot_bank_authority_for_the_next_fast_one():
    """The residual is a displacement; dividing it by ``dt`` made it a lurch.

    A run of cycles at the ``LoopRate`` ceiling of 0.5 s -- which a blocking
    frame grab produces routinely -- followed by one normal cycle used to emit
    0.35 against a 0.10 cruise cap, defeating every jerk and braking limit
    upstream.
    """
    shaper = QuantizedCommandShaper(min_effective=0.035, quantization_step=0.01)
    for _ in range(6):
        shaper.shape(0.02, 0.5)

    command = shaper.shape(0.05, DT)
    assert abs(command) <= 0.05 + shaper.floor, f"banked residual discharged as {command}"


def test_the_bound_holds_under_a_pathological_dt_collapse():
    shaper = QuantizedCommandShaper(min_effective=0.035, quantization_step=0.01)
    for _ in range(40):
        shaper.shape(0.02, 0.5)
    assert abs(shaper.shape(0.05, 1e-4)) <= 0.05 + shaper.floor


@pytest.mark.parametrize("demand", [0.012, 0.031, 0.055, 0.094])
def test_bounding_the_dither_preserves_mean_fidelity(demand):
    """The clamp must not cost the sigma-delta its whole reason for existing."""
    shaper = QuantizedCommandShaper(min_effective=0.035, quantization_step=0.01)
    commands = [shaper.shape(demand, DT) for _ in range(800)]
    assert sum(commands) / len(commands) == pytest.approx(demand, abs=0.002)


# ------------------------------------------------- horizontal flight envelope


class _Actuator:
    no_fly = False


def supervisor():
    from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

    return FailsafeSupervisor(
        drone_actuator=_Actuator(),
        odom_supervisor=None,
        timeouts_cfg=MissionParameters().timeouts,
        kinematics_cfg=FlightKinematicsConfig(),
    )


def test_horizontal_commands_are_saturated_onto_the_envelope():
    """Nothing bounded ``vx``/``vy`` between the guidance law and the driver.

    ``clamp_kinematics`` saturated only ``vz`` and ``vyaw``. The horizontal pair
    reached ``move_velocity`` with the driver's own [-1, 1] as the sole guard --
    that is, full throttle. Every speed cap upstream lives inside the guidance
    law a defect would be in, so the envelope has to be enforced at the boundary.
    """
    limit = FlightKinematicsConfig().max_horizontal_speed
    vx, vy = supervisor().clamp_translation(5.0, -5.0)
    assert vx == pytest.approx(limit)
    assert vy == pytest.approx(-limit)


def test_legitimate_guidance_demands_pass_through_untouched():
    """The envelope must never shape normal flight."""
    guard = supervisor()
    for vx, vy in [(0.20, 0.0), (0.15, 0.22), (-0.10, -0.05), (0.0, 0.0)]:
        assert guard.clamp_translation(vx, vy) == (pytest.approx(vx), pytest.approx(vy))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_horizontal_commands_are_zeroed_not_saturated(bad):
    """NaN compares False against everything, so a clamp would grant full authority."""
    vx, vy = supervisor().clamp_translation(bad, bad)
    assert vx == 0.0 and vy == 0.0


def test_the_vertical_invariant_is_unchanged_by_the_new_guard():
    guard = supervisor()
    for vz in (5.0, 0.5, 0.0, -0.5):
        safe_vz, safe_vyaw = guard.clamp_kinematics(vz, 3.0)
        assert safe_vz <= 0.0
        assert safe_vyaw == 0.0


# ------------------------------------------------------ tracker outlier gate


def test_an_identity_switch_does_not_poison_the_velocity_state():
    """The velocity update divides the residual by ``dt``.

    A YOLO identity switch onto a different object is a large residual, and it
    used to be written straight into the rate -- which ``coast`` then
    extrapolated for the whole recovery horizon, servoing the airframe toward a
    phantom.
    """
    tracker = ConstantVelocityTracker(TrackerGains(), max_coast_sec=1.5)
    for step in range(10):
        tracker.update((400.0 + step, 240.0), DT)

    tracker.update((40.0, 240.0), DT)  # the switch

    estimate = tracker.coast(DT)
    assert abs(estimate.vx) <= TrackerGains().max_velocity_px_s
    assert abs(estimate.vx) < 200.0, (
        f"outlier wrote {estimate.vx:.0f} px/s into the velocity state"
    )


def test_genuine_target_motion_still_builds_velocity():
    """The gate must reject outliers without deafening the filter."""
    tracker = ConstantVelocityTracker(TrackerGains(), max_coast_sec=1.5)
    x = 300.0
    for _ in range(20):
        x += 4.0  # 60 px/s, an ordinary closing rate
        estimate = tracker.update((x, 240.0), DT)
    assert estimate.vx > 20.0


def test_the_velocity_state_is_bounded_even_against_sustained_outliers():
    tracker = ConstantVelocityTracker(TrackerGains(), max_coast_sec=1.5)
    for step in range(40):
        tracker.update((10.0 if step % 2 else 840.0, 240.0), DT)
    estimate = tracker.coast(DT)
    assert math.isfinite(estimate.vx)
    assert abs(estimate.vx) <= TrackerGains().max_velocity_px_s


# ------------------------------------------------------------ no_fly latching


def test_no_fly_is_not_persisted_back_into_the_configuration(tmp_path):
    """``--no-fly`` could only ever be set, and the config was written on exit.

    One benchtop run therefore stamped ``no_fly: true`` into mission_config.json
    permanently, and every later invocation -- the real flight included -- loaded
    it and reported a successful mission it never flew.
    """
    config = tmp_path / "mission_config.json"
    MissionParameters().save_to_file(str(config))
    assert MissionParameters.load_from_file(str(config)).no_fly is False

    # What mission.py now does when --no-fly is passed.
    params = MissionParameters.load_from_file(str(config))
    params.no_fly = True
    persisted = MissionParameters.load_from_file(str(config)).no_fly
    armed = params.no_fly
    params.no_fly = persisted
    params.save_to_file(str(config))
    params.no_fly = armed

    assert armed is True, "this run must still be a benchtop run"
    assert MissionParameters.load_from_file(str(config)).no_fly is False, (
        "the arming state leaked onto disk and would latch the next flight"
    )
