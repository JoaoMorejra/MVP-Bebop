"""Regression tests for target reacquisition and terminal touchdown.

Two flight-observed defects, both of which ended with the airframe doing nothing
useful for a long time:

*The approach abandoned a target it was directly on top of.* Near the inspection
attitude the ground footprint is small, the object is foreshortened into an
aspect ratio the detector never trained on, and the airframe's own shadow falls
on it. Losing the detection there is the expected case, and the step treated it
as the end of the approach.

*RTL returned to the takeoff point and never landed.* A braking profile plus
odometry lag leaves the drone slightly past the origin; forward flight was
prohibited outright, so the along-track axis was pinned at zero, the distance
stopped changing, and arrival could never confirm.
"""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.rtl_guidance import RTLGuidanceController, RTLPhase
from mvp_mission_bebop.controllers.visual_servoing import (
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    ReturnToLaunchConfig,
    VisionConfig,
)

DT = 1.0 / 16.0
FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
CENTER_X, CENTER_Y = WIDTH / 2.0, HEIGHT / 2.0
SEARCH_TILT_DEG = -20.0
NADIR_TILT_DEG = GimbalConstraintsConfig().nadir_tilt_deg


# ═══════════════════════════════════════════════ target loss near nadir


def build_controller():
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(
            search_tilt_deg=SEARCH_TILT_DEG, nadir_tilt_deg=NADIR_TILT_DEG
        ),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=VisionConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def at_nadir(controller, tilt=NADIR_TILT_DEG, cycles=6):
    """Drive the controller into the nadir phase with a centred target."""
    controller._phase = TrackingPhase.NADIR
    command = None
    for _ in range(cycles):
        command = controller.compute((CENTER_X, CENTER_Y), FRAME, tilt, 1.20, DT)
        tilt = command.tilt_deg
    return command, tilt


def test_losing_the_target_at_nadir_does_not_end_the_approach():
    controller = build_controller()
    _, tilt = at_nadir(controller)

    command = controller.note_target_lost(tilt, DT)

    assert controller.phase is TrackingPhase.REACQUIRING
    assert not command.recovery_exhausted, "recovery must not give up on the first lost frame"


def test_recovery_holds_position_rather_than_drifting():
    """The cross-track axis is silent without a bearing to act on."""
    controller = build_controller()
    _, tilt = at_nadir(controller)

    for _ in range(int(3.0 / DT)):
        command = controller.note_target_lost(tilt, DT)
        tilt = command.tilt_deg
        assert command.vy == 0.0, "no lateral command without a measurement"
        assert command.vx <= 0.0, "recovery may back off, never press forward"


def test_recovery_sweeps_the_gimbal_back_up_from_the_last_sighting():
    """Pitching up is the recovery; pitching down only stares harder at the miss.

    A shallower ray lands the footprint further ahead and covers far more ground
    per degree of pitch, so it re-frames a target that fell out of the near
    field. The sweep starts from the anchor -- the tilt at which the target was
    last actually seen -- not from wherever the gimbal happens to be.
    """
    controller = build_controller()
    _, tilt = at_nadir(controller)
    start = tilt

    tilts = []
    for _ in range(int(4.0 / DT)):
        command = controller.note_target_lost(tilt, DT)
        tilt = command.tilt_deg
        tilts.append(tilt)

    assert tilts[-1] > start, "the gimbal must pitch up, toward a wider footprint"
    assert tilts[-1] <= SEARCH_TILT_DEG + 1e-6, "never above the search attitude"
    assert tilts == sorted(tilts), "the sweep must be monotone, not a dither"


def test_recovery_backs_off_then_holds():
    """A target directly beneath the airframe is outside the frustum at any tilt."""
    controller = build_controller()
    _, tilt = at_nadir(controller)
    cfg = VisionConfig()

    reverse = []
    elapsed = 0.0
    while elapsed < cfg.reacquire_reverse_sec + 1.5:
        command = controller.note_target_lost(tilt, DT)
        tilt = command.tilt_deg
        reverse.append((elapsed, command.vx))
        elapsed += DT

    early = [vx for t, vx in reverse if t < cfg.reacquire_reverse_sec - DT]
    late = [vx for t, vx in reverse if t > cfg.reacquire_reverse_sec + DT]

    assert all(vx < 0.0 for vx in early), "the creep must actually run"
    assert min(early) >= -cfg.reacquire_reverse_speed - 1e-9, "and stay bounded"
    assert all(vx == 0.0 for vx in late), "then stop, rather than reversing indefinitely"


def test_regaining_the_target_resumes_the_phase_it_was_lost_from():
    """The anchor exists so the approach continues rather than restarting."""
    controller = build_controller()
    _, tilt = at_nadir(controller)

    for _ in range(int(2.0 / DT)):
        command = controller.note_target_lost(tilt, DT)
        tilt = command.tilt_deg
    assert controller.phase is TrackingPhase.REACQUIRING

    for _ in range(VisionConfig().reacquire_confirm_frames):
        command = controller.compute((CENTER_X, CENTER_Y), FRAME, tilt, 1.20, DT)
        tilt = command.tilt_deg

    assert controller.phase is TrackingPhase.NADIR, "must resume nadir, not restart the approach"


def test_a_single_frame_does_not_end_recovery():
    """One detection is what sent the tracker chasing phantoms before."""
    controller = build_controller()
    _, tilt = at_nadir(controller)
    for _ in range(int(1.0 / DT)):
        tilt = controller.note_target_lost(tilt, DT).tilt_deg

    controller.compute((CENTER_X, CENTER_Y), FRAME, tilt, 1.20, DT)
    assert controller.phase is TrackingPhase.REACQUIRING, "one frame is not a reacquisition"


def test_recovery_is_bounded_and_reports_exhaustion():
    controller = build_controller()
    _, tilt = at_nadir(controller)
    cfg = VisionConfig()

    elapsed = 0.0
    command = None
    while elapsed < cfg.reacquire_timeout_sec + 1.0:
        command = controller.note_target_lost(tilt, DT)
        tilt = command.tilt_deg
        elapsed += DT

    assert command.recovery_exhausted, "recovery must terminate rather than hold forever"
    assert command.vx == 0.0, "an exhausted recovery must not still be translating"


def test_recovery_from_the_approach_phase_resumes_the_approach():
    controller = build_controller()
    controller._phase = TrackingPhase.APPROACHING
    tilt = -45.0
    for _ in range(4):
        tilt = controller.compute((CENTER_X, CENTER_Y), FRAME, tilt, 1.20, DT).tilt_deg

    for _ in range(int(1.5 / DT)):
        tilt = controller.note_target_lost(tilt, DT).tilt_deg
    for _ in range(VisionConfig().reacquire_confirm_frames):
        tilt = controller.compute((CENTER_X, CENTER_Y), FRAME, tilt, 1.20, DT).tilt_deg

    assert controller.phase is TrackingPhase.APPROACHING


# ═══════════════════════════════════════════════════ RTL touchdown


class Snapshot:
    """Minimal odometric state the guidance law reads."""

    def __init__(self, ex, ey=0.0, speed=0.0):
        self._ex, self._ey = ex, ey
        self.horizontal_speed = speed
        self.x = self.y = 0.0

    def body_frame_launch_error(self):
        return self._ex, self._ey, math.hypot(self._ex, self._ey)


def guidance(cfg=None):
    controller = RTLGuidanceController(
        cfg or ReturnToLaunchConfig(), FlightKinematicsConfig(), SpeedCalibration(1.0)
    )
    controller.reset()
    return controller


def test_a_terminal_overshoot_is_nulled_instead_of_deadlocking():
    """The flight failure: past the origin, forward prohibited, distance frozen.

    Holding the along-track axis meant the distance never changed, so arrival
    could never confirm and the drone hovered over the takeoff point until the
    whole return window expired.
    """
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)

    ex = 0.35  # origin this far ahead of the nose: outside the arrival radius
    committed_at = None
    for cycle in range(int(30.0 / DT)):
        command = law.compute(Snapshot(ex), 0.0, DT, phase=RTLPhase.NAVIGATING)
        ex -= command.vx * DT
        if command.ready_to_land:
            committed_at = cycle * DT
            break

    assert committed_at is not None, "never committed to landing"
    assert committed_at < 15.0, f"took {committed_at:.1f} s to commit"
    assert ex <= cfg.arrival_radius_m


def test_forward_recovery_never_becomes_forward_flight():
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)
    peak = max(
        law.compute(Snapshot(1.0), 0.0, DT, phase=RTLPhase.NAVIGATING).vx for _ in range(200)
    )
    assert 0.0 < peak <= cfg.overshoot_recovery_speed


def test_the_landing_commit_does_not_depend_on_positional_dispersion():
    """Settlement must not be the only way to reach the ground.

    Its positional-sigma term depends on odometry quality the Bebop does not
    guarantee at hover, so a drone sitting over the origin could fail it
    indefinitely. The commit asks only: inside the radius, moving slowly, for
    long enough.
    """
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)

    # Position jitters well beyond the settlement sigma every single cycle.
    committed = False
    for cycle in range(int(6.0 / DT)):
        snapshot = Snapshot(0.05, 0.05, speed=0.01)
        snapshot.x = 0.4 * ((-1) ** cycle)
        snapshot.y = 0.4 * ((-1) ** (cycle // 2))
        command = law.compute(snapshot, 0.0, DT, phase=RTLPhase.NAVIGATING)
        if command.ready_to_land:
            committed = True
            break

    assert committed, "dispersion alone must not keep the drone airborne"
    assert not command.arrived, "this scenario must not satisfy full settlement"


def test_the_commit_dwell_resets_when_the_drone_leaves_the_radius():
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)

    for _ in range(int(cfg.landing_commit_dwell_sec / DT) - 2):
        law.compute(Snapshot(0.05, speed=0.01), 0.0, DT)

    command = law.compute(Snapshot(2.0, speed=0.30), 0.0, DT)
    assert not command.ready_to_land, "a drone that flew off must re-earn the commit"


def test_moving_fast_inside_the_radius_does_not_commit():
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)
    for _ in range(int(3.0 / DT)):
        command = law.compute(
            Snapshot(0.05, speed=cfg.settle_max_speed_mps * 4.0), 0.0, DT
        )
    assert not command.ready_to_land


@pytest.mark.parametrize("phase", [RTLPhase.NAVIGATING, RTLPhase.STATION_KEEPING])
def test_the_vertical_and_yaw_invariants_survive_the_refactor(phase):
    law = guidance()
    for _ in range(60):
        command = law.compute(Snapshot(-1.0, 0.3), vz_command=0.9, dt=DT, phase=phase)
        assert command.vz <= 0.0
        assert command.vyaw == 0.0


# ═════════════════════════════════════════ the touchdown sequence itself


class LandingDrone:
    """Records the command stream and models whether the firmware obeys land()."""

    def __init__(self, obeys_land=True, descent_rate=0.35):
        self.no_fly = False
        self.obeys_land = obeys_land
        self.descent_rate = descent_rate
        self.land_calls = 0
        self.commands = []
        self.descending = False

    def land(self):
        self.land_calls += 1
        if self.obeys_land:
            self.descending = True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.commands.append((vx, vy, vz, vyaw))
        if vz < 0.0:
            self.descending = True

    def camera_control(self, tilt, pan):
        pass


class LandingOdometry:
    """Altitude that falls at a real rate once a descent has been commanded.

    The period matters: integrating per *call* rather than per second would make
    the modelled descent rate a function of how fast the control loop happens to
    run, which is exactly the coupling the flight code goes out of its way to
    avoid.
    """

    def __init__(self, drone, altitude=1.20, period=1.0 / 16.0):
        self._drone = drone
        self._period = period
        self.relative_altitude = altitude
        self.speed = 0.0
        self.x = self.y = 0.0

    def snapshot(self):
        if self._drone.descending:
            self.relative_altitude = max(
                0.0, self.relative_altitude - self._drone.descent_rate * self._period
            )
        return self

    @staticmethod
    def telemetry_health():
        from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


def landing_ctx(drone):
    import threading

    from mvp_mission_bebop.parameters import MissionParameters
    from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

    class Blackboard:
        rtl_completed = False

    class Ctx:
        pass

    ctx = Ctx()
    ctx.params = MissionParameters()
    # The touchdown loop is paced against the wall clock, so the windows are
    # scaled down rather than the assertions weakened: the sequencing under test
    # is the same, it simply runs in seconds instead of tens of them.
    ctx.params.kinematics.control_loop_hz = 200.0
    ctx.params.rtl.descent_stall_sec = 0.20
    ctx.params.rtl.touchdown_timeout_sec = 4.00
    ctx.drone = drone
    ctx.odom_supervisor = LandingOdometry(
        drone, period=1.0 / ctx.params.kinematics.control_loop_hz
    )
    ctx.failsafe = FailsafeSupervisor(
        drone_actuator=drone,
        odom_supervisor=ctx.odom_supervisor,
        timeouts_cfg=ctx.params.timeouts,
        kinematics_cfg=ctx.params.kinematics,
    )
    ctx.blackboard = Blackboard()
    ctx.emergency_event = threading.Event()
    ctx.handler = None
    return ctx


def test_a_normal_landing_is_commanded_and_confirmed():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=True)
    ctx = landing_ctx(drone)

    status = ClosedLoopRTLStep()._touchdown(ctx, None)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.rtl_completed, "a completed landing must be recorded as completed"
    assert drone.land_calls > 0
    assert ctx.odom_supervisor.relative_altitude <= ctx.params.rtl.touchdown_altitude_m


def test_a_firmware_that_ignores_land_still_gets_the_drone_down():
    """land() publishes an Empty and returns; nothing acknowledges it.

    Re-sending the same unacknowledged request more times cannot help if the
    firmware is not acting on it. The escalation commands the descent directly.
    """
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=False)
    ctx = landing_ctx(drone)

    ClosedLoopRTLStep()._touchdown(ctx, None)

    descents = [vz for _vx, _vy, vz, _vyaw in drone.commands if vz < 0.0]
    assert descents, "assisted descent was never commanded"
    assert all(vz >= -ctx.params.governor.max_descent_speed for vz in descents), (
        "assisted descent must still respect the governor's descent cap"
    )
    assert ctx.odom_supervisor.relative_altitude <= ctx.params.rtl.touchdown_altitude_m


def test_the_land_request_is_the_last_thing_on_the_wire():
    """A Twist arriving after the request can hold the firmware in piloting state."""
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=False)
    ctx = landing_ctx(drone)

    order = []
    original_land, original_move = drone.land, drone.move_velocity
    drone.land = lambda: (order.append("land"), original_land())[1]
    drone.move_velocity = lambda **kw: (order.append("move"), original_move(**kw))[1]

    ClosedLoopRTLStep()._touchdown(ctx, None)

    assert order[-1] == "land", "the landing request must be the final command sent"


def test_touchdown_never_commands_climb_or_yaw():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=False)
    ctx = landing_ctx(drone)
    ClosedLoopRTLStep()._touchdown(ctx, None)

    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in drone.commands)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in drone.commands)
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in drone.commands)


def test_a_slow_steady_descent_is_not_mistaken_for_touchdown():
    """Regression: the stagnation check used to confirm on the first centimetre.

    It compared consecutive samples, so any descent slower than one epsilon per
    cycle was indistinguishable from no descent at all -- and the counter had
    already been accumulating throughout the pre-descent stall, so the moment
    the drone started moving it reported itself landed, most of a metre up.
    """
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    # Descends, but slowly enough that no single cycle clears the epsilon.
    drone = LandingDrone(obeys_land=True, descent_rate=0.10)
    ctx = landing_ctx(drone)
    ctx.params.rtl.touchdown_timeout_sec = 1.0  # far too short to actually land

    ClosedLoopRTLStep()._touchdown(ctx, None)

    assert ctx.odom_supervisor.relative_altitude > ctx.params.rtl.touchdown_altitude_m
    assert not ctx.blackboard.rtl_completed, (
        "a drone still airborne must not be recorded as landed"
    )
