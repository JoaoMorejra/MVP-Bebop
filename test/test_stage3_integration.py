"""Step-level integration test for Stage 3 against the flight's own scenario.

``test_centering_recovery`` proves the *control law* escapes the centering
deadlock. This proves the *step* does -- the loop that owns perception, the
gimbal, the governor and the actuator boundary -- because that is the layer the
drone actually flew, and a control law that converges in isolation is worth
nothing if the step around it never transmits its output.

The scene is closed-loop. The stub camera renders the target's pixel position
from the current range and gimbal tilt, and the range shortens by whatever the
step transmits as ``vx``. So the only way this test reaches nadir is if the step
really does drive the airframe over the target.
"""

from __future__ import annotations

import math
import threading

import pytest

from mvp_mission_bebop.controllers.visual_servoing import (
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import tracking as tracking_module
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
ALTITUDE_M = 1.23


class FakeClock:
    """Simulated time, so a sixty-second flight stage runs in milliseconds.

    ``LoopRate`` and ``Deadline`` both accept an injected clock precisely for
    this, but the step constructs them itself, so the classes are substituted in
    the step's own namespace instead. The pacing logic under test is the real
    one; only the passage of time is simulated.
    """

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture
def simulated_time(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(
        tracking_module,
        "LoopRate",
        lambda hz, **kw: LoopRate(hz, clock=clock, sleeper=clock.sleep),
    )
    monkeypatch.setattr(
        tracking_module,
        "Deadline",
        lambda seconds, **kw: Deadline(seconds, clock=clock),
    )
    return clock


class Detection:
    """The two attributes the step reads off a detection."""

    def __init__(self, center, confidence):
        self.center = center
        self.confidence = confidence


class DetectionResult:
    def __init__(self, detections):
        self._detections = detections

    def filter_by_class(self, _classes):
        return self._detections


class Scene:
    """A stationary ground target, observed through a steerable gimbal."""

    def __init__(self, ground_range_m, altitude_m=ALTITUDE_M, lateral_m=0.0):
        self.ground_range_m = ground_range_m
        self.altitude_m = altitude_m
        self.lateral_m = lateral_m

    def advance(self, vx_normalized, vy_normalized, dt):
        self.ground_range_m = max(0.0, self.ground_range_m - vx_normalized * dt)
        # ``lateral_m`` follows GroundProjection: positive is to the *left*. The
        # body frame is FLU, so +vy is also to the left, and flying left reduces
        # a leftward offset.
        self.lateral_m -= vy_normalized * dt

    def pixel(self, tilt_deg):
        depression = math.atan2(self.altitude_m, max(self.ground_range_m, 1e-3))
        theta_y = depression - math.radians(-tilt_deg)
        # Negated, matching project_to_ground: a pixel right of the optical axis
        # corresponds to a *negative* (rightward) lateral offset.
        theta_x = math.atan2(-self.lateral_m, math.hypot(self.ground_range_m, self.altitude_m))
        return (
            WIDTH / 2.0 + (WIDTH / 2.0) * math.tan(theta_x) / math.tan(math.radians(40.0)),
            HEIGHT / 2.0 + (HEIGHT / 2.0) * math.tan(theta_y) / math.tan(math.radians(25.0)),
        )


class Drone:
    def __init__(self):
        self.no_fly = False
        self.commands = []
        self.tilts = []

    def camera_control(self, tilt, pan):
        self.tilts.append(tilt)

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.commands.append((vx, vy, vz, vyaw))


class Odometry:
    """Healthy telemetry at a held altitude. The vertical axis is not on trial here."""

    def __init__(self, altitude_m):
        self.relative_altitude = altitude_m
        self.speed = 0.0
        self.x = self.y = 0.0

    def snapshot(self):
        return self

    @staticmethod
    def telemetry_health():
        from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


class Governor:
    """Descent-only, as the real one is; the vertical axis is not on trial here."""

    engaged = False

    @staticmethod
    def compute_vz(_altitude, _dt=None):
        return 0.0


class Blackboard:
    target_confirmed = True
    approach_finished = False


class Ctx:
    """The attribute surface ``VisualServoingStep.execute`` touches."""

    def __init__(self, params, scene):
        self.params = params
        self.scene = scene
        self.drone = Drone()
        self.odom_supervisor = Odometry(scene.altitude_m)
        self.governor = Governor()
        self.failsafe = FailsafeSupervisor(
            drone_actuator=self.drone,
            odom_supervisor=self.odom_supervisor,
            timeouts_cfg=params.timeouts,
            kinematics_cfg=params.kinematics,
        )
        self.visual_controller = VisualServoingController(
            gimbal_config=params.gimbal,
            gimbal_pid_cfg=params.gimbal_pid,
            lateral_pid_cfg=params.lateral_pid,
            vision_cfg=params.vision,
            kinematics_cfg=params.kinematics,
            calibration=SpeedCalibration(1.0),
        )
        self.blackboard = Blackboard()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.requested_stage = None
        self.frame_width, self.frame_height = FRAME
        self.current_tilt_deg = params.gimbal.search_tilt_deg
        self.detector = self
        self.overlays = []
        self._dt = 1.0 / params.kinematics.control_loop_hz

    # -- perception ------------------------------------------------------
    def grab_frame(self, timeout_sec=1.0):
        # Integrate the previously transmitted command before rendering, so the
        # observation the step sees is a consequence of what the step commanded.
        if self.drone.commands:
            vx, vy, _vz, _vyaw = self.drone.commands[-1]
            self.scene.advance(vx, vy, self._dt)
        return object()

    #: Cycles over which the detector returns nothing, simulating the dropout
    #: that happens near nadir. Set by the loss test.
    blackout = range(0, 0)

    def interrupted(self) -> bool:
        """Mirrors MissionContext.interrupted: an abort or a commanded stage jump."""
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def detect(self, _frame, conf=0.5):
        self._cycle = getattr(self, "_cycle", -1) + 1
        if self._cycle in self.blackout:
            return DetectionResult([])
        return DetectionResult([Detection(self.scene.pixel(self.current_tilt_deg), 0.79)])

    def publish_annotated_stream(self, _frame, _result, text):
        self.overlays.append(text)
        return None


def flight_params():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.20
    params.kinematics.control_loop_hz = 16.0
    return params


# ---------------------------------------------------------------------------


def inspection_standoff(params, altitude_m=ALTITUDE_M):
    """Ground range the gimbal frames when it reaches the inspection attitude.

    The stage no longer flies the drone onto the target. It closes the range
    until the camera has pitched to ``nadir_tilt_deg``, which by construction is
    the moment the optical axis lands on the subject -- ``h / tan(|tilt|)``
    ahead -- and freezes there. So the arrival criterion is this distance, not
    the old 0.25 m "almost on top of it" threshold.
    """
    return altitude_m / math.tan(math.radians(abs(params.gimbal.nadir_tilt_deg)))


def test_stage3_drives_the_airframe_to_the_inspection_standoff(simulated_time):
    """The whole stage, from where the real flight stalled, to the freeze.

    Eight metres out at 1.23 m -- the geometry that produced a sixty-second
    hover at ``vx = 0``.
    """
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))

    status = VisualServoingStep().execute(ctx)

    standoff = inspection_standoff(ctx.params)
    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished, "the stage never confirmed the inspection attitude"
    assert ctx.scene.ground_range_m <= standoff + 0.10, (
        f"stopped {ctx.scene.ground_range_m:.2f} m out against a {standoff:.2f} m standoff"
    )
    assert ctx.scene.ground_range_m >= standoff - 0.15, (
        f"overflew the scene to {ctx.scene.ground_range_m:.2f} m; the gimbal cannot follow "
        f"a subject closer than the {standoff:.2f} m its stop frames"
    )


def test_the_airframe_is_stationary_once_the_stage_reports_complete(simulated_time):
    """Requirement: reaching the inspection tilt freezes the drone where it is.

    Stage 4 verifies motionlessness statistically before it triggers the 14 MP
    capture, and every command still in flight when Stage 3 hands over is
    something that verification has to wait out. Asserted on the command stream
    rather than on a controller flag, because the command stream is what the
    airframe sees.
    """
    ctx = Ctx(flight_params(), Scene(ground_range_m=6.0))
    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    tail = ctx.drone.commands[-4:]
    assert tail, "the stage transmitted nothing"
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in tail), (
        f"the airframe was still translating when the stage finished: {tail}"
    )


def test_the_gimbal_never_goes_past_the_inspection_attitude(simulated_time):
    """It must not go all the way down to -80: -69 is a hard mechanical stop."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    assert min(ctx.drone.tilts) >= ctx.params.gimbal.nadir_tilt_deg - 1e-6
    assert min(ctx.drone.tilts) > -80.0


def test_forward_cruise_is_transmitted_not_merely_computed(simulated_time):
    """The failure was a step that never sent a non-zero vx to the actuator."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    forward = [vx for vx, _vy, _vz, _vyaw in ctx.drone.commands]
    assert max(forward) > 0.0, "no forward velocity ever reached the drone"
    assert max(forward) <= ctx.params.kinematics.max_horizontal_speed


def test_the_gimbal_walks_from_search_attitude_to_nadir(simulated_time):
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    assert ctx.drone.tilts, "the gimbal was never commanded"
    assert ctx.drone.tilts[-1] == pytest.approx(ctx.params.gimbal.nadir_tilt_deg, abs=2.5)
    assert min(ctx.drone.tilts) >= ctx.params.gimbal.nadir_tilt_deg - 1e-6, (
        "the gimbal exceeded its mechanical nadir limit"
    )


def test_the_vertical_invariant_holds_for_every_transmitted_command(simulated_time):
    """Requirement 1: vz <= 0 across the whole of Stage 3, without exception."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    assert ctx.drone.commands
    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in ctx.drone.commands)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)


def test_a_laterally_offset_target_is_still_reached(simulated_time):
    """The cross-track loop has to actually move the airframe, not just demand it."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=6.0, lateral_m=1.2))
    VisualServoingStep().execute(ctx)

    assert abs(ctx.scene.lateral_m) < abs(1.2), "the lateral offset was never reduced"
    assert ctx.blackboard.approach_finished


def test_the_stage_completes_well_inside_its_deadline(simulated_time):
    """A stage that only finishes at the timeout has not really converged."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    cycles = len(ctx.drone.commands)
    elapsed = cycles / ctx.params.kinematics.control_loop_hz
    assert ctx.blackboard.approach_finished
    assert elapsed < ctx.params.timeouts.tracking_timeout_sec * 0.75, (
        f"took {elapsed:.1f} s of a {ctx.params.timeouts.tracking_timeout_sec:.0f} s window"
    )


def test_the_window_covers_the_range_the_search_stage_can_hand_over(simulated_time):
    """The stage deadline has to be sized from the geometry it must cover.

    Stage 2 confirms a target as soon as three frames clear the confidence
    threshold, which for a bicycle at 1.2 m AGL is routinely eight metres out
    and can be considerably more. At the 0.15 approach cap the old 60 s window
    bought 9.0 m of travel, so a target confirmed past roughly 8.5 m could not
    be reached inside it no matter how well the control law performed -- the
    stage would have ended in a timeout that looked exactly like the centering
    deadlock it had just been fixed for.
    """
    params = flight_params()
    reachable = params.timeouts.tracking_timeout_sec * params.kinematics.max_approach_forward_speed
    assert reachable >= 15.0, (
        f"the window covers only {reachable:.1f} m of approach; Stage 2 hands over from further"
    )

    ctx = Ctx(params, Scene(ground_range_m=14.0))
    VisualServoingStep().execute(ctx)
    assert ctx.blackboard.approach_finished, "a 14 m standoff was not reachable in the window"


def test_an_emergency_aborts_the_stage_without_commanding_motion(simulated_time):
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    ctx.emergency_event.set()

    status = VisualServoingStep().execute(ctx)

    assert status.name == "ABORTED"
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in ctx.drone.commands)


def test_phase_progression_is_monotone_through_the_approach(simulated_time):
    """Centering, then approaching, then nadir -- and the phases are reached."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=8.0))
    VisualServoingStep().execute(ctx)

    reached = [line.split("STEP 3: ")[1].split()[0] for line in ctx.overlays if "STEP 3: " in line]
    assert TrackingPhase.CENTERING.value.upper() in reached
    assert TrackingPhase.APPROACHING.value.upper() in reached
    assert TrackingPhase.NADIR.value.upper() in reached
    assert reached.index(TrackingPhase.APPROACHING.value.upper()) < reached.index(
        TrackingPhase.NADIR.value.upper()
    )


def test_the_step_recovers_from_a_nadir_dropout_instead_of_ending(simulated_time):
    """The flight defect: a dropout near nadir ended the approach outright.

    Near the inspection attitude the footprint is small and the object is
    foreshortened into an aspect ratio the detector never trained on, so losing
    it there is expected. The step used to break out of its loop and
    report an unfinished approach, discarding a target it was directly above.
    """
    ctx = Ctx(flight_params(), Scene(ground_range_m=3.0))
    # The blackout has to outlast the tracker's coast horizon before recovery is
    # even reached: an alpha-beta extrapolation covers short dropouts on its own,
    # and that layer is working as intended. Eight seconds of nothing is what it
    # takes to get past it.
    ctx.blackout = range(60, 200)

    VisualServoingStep().execute(ctx)

    assert any("REACQUIRING" in line for line in ctx.overlays), "recovery never engaged"
    assert ctx.blackboard.approach_finished, "the approach did not survive the dropout"
    assert ctx.scene.ground_range_m <= inspection_standoff(ctx.params) + 0.10


def test_a_permanent_loss_still_terminates(simulated_time):
    """Recovery must be bounded: it is a retry, not a new way to hang."""
    ctx = Ctx(flight_params(), Scene(ground_range_m=3.0))
    ctx.blackout = range(60, 10_000)

    status = VisualServoingStep().execute(ctx)

    assert status.name == "SUCCESS"
    assert not ctx.blackboard.approach_finished
    cycles = len(ctx.drone.commands)
    elapsed = cycles / ctx.params.kinematics.control_loop_hz
    assert elapsed < ctx.params.timeouts.tracking_timeout_sec, (
        "recovery must end on its own window, not on the stage deadline"
    )


def test_recovery_never_violates_the_vertical_invariant(simulated_time):
    ctx = Ctx(flight_params(), Scene(ground_range_m=3.0))
    ctx.blackout = range(60, 120)
    VisualServoingStep().execute(ctx)

    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in ctx.drone.commands)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)
