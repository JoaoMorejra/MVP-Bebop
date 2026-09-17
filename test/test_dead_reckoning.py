"""Aggregating displacement from the mission's own ``move_velocity`` history.

The knowledge this is built on: a ``move_velocity`` held for a known duration
displaces the airframe by a knowable amount, but not by a naive
speed-times-time product. Commands under the driver's quantization threshold are
transmitted as exact zeros and move nothing at all; the airframe spends the
leading edge of each command accelerating into it and part of the next one
shedding it; and roll and pitch authority are not the same number. The
conversion is a calibration, and the aggregate is only ever as good as it.

What the aggregate buys is a position estimate that cannot drift. ``/bebop/odom``
is optical flow over featureless tarmac at the tilt angles this mission flies,
and when it wanders nothing on the airframe says so. The command history is not a
measurement of the world at all -- it is an exact record of intent -- so its error
is fixed by the calibration rather than growing with the flight.

The worked example from the requirement runs at the bottom: five metres forward,
four metres to the left, and a return vector that is the hypotenuse of the two.
"""

from __future__ import annotations

import contextlib
import logging
import math

import pytest

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker
from mvp_mission_bebop.parameters import (
    DeadReckoningConfig,
    FlightKinematicsConfig,
    MissionParameters,
)


class FakeClock:
    """Simulated monotonic time, so a whole mission integrates in microseconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def tracker(clock=None, calibration=None, **config_overrides):
    return DeadReckoningTracker(
        calibration or SpeedCalibration(normalized_to_mps=1.0),
        DeadReckoningConfig(**config_overrides),
        clock=clock or FakeClock(),
    )


def armed(clock, **kwargs):
    instance = tracker(clock=clock, **kwargs)
    instance.arm()
    return instance


#: The mission's control cadence. Every loop that translates transmits on it,
#: and the proxy credits the held command on every transmission, so this is the
#: interval the integration actually sees in flight.
CONTROL_PERIOD_SEC = 1.0 / 15.0


def hold(instance, clock, command, seconds, period=CONTROL_PERIOD_SEC):
    """Latch ``command`` and let it run for ``seconds`` at the control cadence.

    Stepped rather than advanced in one jump, because that is what the flight
    path does: the mission transmits every cycle and the proxy credits the held
    command on every transmission. Jumping would run the integration into the
    stall clamp, which is a guard against a descheduled mission thread and not a
    model of ordinary flight -- see
    ``test_a_stalled_loop_cannot_credit_unbounded_travel``.
    """
    instance.command(**command)
    remaining = seconds
    while remaining > 1e-9:
        step = min(period, remaining)
        clock.advance(step)
        instance.advance()
        remaining -= step


# ------------------------------------------------------------------ integration


def test_a_held_command_accumulates_displacement():
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 10.0)

    x, y = track.displacement
    assert x == pytest.approx(5.0)
    assert y == pytest.approx(0.0)


def test_displacement_accrues_over_the_interval_until_the_next_command():
    """The Bebop latches: a command displaces the drone until another arrives.

    Not over some nominal control period -- there is no timeout in the firmware
    and no implicit zero. Integrating against the control rate rather than the
    real interval is how an aggregate quietly loses everything a blocked loop
    did.
    """
    clock = FakeClock()
    track = armed(clock)

    # A deliberately irregular interval, well away from any control period: the
    # credited displacement has to follow the clock rather than a nominal rate.
    track.command(vx=0.5)
    clock.advance(0.37)
    track.command(vx=0.0)  # closes out the previous command over those 0.37 s

    assert track.displacement[0] == pytest.approx(0.5 * 0.37)

    # And the new command, being zero, accrues nothing however long it is held.
    clock.advance(0.43)
    track.advance()
    assert track.displacement[0] == pytest.approx(0.5 * 0.37)


def test_the_new_command_is_latched_only_after_the_old_one_is_credited():
    """Reversing the two lags every leg by one control period.

    Over a mission of several hundred commands that is not a rounding error, it
    is a systematic bias -- and it is invisible, because each individual segment
    still looks plausible.
    """
    clock = FakeClock()
    track = armed(clock)

    track.command(vx=0.5)
    clock.advance(0.2)
    track.command(vy=0.5, vx=0.0)
    clock.advance(0.2)
    track.advance()

    x, y = track.displacement
    assert x == pytest.approx(0.1), "the forward leg was not credited before the turn"
    assert y == pytest.approx(0.1)


def test_a_command_with_an_explicit_duration_expires():
    """``move_velocity(duration=...)`` zeroes the Twist when it elapses.

    Integrating it forward indefinitely would credit travel the airframe did not
    make -- and would do so for the whole of however long the mission then spent
    not sending anything.
    """
    clock = FakeClock()
    track = armed(clock)

    track.command(vx=0.5, duration=2.0)
    for _ in range(int(10.0 / CONTROL_PERIOD_SEC)):
        clock.advance(CONTROL_PERIOD_SEC)
        track.advance()

    assert track.displacement[0] == pytest.approx(1.0, abs=0.05)


def test_a_stalled_loop_cannot_credit_unbounded_travel():
    """The clamp loses real motion when it bites, and that is the right trade.

    Under-reading makes the return leg stop short; over-reading flies the drone
    past the origin and keeps going. A mission thread descheduled behind a
    blocking frame grab or a model load must not be credited with several
    seconds at the last commanded speed.
    """
    clock = FakeClock()
    track = armed(clock, max_integration_step_sec=0.5)

    track.command(vx=0.5)
    clock.advance(30.0)  # the loop never came back
    track.advance()

    assert track.displacement[0] == pytest.approx(0.5 * 0.5)


def test_nothing_accumulates_before_arming():
    """The aggregate is a displacement *from* a point, so it needs the point.

    Arming on the ground would credit it with the lift-off transient and the
    climb's horizontal wander, and the return leg would then fly home to a spot
    the drone was never at.
    """
    clock = FakeClock()
    track = tracker(clock=clock)

    track.command(vx=0.5)
    for _ in range(int(10.0 / CONTROL_PERIOD_SEC)):
        clock.advance(CONTROL_PERIOD_SEC)
        track.advance()

    assert track.displacement == (0.0, 0.0)
    assert not track.armed


def test_arming_discards_everything_that_came_before():
    clock = FakeClock()
    track = armed(clock)
    hold(track, clock, dict(vx=0.5), 10.0)
    assert track.distance_m == pytest.approx(5.0)

    track.arm()
    assert track.displacement == (0.0, 0.0)
    assert track.state().segment_count == 0


# ------------------------------------------------------------------ calibration


def test_commands_inside_the_driver_dead_zone_move_nothing():
    """``int8(v * 100)`` transmits anything under 0.01 as an exact zero.

    Systematic rather than incidental: the sigma-delta shaper deliberately emits
    long runs of sub-threshold demand, so an aggregate that credits them biases
    every estimate outward for the whole flight.
    """
    clock = FakeClock()
    calibration = SpeedCalibration(normalized_to_mps=1.0, command_deadzone=0.01)
    track = armed(clock, calibration=calibration)

    hold(track, clock, dict(vx=0.008), 20.0)
    assert track.displacement[0] == 0.0

    hold(track, clock, dict(vx=0.02), 10.0)
    assert track.displacement[0] == pytest.approx(0.2)


def test_the_conversion_is_not_a_naive_one_to_one():
    """0.5 for ten seconds is not five metres, and the calibration says why.

    Two effects, both measurable on the ground: the command-to-speed gain is not
    unity, and the airframe does not spend the whole interval at the commanded
    speed -- it accelerates into it and sheds it again.
    """
    clock = FakeClock()
    calibration = SpeedCalibration(normalized_to_mps=1.4, translation_efficiency=0.85)
    track = armed(clock, calibration=calibration)

    hold(track, clock, dict(vx=0.5), 10.0)

    assert track.displacement[0] == pytest.approx(0.5 * 1.4 * 10.0 * 0.85)
    assert track.displacement[0] != pytest.approx(5.0)


def test_the_cross_track_axis_can_carry_its_own_gain():
    """Roll and pitch authority are not the same number on this airframe.

    An aggregate that assumes they are accumulates a cross-track bias over a
    mission, which is exactly the error a straight reverse return cannot see.
    """
    clock = FakeClock()
    calibration = SpeedCalibration(normalized_to_mps=1.0, lateral_to_mps=0.6)
    track = armed(clock, calibration=calibration)

    hold(track, clock, dict(vx=0.5, vy=0.5), 10.0)

    x, y = track.displacement
    assert x == pytest.approx(5.0)
    assert y == pytest.approx(3.0)


def test_the_default_calibration_reproduces_the_naive_conversion():
    """So an uncalibrated build is wrong in an obvious way rather than a subtle one."""
    calibration = SpeedCalibration.from_kinematics(FlightKinematicsConfig())
    assert calibration.displacement(0.5, 0.0, 10.0)[0] == pytest.approx(5.0)
    assert calibration.is_displacement_naive is False, (
        "the driver dead zone is a fact about the hardware, not a tuning choice"
    )


# --------------------------------------------------------------- return vector


def test_the_return_vector_is_the_reverse_of_the_aggregate():
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 10.0)
    hold(track, clock, dict(vx=0.0, vy=0.4), 10.0)

    ex, ey, distance = track.body_frame_origin_error()
    assert ex == pytest.approx(-5.0), "the origin lies five metres behind the nose"
    assert ey == pytest.approx(-4.0), "and four metres to starboard"
    assert distance == pytest.approx(math.hypot(5.0, 4.0))


def test_the_worked_example_from_the_requirement():
    """Five metres forward then four to the left, and the hypotenuse home.

    The requirement writes that aggregate as ``(5, -4)``, counting the lateral
    axis positive to the right. This package counts body FLU throughout --
    positive ``y`` is to the left, matching ``move_velocity`` and
    ``body_frame_launch_error`` -- so the same displacement reads ``(5, +4)``
    and the return is ``(-5, -4)``: five back and four to starboard. Same
    vector, same hypotenuse, one sign convention rather than two.
    """
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 10.0)
    hold(track, clock, dict(vx=0.0, vy=0.4), 10.0)

    state = track.state()
    assert state.displacement == (pytest.approx(5.0), pytest.approx(4.0))
    assert state.distance_m == pytest.approx(6.403, abs=0.01)

    ex, ey, _ = state.body_frame_origin_error()
    bearing_deg = math.degrees(math.atan2(ey, ex))
    assert bearing_deg == pytest.approx(-180.0 + 38.66, abs=0.5), (
        "the return bearing must point back down the hypotenuse"
    )


def test_returning_along_the_vector_drives_the_aggregate_to_zero():
    """The aggregate closes the loop on itself: the return leg is integrated too.

    That is what makes this navigation rather than bookkeeping. Every command
    the return leg transmits goes through the same integration, so the remaining
    displacement shrinks as the drone flies it off, and reaching ``(0, 0)`` is a
    statement about the commands actually sent rather than about a plan.
    """
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 10.0)
    hold(track, clock, dict(vx=0.0, vy=0.4), 10.0)
    assert track.distance_m > 6.0

    # Fly the reverse of the aggregate.
    hold(track, clock, dict(vx=0.0, vy=-0.4), 10.0)
    hold(track, clock, dict(vx=-0.5, vy=0.0), 10.0)
    hold(track, clock, dict(vx=0.0, vy=0.0), 1.0)

    assert track.distance_m == pytest.approx(0.0, abs=1e-6)


def test_path_length_and_displacement_are_distinct():
    """Out and back is zero displacement over ten metres of path."""
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 10.0)
    hold(track, clock, dict(vx=-0.5), 10.0)

    state = track.state()
    assert state.distance_m == pytest.approx(0.0, abs=1e-6)
    assert state.path_length_m == pytest.approx(10.0)


# ------------------------------------------------------------------- segments


def test_the_segment_log_reconstructs_the_aggregate_exactly():
    """A return leg that ended in the wrong place is a question about which
    segment was wrong, and without the log there is nothing to answer it with.

    One segment per integration interval rather than per command, because the
    interval is the thing displacement is actually credited over -- a command
    held across fifty cycles is fifty intervals of evidence, and collapsing them
    would throw away the resolution that makes the log worth keeping.
    """
    clock = FakeClock()
    track = armed(clock)

    hold(track, clock, dict(vx=0.5), 4.0)
    hold(track, clock, dict(vx=0.0, vy=0.25), 4.0)

    segments = track.segments
    assert segments, "nothing was recorded"
    assert sum(segment.dx_m for segment in segments) == pytest.approx(track.displacement[0])
    assert sum(segment.dy_m for segment in segments) == pytest.approx(track.displacement[1])
    assert sum(segment.duration_sec for segment in segments) == pytest.approx(8.0, abs=0.01)

    forward = [segment for segment in segments if segment.vx != 0.0]
    lateral = [segment for segment in segments if segment.vy != 0.0]
    assert all(segment.vx == pytest.approx(0.5) for segment in forward)
    assert sum(segment.dx_m for segment in forward) == pytest.approx(2.0)
    assert sum(segment.dy_m for segment in lateral) == pytest.approx(1.0)


def test_the_segment_log_is_bounded_without_losing_the_count():
    """The log is a ring buffer; the total is not.

    A mission that overran the buffer must still be able to say how many
    commands it aggregated, or the summary silently understates the flight.
    """
    clock = FakeClock()
    track = armed(clock, max_segments=10)
    for index in range(50):
        hold(track, clock, dict(vx=0.1 + index * 1e-4), 0.2)

    assert len(track.segments) == 10
    assert track.state().segment_count > 100
    assert track.state().path_length_m > 0.9


def test_a_stationary_command_records_no_segment():
    clock = FakeClock()
    track = armed(clock)
    hold(track, clock, dict(vx=0.0, vy=0.0), 30.0)
    assert track.segments == []
    assert track.state().elapsed_sec > 0.0


def test_the_summary_reports_the_vector_and_the_bearing():
    clock = FakeClock()
    track = armed(clock)
    hold(track, clock, dict(vx=0.5), 10.0)
    hold(track, clock, dict(vx=0.0, vy=0.4), 10.0)

    summary = track.summary()
    assert "+5.00" in summary and "+4.00" in summary
    assert "6.40" in summary


# ------------------------------------------------------------ actuator wiring


class StubDrone:
    def __init__(self):
        self.calls = []

    def flat_trim(self):
        pass

    def takeoff(self, altitude):
        return True

    def land(self, timeout=10.0):
        self.calls.append(("land",))
        return True

    def camera_control(self, tilt, pan):
        pass

    def snapshot(self):
        pass

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append(("move_velocity", vx, vy, vz, vyaw))

    def delay(self, seconds):
        pass

    def connect(self):
        return True

    def cleanup(self):
        pass


def test_every_command_through_the_proxy_is_aggregated():
    """The proxy feeds the tracker, not the steps.

    An aggregate assembled by five separate control loops is only as complete as
    all five of them remembering to report themselves, and a leg lost that way
    is lost silently -- the number still looks like a displacement.
    """
    clock = FakeClock()
    track = armed(clock)
    proxy = BenchtopDroneProxy(StubDrone(), no_fly=True, motion_tracker=track)

    def transmit(seconds, **command):
        proxy.move_velocity(**command)
        for _ in range(int(round(seconds / CONTROL_PERIOD_SEC))):
            clock.advance(CONTROL_PERIOD_SEC)
            proxy.move_velocity(**command)

    transmit(6.0, vx=0.5)
    transmit(4.0, vx=0.0, vy=0.5)
    proxy.move_velocity(vx=0.0, vy=0.0)

    assert track.displacement == (pytest.approx(3.0, abs=0.05), pytest.approx(2.0, abs=0.05))


def test_landing_stops_the_aggregate_accruing():
    """A landing ends translation whatever the last Twist said.

    Without this the tracker goes on crediting the final held command for the
    whole descent -- and the descent is the longest single interval in the
    flight during which nothing is transmitted.
    """
    clock = FakeClock()
    track = armed(clock)
    proxy = BenchtopDroneProxy(StubDrone(), no_fly=True, motion_tracker=track)

    proxy.move_velocity(vx=0.5)
    for _ in range(int(2.0 / CONTROL_PERIOD_SEC)):
        clock.advance(CONTROL_PERIOD_SEC)
        proxy.move_velocity(vx=0.5)
    proxy.land()
    clock.advance(30.0)
    track.advance()

    assert track.displacement[0] == pytest.approx(1.0, abs=0.05)


def test_a_proxy_without_a_tracker_is_unaffected():
    """Dead reckoning is switchable, and switching it off must change nothing."""
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)
    proxy.move_velocity(vx=0.2, vy=-0.1)
    assert proxy.motion_tracker is None
    assert ("move_velocity", 0.2, -0.1, 0.0, 0.0) in drone.calls


# ---------------------------------------------------------------- integration


def test_dead_reckoning_agrees_with_the_benchtop_simulation():
    """Two independent integrations of the same command stream must agree.

    The simulator integrates commands into synthetic odometry to model the
    airframe; the tracker integrates them into a displacement to model the
    mission's own knowledge. They share no code and are driven from the same
    wire, so a disagreement means one of the two has the latching semantics
    wrong -- which is the defect most likely to survive a unit test, because
    each looks right in isolation.
    """
    from mvp_mission_bebop.actuators.simulator import KinematicSimulator
    from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

    params = MissionParameters()
    clock = FakeClock()
    calibration = SpeedCalibration(normalized_to_mps=1.0)
    supervisor = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)
    simulator = KinematicSimulator(supervisor, calibration, clock=clock)
    track = DeadReckoningTracker(calibration, params.dead_reckoning, clock=clock)
    proxy = BenchtopDroneProxy(
        StubDrone(), no_fly=True, simulator=simulator, motion_tracker=track
    )

    simulator.publish_initial_state()
    assert supervisor.calibrate_ground_reference()
    proxy.takeoff(altitude=1.0)
    clock.advance(3.0)
    simulator.integrate()
    supervisor.freeze_hover_takeoff_origin()
    track.arm()

    for command, seconds in (
        (dict(vx=0.10), 12.0),
        (dict(vx=0.15), 8.0),
        (dict(vx=0.0, vy=0.08), 5.0),
        (dict(vx=0.0, vy=0.0), 2.0),
    ):
        for _ in range(int(round(seconds / CONTROL_PERIOD_SEC))):
            proxy.move_velocity(**command)
            clock.advance(CONTROL_PERIOD_SEC)
            simulator.integrate()

    track.advance()
    snapshot = supervisor.snapshot()
    reckoned_x, reckoned_y = track.displacement

    assert reckoned_x == pytest.approx(snapshot.x - snapshot.takeoff_x, abs=0.05)
    assert reckoned_y == pytest.approx(snapshot.y - snapshot.takeoff_y, abs=0.05)
    assert reckoned_x == pytest.approx(0.10 * 12.0 + 0.15 * 8.0, abs=0.05)
    assert reckoned_y == pytest.approx(0.08 * 5.0, abs=0.05)


# ------------------------------------------------------- the RTL step's choice


class _Blackboard:
    rtl_completed = False


class _Snapshot:
    """Odometry that disagrees with the aggregate, so the choice is observable."""

    def __init__(self, x=0.0, y=0.0, has_origin=True):
        self.x, self.y = x, y
        self.horizontal_speed = 0.0
        self.speed = 0.0
        self.relative_altitude = 1.55
        self.has_launch_origin = has_origin
        self.takeoff_x = 0.0 if has_origin else None
        self.takeoff_y = 0.0 if has_origin else None

    def body_frame_launch_error(self):
        return -self.x, -self.y, math.hypot(self.x, self.y)


class _Odometry:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class _Ctx:
    """The attribute surface ``ClosedLoopRTLStep._reference`` reads."""

    def __init__(self, drone, snapshot, use_dead_reckoning=True):
        self.params = MissionParameters()
        self.params.rtl.use_dead_reckoning = use_dead_reckoning
        self.drone = drone
        self.odom_supervisor = _Odometry(snapshot)


def rtl_step():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    return ClosedLoopRTLStep()


def flown_proxy(clock, forward_m=5.0, left_m=4.0):
    """A proxy whose tracker has aggregated a forward leg and a lateral one."""
    track = armed(clock)
    proxy = BenchtopDroneProxy(StubDrone(), no_fly=True, motion_tracker=track)
    for command, seconds in ((dict(vx=0.5), forward_m / 0.5), (dict(vy=0.4), left_m / 0.4)):
        proxy.move_velocity(**command)
        for _ in range(int(round(seconds / CONTROL_PERIOD_SEC))):
            clock.advance(CONTROL_PERIOD_SEC)
            proxy.move_velocity(**command)
    proxy.move_velocity(vx=0.0, vy=0.0)
    return proxy


def test_the_return_leg_navigates_on_the_aggregate_not_on_odometry():
    """The whole point of Requirement C, stated where the choice is made.

    Odometry here claims the drone never left the origin -- the failure mode
    optical flow over featureless tarmac actually produces. The aggregate knows
    better, because it is a record of what was commanded rather than a
    measurement of what was seen.
    """
    clock = FakeClock()
    proxy = flown_proxy(clock)
    ctx = _Ctx(proxy, _Snapshot(x=0.0, y=0.0))

    ref = rtl_step()._reference(ctx, proxy.motion_tracker, ctx.odom_supervisor.snapshot(),
                                log=True)

    assert ref.source == "dead_reckoning"
    assert ref.ex_body_m == pytest.approx(-5.0, abs=0.05)
    assert ref.ey_body_m == pytest.approx(-4.0, abs=0.05)
    assert ref.distance_m == pytest.approx(math.hypot(5.0, 4.0), abs=0.05)


def test_the_step_falls_back_to_odometry_when_nothing_was_aggregated():
    """Three situations, one meaning: there is no ``(0, 0)`` to be relative to.

    Dead reckoning switched off, no tracker wired in, or a tracker that was
    never armed -- which is what a mission that failed before Stage 1 froze its
    origin looks like.
    """
    step = rtl_step()
    snapshot = _Snapshot(x=2.0, y=-0.5)

    clock = FakeClock()
    unarmed = tracker(clock=clock)
    proxy = BenchtopDroneProxy(StubDrone(), no_fly=True, motion_tracker=unarmed)
    ctx = _Ctx(proxy, snapshot)
    assert step._tracker(ctx) is None

    ctx_disabled = _Ctx(flown_proxy(FakeClock()), snapshot, use_dead_reckoning=False)
    assert step._tracker(ctx_disabled) is None

    ctx_none = _Ctx(BenchtopDroneProxy(StubDrone(), no_fly=True), snapshot)
    assert step._tracker(ctx_none) is None

    ref = step._reference(ctx_none, None, snapshot, log=True)
    assert ref.source == "odometry"
    assert ref.ex_body_m == pytest.approx(-2.0)


class _Recorder(logging.Handler):
    """Captures one logger's records.

    Attached directly rather than through ``caplog``, which this environment's
    pytest configuration does not deliver -- the fixture's handler never sees the
    record, so a test written against it fails inexplicably in one direction and
    would pass vacuously in the other. A handler on the logger under test has no
    such dependency.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, minimum=logging.DEBUG):
        return [r.getMessage() for r in self.records if r.levelno >= minimum]


@contextlib.contextmanager
def recording(name="Step5RTL"):
    handler = _Recorder()
    target = logging.getLogger(name)
    previous = target.level
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


def test_the_disagreement_between_the_two_estimates_is_logged():
    """The only observable this mission produces that says which to trust.

    A flight that lands two metres from the pad is otherwise a mystery; with
    this line in the log it is a measurement of either the speed calibration or
    the optical flow. Raised to a warning once it exceeds the configured
    threshold, because past that it is the operator's problem and not just
    telemetry.
    """
    clock = FakeClock()
    proxy = flown_proxy(clock)
    ctx = _Ctx(proxy, _Snapshot(x=0.0, y=0.0))

    with recording() as log:
        rtl_step()._reference(
            ctx, proxy.motion_tracker, ctx.odom_supervisor.snapshot(), log=True
        )

    warnings = log.messages(logging.WARNING)
    assert any("disagree by 6.40 m" in message for message in warnings), warnings
    assert any("Navigating on dead_reckoning" in message for message in warnings)


def test_agreeing_estimates_are_reported_without_raising_a_warning():
    """The number is still computed and logged -- it just is not an alarm."""
    clock = FakeClock()
    proxy = flown_proxy(clock)
    ctx = _Ctx(proxy, _Snapshot(x=5.0, y=4.0))

    with recording() as log:
        ref = rtl_step()._reference(
            ctx, proxy.motion_tracker, ctx.odom_supervisor.snapshot(), log=True
        )

    assert ref.source == "dead_reckoning"
    assert not log.messages(logging.WARNING), "agreement must not read as a fault"
    assert any("disagree by 0.0" in message for message in log.messages())


def test_the_aggregate_is_armed_when_the_airborne_origin_is_frozen():
    """They have to agree on which point ``(0, 0)`` is.

    Stage 1 measures the horizontal origin after the climb has converged, not on
    the ground, so that ground-effect turbulence and the climb's own wander stay
    out of the coordinate the drone returns to. An aggregate armed anywhere else
    is relative to a different point than the one odometry calls home.
    """
    from mvp_mission_bebop.steps.takeoff import TakeoffStep

    clock = FakeClock()
    track = tracker(clock=clock)
    proxy = BenchtopDroneProxy(StubDrone(), no_fly=True, motion_tracker=track)

    class Ctx:
        drone = proxy

    assert not track.armed
    TakeoffStep._arm_dead_reckoning(Ctx())
    assert track.armed
    assert track.displacement == (0.0, 0.0)


def test_arming_is_a_no_op_when_dead_reckoning_is_not_wired_in():
    from mvp_mission_bebop.steps.takeoff import TakeoffStep

    class Ctx:
        drone = BenchtopDroneProxy(StubDrone(), no_fly=True)

    TakeoffStep._arm_dead_reckoning(Ctx())  # must not raise
