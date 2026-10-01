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


# ------------------------------------------------------------ stage jump gate


def _gate(requested, **state):
    from mvp_mission_bebop.engine.stage_gate import refuse_stage_request

    defaults = dict(
        stage_numbers=(1, 2, 3, 4, 5),
        current_stage=None,
        takeoff_committed=False,
        takeoff_complete=False,
        airborne=False,
    )
    defaults.update(state)
    return refuse_stage_request(requested, **defaults)


def test_stage_one_is_refused_on_the_ground_after_touchdown():
    """Measured defect: after touchdown, altitude <= 0.25 m let goto 1 re-launch."""
    assert _gate(1, current_stage=5, takeoff_committed=True, takeoff_complete=True) is not None


def test_stage_one_is_refused_once_the_countdown_has_ended():
    assert _gate(1, current_stage=1, takeoff_committed=True) is not None


def test_stage_one_is_refused_whenever_the_mission_is_past_it():
    for stage in (2, 3, 4, 5):
        assert _gate(1, current_stage=stage) is not None


def test_stage_one_is_refused_when_altitude_says_airborne():
    assert _gate(1, current_stage=1, airborne=True) is not None


def test_stage_one_during_its_own_countdown_on_the_ground_is_allowed():
    assert _gate(1, current_stage=1) is None
    assert _gate(1) is None


def test_stage_five_is_refused_while_already_in_stage_five():
    assert _gate(5, current_stage=5, takeoff_committed=True, takeoff_complete=True) is not None


def test_stage_five_is_allowed_from_any_earlier_stage_including_the_climb():
    """The battery RTL jumps to 5 from anywhere; refusing it would keep a dying pack aloft."""
    for stage in (1, 2, 3, 4):
        assert _gate(5, current_stage=stage, takeoff_committed=True) is None


@pytest.mark.parametrize("requested", [2, 3, 4])
def test_flight_stages_are_refused_until_stage_one_completed_the_takeoff(requested):
    assert _gate(requested, current_stage=1, takeoff_committed=True) is not None
    assert _gate(requested, current_stage=1, takeoff_committed=True, takeoff_complete=True) is None


def test_a_partial_run_without_stage_one_has_no_takeoff_to_wait_for():
    assert _gate(3, stage_numbers=(2, 3, 4), current_stage=2) is None


def test_a_stage_outside_the_run_is_refused():
    assert _gate(3, stage_numbers=(4,), current_stage=4) is not None


def test_the_handler_reads_live_state_and_only_forwards_accepted_requests():
    import types

    from mvp_mission_bebop.engine.stage_gate import StageRequestHandler

    requested = []
    ctx = types.SimpleNamespace(
        current_stage=1,
        blackboard=types.SimpleNamespace(takeoff_committed=False, takeoff_complete=False),
        request_stage=requested.append,
    )
    altitude = {"value": 0.0}
    handler = StageRequestHandler(ctx, (1, 2, 3, 4, 5), lambda: altitude["value"])

    assert handler(2) is False
    ctx.blackboard.takeoff_committed = True
    ctx.blackboard.takeoff_complete = True
    ctx.current_stage = 2
    assert handler(3) is True
    assert handler(1) is False
    assert requested == [3]


def test_the_handler_treats_an_unreadable_altitude_as_airborne():
    import types

    from mvp_mission_bebop.engine.stage_gate import StageRequestHandler

    def broken():
        raise RuntimeError("no odometry")

    ctx = types.SimpleNamespace(
        current_stage=None,
        blackboard=types.SimpleNamespace(takeoff_committed=False, takeoff_complete=False),
        request_stage=lambda _s: None,
    )
    assert StageRequestHandler(ctx, (1, 2, 3, 4, 5), broken)(1) is False


def test_the_runner_records_the_stage_it_is_executing():
    import threading
    import types

    from mvp_mission_bebop.engine.runner import MissionRunner
    from mvp_mission_bebop.steps.base import StepStatus

    seen = []

    class Probe:
        name = "probe"

        def execute(self, ctx):
            seen.append(ctx.current_stage)
            return StepStatus.SUCCESS

    ctx = types.SimpleNamespace(
        emergency_event=threading.Event(),
        stage_jump_event=threading.Event(),
        requested_stage=None,
        detection_reveal_enabled=True,
        current_stage=None,
    )
    MissionRunner(ctx, [Probe(), Probe()], stage_numbers=[2, 4]).run()
    assert seen == [2, 4]
