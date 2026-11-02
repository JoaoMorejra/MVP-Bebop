"""Regression tests for target reacquisition and terminal touchdown.

Two flight-observed defects, both of which ended with the airframe doing nothing
useful for a long time:

*Stage 3 abandoned a target on the first sustained loss.* The step treated a
lost detection as the end of the stage. Reacquisition now holds the tilt and, in
the lateral flight pattern, creeps sideways toward the edge the target left
through -- never along-track -- before giving up on a bounded window.

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


# ═══════════════════════════════════════════════ target loss in Stage 3



@pytest.fixture(autouse=True)
def _lens_frame_centering(monkeypatch):
    """Pin the centering law in the lens frame (camera-to-CoG offset zero).

    These tests simulate the marker relative to the camera and exercise the
    law's dynamics, gates and recovery; the camera-to-CoG extrinsic that
    shifts the equilibrium is covered by ``test_camera_extrinsics.py``.
    """
    from mvp_mission_bebop.controllers import rtl_guidance as _rtl_guidance

    monkeypatch.setattr(_rtl_guidance, "_camera_offset", lambda _cfg, _name: 0.0)

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


def tracking(controller, px_x=CENTER_X, cycles=6):
    """Servo on a target at ``px_x`` without letting the freeze latch."""
    command = None
    for _ in range(cycles):
        command = controller.compute(
            (px_x, CENTER_Y), FRAME, SEARCH_TILT_DEG, 1.20, DT, airframe_at_rest=False
        )
    return command


def test_losing_the_target_does_not_end_the_stage():
    controller = build_controller()
    tracking(controller)

    command = controller.note_target_lost(SEARCH_TILT_DEG, DT)

    assert controller.phase is TrackingPhase.REACQUIRING
    assert not command.recovery_exhausted, "recovery must not give up on the first lost frame"


def test_a_loss_at_the_centreline_holds_position():
    """No side to creep toward, so no motion on either axis and no tilt change."""
    controller = build_controller()
    tracking(controller)

    for _ in range(int(3.0 / DT)):
        command = controller.note_target_lost(SEARCH_TILT_DEG, DT)
        assert command.vy == 0.0, "no lateral command without a side to creep toward"
        assert command.vx == 0.0, "recovery never translates along-track"
        assert command.tilt_deg == SEARCH_TILT_DEG


def test_recovery_creeps_toward_the_exit_side_then_holds():
    """The target left through the right edge: creep right, bounded, then stop."""
    controller = build_controller()
    tracking(controller, px_x=820.0)
    cfg = VisionConfig()

    creep = []
    elapsed = 0.0
    while elapsed < cfg.reacquire_reverse_sec + 1.5:
        command = controller.note_target_lost(SEARCH_TILT_DEG, DT)
        elapsed += DT
        creep.append((elapsed, command.vy))
        assert command.vx == 0.0

    early = [vy for t, vy in creep if t < cfg.reacquire_reverse_sec - DT]
    late = [vy for t, vy in creep if t > cfg.reacquire_reverse_sec + DT]

    assert any(vy < 0.0 for vy in early), "the creep must actually run"
    assert all(vy <= 0.0 for vy in early), "and only toward the exit side"
    assert sum(early) / len(early) >= -cfg.reacquire_reverse_speed - 0.015, "and stay bounded"
    assert all(vy == 0.0 for vy in late), "then stop, rather than creeping indefinitely"


def test_regaining_the_target_resumes_centering():
    controller = build_controller()
    tracking(controller, px_x=820.0)

    for _ in range(int(2.0 / DT)):
        controller.note_target_lost(SEARCH_TILT_DEG, DT)
    assert controller.phase is TrackingPhase.REACQUIRING

    for _ in range(VisionConfig().reacquire_confirm_frames):
        tracking(controller, px_x=700.0, cycles=1)

    assert controller.phase is TrackingPhase.CENTERING


def test_a_single_frame_does_not_end_recovery():
    """One detection is what sent the tracker chasing phantoms before."""
    controller = build_controller()
    tracking(controller)
    for _ in range(int(1.0 / DT)):
        controller.note_target_lost(SEARCH_TILT_DEG, DT)

    tracking(controller, cycles=1)
    assert controller.phase is TrackingPhase.REACQUIRING, "one frame is not a reacquisition"


def test_a_predicted_frame_does_not_confirm_a_reacquisition():
    controller = build_controller()
    tracking(controller)
    controller.note_target_lost(SEARCH_TILT_DEG, DT)

    for _ in range(VisionConfig().reacquire_confirm_frames * 3):
        controller.compute(
            (CENTER_X, CENTER_Y), FRAME, SEARCH_TILT_DEG, 1.20, DT, measured=False
        )
    assert controller.phase is TrackingPhase.REACQUIRING


def test_recovery_is_bounded_and_reports_exhaustion():
    controller = build_controller()
    tracking(controller, px_x=820.0)
    cfg = VisionConfig()

    elapsed = 0.0
    command = None
    while elapsed < cfg.reacquire_timeout_sec + 1.0:
        command = controller.note_target_lost(SEARCH_TILT_DEG, DT)
        elapsed += DT

    assert command.recovery_exhausted, "recovery must terminate rather than hold forever"
    assert command.vx == 0.0 and command.vy == 0.0, "an exhausted recovery must not translate"


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
    """The flight failure: past the origin, outbound prohibited, distance frozen.

    Holding the return axis meant the distance never changed, so arrival could
    never confirm and the drone hovered over the takeoff point until the whole
    return window expired. The return is flown to the right, so the overshoot
    leaves the origin to the left of the airframe.
    """
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)

    ey = 0.35  # origin this far to the left: outside the arrival radius
    committed_at = None
    for cycle in range(int(30.0 / DT)):
        command = law.compute(Snapshot(0.0, ey), 0.0, DT, phase=RTLPhase.NAVIGATING)
        ey -= command.vy * DT
        if command.ready_to_land:
            committed_at = cycle * DT
            break

    assert committed_at is not None, "never committed to landing"
    assert committed_at < 15.0, f"took {committed_at:.1f} s to commit"
    assert ey <= cfg.arrival_radius_m


def test_leftward_recovery_never_becomes_leftward_flight():
    cfg = ReturnToLaunchConfig()
    law = guidance(cfg)
    peak = max(
        law.compute(Snapshot(0.0, 1.0), 0.0, DT, phase=RTLPhase.NAVIGATING).vy
        for _ in range(200)
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
    ctx.stage_jump_event = threading.Event()
    ctx.requested_stage = None
    # Mirrors MissionContext.interrupted: an abort, or a commanded stage jump.
    ctx.interrupted = lambda: (
        ctx.emergency_event.is_set() or ctx.stage_jump_event.is_set()
    )
    ctx.handler = None
    return ctx


def test_a_normal_landing_is_commanded_and_confirmed():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=True)
    ctx = landing_ctx(drone)

    status = ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

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

    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

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

    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

    assert order[-1] == "land", "the landing request must be the final command sent"


def test_touchdown_never_commands_climb_or_yaw():
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=False)
    ctx = landing_ctx(drone)
    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

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

    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

    assert ctx.odom_supervisor.relative_altitude > ctx.params.rtl.touchdown_altitude_m
    assert not ctx.blackboard.rtl_completed, (
        "a drone still airborne must not be recorded as landed"
    )


@pytest.fixture
def milestones(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "mvp_mission_bebop.steps.rtl.emit_milestone",
        lambda key, payload=None: emitted.append((key, dict(payload or {}))),
    )
    return emitted


@pytest.mark.parametrize("at_base", [True, False])
def test_a_confirmed_touchdown_reports_its_outcome(milestones, at_base):
    """``mission.touchdown`` is what the station's landing narration keys on."""
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=True)
    ctx = landing_ctx(drone)

    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=at_base)

    keys = [key for key, _ in milestones]
    assert keys == ["mission.landing", "mission.touchdown"]
    payload = milestones[-1][1]
    assert payload["confirmed"] is True
    assert payload["at_base"] is at_base
    assert payload["method"] in ("settled", "stagnation")
    assert ctx.blackboard.touchdown_confirmed is True


def test_an_unconfirmed_touchdown_is_reported_as_such(milestones):
    """Regression: an unconfirmed landing announced itself and exited as a success."""
    from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

    drone = LandingDrone(obeys_land=True, descent_rate=0.10)
    ctx = landing_ctx(drone)
    ctx.params.rtl.touchdown_timeout_sec = 1.0

    ClosedLoopRTLStep()._touchdown(ctx, None, at_base=True)

    payload = milestones[-1][1]
    assert milestones[-1][0] == "mission.touchdown"
    assert payload == {"confirmed": False, "at_base": True, "method": "unconfirmed", "battery_return": False}
    assert ctx.blackboard.touchdown_confirmed is False
