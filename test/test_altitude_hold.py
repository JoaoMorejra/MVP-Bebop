"""Holding altitude through horizontal translation.

The flight symptom: the drone climbs to 1.55 m, stabilizes there beautifully,
and then loses most of a metre the moment it starts moving horizontally --
during the Stage 2 cruise, during the Stage 3 approach, during any lateral
correction. It arrives over the accident scene low, photographs it from the
wrong height, and nothing in the velocity log looks wrong.

The cause is a missing sign. Pitching into a horizontal command tilts the thrust
vector and the firmware does not make up the vertical component, so the airframe
sinks for as long as the translation lasts -- and the governor watching for that
could only ever command descent. The altitude error had the one sign it had no
authority over, so it computed ``vz = 0.0`` every cycle and the drone went on
sinking, correctly, forever.

These tests are built around a disturbance model rather than around the
controller's internals: a point mass that sinks while it translates. The
descent-only governor is held to the same model, so the comparison is between
two control laws under one physics rather than between a fix and its own
assumptions.
"""

from __future__ import annotations

import pytest

from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.parameters import (
    AltitudeGovernorConfig,
    FlightKinematicsConfig,
    MissionParameters,
)
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

DT = 1.0 / 15.0
TARGET_M = 1.55

#: Sink rate a full-authority horizontal command induces, in m/s. Sized from the
#: flight: roughly a metre lost over the fifteen-odd seconds of translation
#: between lift-off stabilization and the nadir hover.
SINK_MPS = 0.07


def hold(target=TARGET_M, **overrides):
    return AltitudeHoldGovernor(target, AltitudeGovernorConfig(**overrides))


class Airframe:
    """A point mass that sinks whenever it is translating.

    Vertical velocity is the commanded ``vz`` minus the translation penalty,
    which is the whole of the disturbance under test. Deliberately crude: the
    claim being checked is that the loop rejects a sustained one-sided
    disturbance, and a more elaborate model would only make it easier.
    """

    def __init__(self, altitude=TARGET_M, sink_mps=SINK_MPS):
        self.altitude = altitude
        self.sink_mps = sink_mps
        self.translating = False

    def step(self, vz, dt):
        rate = vz - (self.sink_mps if self.translating else 0.0)
        self.altitude = max(0.0, self.altitude + rate * dt)
        return self.altitude


def fly(governor, *, seconds=20.0, translate_from=2.0, airframe=None, clamp=None):
    """Run the loop and return the altitude trace."""
    drone = airframe or Airframe()
    trace = []
    elapsed = 0.0
    while elapsed < seconds:
        drone.translating = elapsed >= translate_from
        command = governor.compute_vz(drone.altitude, DT)
        if clamp is not None:
            command, _ = clamp.clamp_kinematics(command, 0.0)
        trace.append(drone.step(command, DT))
        elapsed += DT
    return trace


# ------------------------------------------------------------ the requirement


def test_altitude_is_held_through_sustained_translation():
    """Requirement B, stated as the number that failed in flight."""
    trace = fly(hold())
    settled = trace[len(trace) // 2 :]

    assert min(settled) >= TARGET_M - 0.10, (
        f"sank to {min(settled):.2f} m against a {TARGET_M:.2f} m setpoint"
    )
    assert max(settled) <= TARGET_M + 0.10, f"overshot to {max(settled):.2f} m"


def test_the_descent_only_governor_fails_the_same_scenario():
    """The premise. Without this the test above proves only that a loop runs.

    Same disturbance, same airframe, same window -- the only difference is which
    control law is in the loop.
    """
    trace = fly(AltitudeAntiClimbGovernor(TARGET_M, AltitudeGovernorConfig()))

    assert trace[-1] < TARGET_M - 0.50, (
        f"the descent-only governor held {trace[-1]:.2f} m; the premise of the fix is that "
        f"it cannot"
    )


def test_the_hold_converges_without_oscillating():
    """A two-sided loop that hunts is worse than a one-sided loop that sags.

    Counted as sign changes in the altitude *error* once the transient has
    passed: a converged loop crosses the setpoint a handful of times as noise
    moves it around, and a limit cycle crosses it every couple of cycles.
    """
    trace = fly(hold(), seconds=30.0)
    settled = trace[len(trace) // 2 :]

    crossings = sum(
        1
        for previous, current in zip(settled, settled[1:])
        if (previous - TARGET_M) * (current - TARGET_M) < 0.0
    )
    span = max(settled) - min(settled)

    assert span <= 0.06, f"altitude oscillated over a {span:.3f} m band"
    assert crossings <= len(settled) // 6, f"{crossings} setpoint crossings is a limit cycle"


def test_a_drone_that_starts_low_is_brought_back_up():
    """The sink is usually already under way by the time the loop engages.

    The recovery is checked as a monotone shrinking of the deficit rather than a
    monotone rise: the vertical command is jerk-limited, so for the first few
    cycles the disturbance is still winning and the drone loses a further
    centimetre or two while the profile spins up. That is the profile doing its
    job, not the loop failing to.
    """
    drone = Airframe(altitude=TARGET_M - 0.30)
    trace = fly(hold(), airframe=drone, translate_from=0.0, seconds=25.0)

    assert trace[-1] == pytest.approx(TARGET_M, abs=0.08)
    assert min(trace) >= TARGET_M - 0.32, "the loop lost ground before recovering"

    climbing = trace[int(1.0 / DT) : len(trace) // 2]
    deficits = [TARGET_M - altitude for altitude in climbing]
    assert deficits == sorted(deficits, reverse=True), (
        "the deficit must shrink monotonically once the profile is up to speed"
    )
    assert max(trace) <= TARGET_M + 0.08, "the recovery overshot the setpoint"


def test_a_drone_above_the_setpoint_is_still_brought_down():
    """The original anti-climb duty is not given up in exchange for the new one.

    Flying over an obstacle shortens the ultrasonic range, the firmware reads
    that as lost height and climbs. That was the failure the one-way governor
    existed for, and it is still a failure.
    """
    drone = Airframe(altitude=TARGET_M + 0.25, sink_mps=0.0)
    governor = hold()
    for _ in range(int(20.0 / DT)):
        drone.step(governor.compute_vz(drone.altitude, DT), DT)

    assert drone.altitude == pytest.approx(TARGET_M, abs=0.06)


# ------------------------------------------------------------------- bounds


def test_the_command_never_leaves_the_configured_band():
    config = AltitudeGovernorConfig()
    governor = hold()
    for altitude in (0.0, 0.5, 1.0, 1.50, 1.55, 1.60, 2.0, 5.0, 1.20, 1.55):
        for _ in range(40):
            command = governor.compute_vz(altitude, DT)
            assert -config.max_descent_speed - 1e-9 <= command <= config.max_climb_speed + 1e-9


def test_the_ascent_ceiling_is_far_below_the_stage_one_climb():
    """This is a trim, not a manoeuvre, and the configuration has to say so."""
    governor_cfg = AltitudeGovernorConfig()
    kinematics = FlightKinematicsConfig()
    assert 0.0 < governor_cfg.max_climb_speed < kinematics.max_climb_speed_mps


def test_the_hold_stays_inside_the_safety_ceiling():
    """Corrective climb must not walk the drone through its own ceiling."""
    params = MissionParameters()
    params.kinematics.target_altitude_m = TARGET_M
    ceiling = TARGET_M + params.kinematics.altitude_ceiling_margin_m

    trace = fly(hold(), seconds=40.0)
    assert max(trace) < ceiling


def test_the_deadband_is_asymmetric_and_the_response_is_continuous():
    """Drifting up walks toward the ceiling; drifting down a little does not.

    The continuity matters as much as the asymmetry: a controller that jumps to
    ``kp * deadband`` the instant it crosses the threshold is a step input every
    time the altitude wanders across the boundary, which is the limit cycle this
    is supposed to prevent.
    """
    config = AltitudeGovernorConfig()
    assert config.climb_deadband_m > config.deadband_m

    governor = hold()
    for _ in range(30):
        governor.compute_vz(TARGET_M, DT)
    assert governor.compute_vz(TARGET_M, DT) == 0.0

    just_outside = governor.compute_vz(TARGET_M - config.climb_deadband_m - 0.002, DT)
    assert 0.0 <= just_outside <= 0.01, (
        f"crossing the deadband edge emitted {just_outside:.4f}; it must ease in"
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_altitude_unwinds_the_command_to_rest(bad):
    """An absent measurement, not a large one.

    NaN compares False against every bound, so an unguarded clamp resolves it to
    whichever limit happens to be the first operand -- full authority, held
    indefinitely, on one corrupt sample. The controller instead ramps the
    vertical axis down to rest, which is the only honest reading of "I do not
    know where I am", and does so through the profile rather than by dropping
    the command, because a step to zero is itself a disturbance.
    """
    governor = hold()
    for _ in range(10):
        governor.compute_vz(TARGET_M - 0.20, DT)
    engaged = governor.compute_vz(TARGET_M - 0.20, DT)
    assert engaged > 0.0, "the scenario must start from a live command"

    commands = [governor.compute_vz(bad, DT) for _ in range(40)]
    assert all(abs(value) <= abs(engaged) + 1e-9 for value in commands), (
        "a corrupt sample must never increase the command"
    )
    assert commands[-1] == pytest.approx(0.0, abs=1e-6)
    assert commands == sorted(commands, reverse=True), "the unwind must be monotone"


def test_a_valid_sample_after_a_corrupt_one_resumes_normally():
    governor = hold()
    for _ in range(20):
        governor.compute_vz(TARGET_M - 0.20, DT)
    for _ in range(5):
        governor.compute_vz(float("nan"), DT)

    recovered = [governor.compute_vz(TARGET_M - 0.20, DT) for _ in range(20)]
    assert recovered[-1] > 0.0, "the loop did not resume after the corrupt samples"


def test_the_integral_does_not_survive_the_disturbance_that_earned_it():
    """A bank built against a translation must not overshoot once it ends."""
    drone = Airframe()
    governor = hold()
    for _ in range(int(15.0 / DT)):
        drone.translating = True
        drone.step(governor.compute_vz(drone.altitude, DT), DT)

    drone.translating = False
    trace = []
    for _ in range(int(25.0 / DT)):
        trace.append(drone.step(governor.compute_vz(drone.altitude, DT), DT))

    assert max(trace) <= TARGET_M + 0.08, f"integral wind-up carried it to {max(trace):.2f} m"
    assert trace[-1] == pytest.approx(TARGET_M, abs=0.06)


def test_reset_clears_the_controller():
    governor = hold()
    for _ in range(30):
        governor.compute_vz(TARGET_M - 0.40, DT)
    assert governor.engaged
    governor.reset()
    assert not governor.engaged
    assert not governor.climbing
    assert governor.compute_vz(TARGET_M, DT) == 0.0


# ------------------------------------------------- horizontal speed throttling


def test_horizontal_speed_is_throttled_as_the_altitude_error_grows():
    """Translation is the disturbance, so translating less is a correction."""
    config = AltitudeGovernorConfig()
    scales = []
    for error in (0.0, 0.04, 0.08, 0.12, 0.30):
        governor = hold()
        governor.compute_vz(TARGET_M - error, DT)
        scales.append(governor.horizontal_scale())

    assert scales[0] == 1.0, "a drone holding altitude must fly at full speed"
    assert scales == sorted(scales, reverse=True), f"throttling must be monotone: {scales}"
    assert scales[-1] == pytest.approx(config.min_horizontal_scale)


def test_the_throttle_never_reaches_zero():
    """A stage that stops translating cannot finish, and the vertical axis would
    be left to resolve the error alone anyway -- which it is already doing."""
    governor = hold()
    for _ in range(50):
        governor.compute_vz(0.10, DT)
    assert governor.horizontal_scale() > 0.0


def test_the_descent_only_governor_declines_to_throttle():
    """It cannot act on a sink, so slowing down would cost speed for nothing.

    Present so the two governors are interchangeable at every call site, which
    is what makes ``hold_enabled = false`` a configuration change rather than a
    code change.
    """
    governor = AltitudeAntiClimbGovernor(TARGET_M, AltitudeGovernorConfig())
    governor.compute_vz(TARGET_M - 0.40, DT)
    assert governor.horizontal_scale() == 1.0
    assert governor.climb_authority == 0.0


# ---------------------------------------------------------- failsafe authority


class _Actuator:
    no_fly = False


def supervisor():
    return FailsafeSupervisor(
        drone_actuator=_Actuator(),
        odom_supervisor=None,
        timeouts_cfg=MissionParameters().timeouts,
        kinematics_cfg=FlightKinematicsConfig(),
    )


def test_ascent_is_suppressed_outside_an_altitude_hold_window():
    """The default posture is unchanged: descent-only unless a window is open."""
    guard = supervisor()
    for vz in (5.0, 0.5, 0.05, 0.0, -0.5):
        safe_vz, safe_vyaw = guard.clamp_kinematics(vz, 3.0)
        assert safe_vz <= 0.0
        assert safe_vyaw == 0.0


def test_a_hold_window_admits_bounded_ascent_and_closes_behind_itself():
    guard = supervisor()
    ceiling = AltitudeGovernorConfig().climb_authority

    with guard.altitude_hold_window(ceiling):
        assert guard.clamp_kinematics(0.05, 0.0)[0] == pytest.approx(0.05)
        assert guard.clamp_kinematics(5.0, 0.0)[0] == pytest.approx(ceiling)
        assert guard.clamp_kinematics(-0.20, 0.0)[0] == pytest.approx(-0.20)

    assert guard.altitude_hold_ceiling == 0.0
    assert guard.clamp_kinematics(0.05, 0.0)[0] == 0.0


def test_the_window_closes_even_when_the_stage_raises():
    guard = supervisor()
    with pytest.raises(RuntimeError):
        with guard.altitude_hold_window(0.10):
            raise RuntimeError("a stage fault mid-translation")
    assert guard.clamp_kinematics(0.05, 0.0)[0] == 0.0


def test_a_disabled_hold_opens_no_authority_at_all():
    """``hold_enabled = false`` needs no second check anywhere else."""
    guard = supervisor()
    ceiling = AltitudeGovernorConfig(hold_enabled=False).climb_authority
    assert ceiling == 0.0
    with guard.altitude_hold_window(ceiling):
        assert guard.clamp_kinematics(0.05, 0.0)[0] == 0.0


def test_hold_windows_nest_and_restore_the_outer_ceiling():
    """A hold inside a navigating loop is an ordinary arrangement, not an error."""
    guard = supervisor()
    with guard.altitude_hold_window(0.10):
        with guard.altitude_hold_window(0.02):
            assert guard.altitude_hold_ceiling == pytest.approx(0.02)
        assert guard.altitude_hold_ceiling == pytest.approx(0.10)
    assert guard.altitude_hold_ceiling == 0.0


def test_the_yaw_invariant_has_no_exception_inside_a_hold_window():
    guard = supervisor()
    with guard.altitude_hold_window(0.10):
        assert guard.clamp_kinematics(0.05, 2.0)[1] == 0.0


def test_the_governor_and_the_window_cannot_disagree_about_the_ceiling():
    """The supervisor is sized from the governor's own authority, not a literal."""
    config = AltitudeGovernorConfig()
    governor = AltitudeHoldGovernor(TARGET_M, config)
    assert governor.climb_authority == config.climb_authority

    guard = supervisor()
    with guard.altitude_hold_window(governor.climb_authority):
        for _ in range(60):
            command = governor.compute_vz(TARGET_M - 1.0, DT)
            assert guard.clamp_kinematics(command, 0.0)[0] == pytest.approx(command)


def test_the_stage_one_climb_window_still_needs_an_explicit_opt_in():
    """The two windows are granted for different reasons on different terms."""
    guard = supervisor()
    with guard.climb_window(0.25):
        assert guard.clamp_kinematics(0.20, 0.0)[0] == 0.0, (
            "the Stage 1 window must not grant ascent to a caller that did not ask"
        )
        assert guard.clamp_kinematics(0.20, 0.0, allow_climb=True)[0] == pytest.approx(0.20)


def test_feedforward_is_inert_at_the_default_gain():
    """The shipped default: no behaviour change until tuned."""
    governor = hold()
    with_ff = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    governor2 = hold()
    without_ff = governor2.compute_vz(TARGET_M, DT, vx_commanded=0.0)
    assert with_ff == pytest.approx(without_ff)


def test_feedforward_anticipates_translation_before_altitude_sags():
    """With a nonzero gain, a horizontal command alone -- no altitude error
    yet -- should already request some climb authority."""
    governor = hold(feedforward_gain=0.05)
    command = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    assert command > 0.0, "feedforward did not anticipate the sink"


def test_feedforward_is_bounded_by_the_climb_ceiling():
    """A large commanded vx must not push the feedforward past max_climb_speed."""
    governor = hold(feedforward_gain=10.0, max_climb_speed=0.10)
    command = None
    for _ in range(200):
        command = governor.compute_vz(TARGET_M, DT, vx_commanded=1.0)
    assert command <= 0.10 + 1e-9


def test_feedforward_respects_positional_calls_without_vx_commanded():
    """Existing call sites that never pass vx_commanded keep working."""
    governor = hold(feedforward_gain=0.05)
    command = governor.compute_vz(TARGET_M, DT)
    assert command == pytest.approx(0.0)
