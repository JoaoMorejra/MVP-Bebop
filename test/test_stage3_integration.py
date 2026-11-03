"""Step-level integration test for Stage 3 in the lateral flight pattern.

The control law is pinned in ``test_lateral_servoing``. This proves the *step*
-- the loop that owns perception, the governor and the actuator boundary --
centres the target with lateral corrections alone and freezes, because that is
the layer the drone actually flies.

The scene is closed-loop. The stub camera renders the target's pixel position
from the current lateral offset and gimbal tilt, and the offset changes by
whatever the step transmits as ``vy``. The ground range is constant: the step
must never transmit along-track motion, and the scene ignores it so that a
regression shows up as a failed assertion on the command stream rather than as
a silently easier geometry.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import os
import threading

import pytest

import mvp_mission_bebop
from mvp_mission_bebop.controllers.visual_servoing import (
    ServoCommand,
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import SynchronousPerception
from mvp_mission_bebop.steps import tracking as tracking_module
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor

FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
ALTITUDE_M = 1.23
SHIPPED_CONFIG = os.path.join(os.path.dirname(mvp_mission_bebop.__file__), "mission_config.json")


class FakeClock:
    """Simulated time, so a long flight stage runs in milliseconds.

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
    """A stationary ground target at a constant range, observed through the gimbal."""

    def __init__(self, ground_range_m, altitude_m=ALTITUDE_M, lateral_m=0.0):
        self.ground_range_m = ground_range_m
        self.altitude_m = altitude_m
        self.lateral_m = lateral_m

    def advance(self, _vx_normalized, vy_normalized, dt):
        # ``lateral_m`` follows GroundProjection: positive is to the *left*. The
        # body frame is FLU, so +vy is also to the left, and flying left reduces
        # a leftward offset. ``vx`` is ignored on purpose: the range is constant.
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
    """Healthy telemetry at a held altitude, airframe at rest unless told otherwise."""

    def __init__(self, altitude_m):
        self.relative_altitude = altitude_m
        self.speed = 0.0
        self.horizontal_speed = 0.0
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
    def compute_vz(_altitude, _dt=None, vx_commanded=0.0):
        return 0.0


class Blackboard:
    target_confirmed = True
    approach_finished = False
    confirmed_tilt_deg = None
    confirmed_target_px = None


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
        #: Every pixel the stub detector reported, in order.
        self.detected_px = []
        self._dt = 1.0 / params.kinematics.control_loop_hz
        # Inline perception: one frame, one inference, one overlay per control
        # cycle, so the stage's frame-counting logic is exercised exactly.
        self.perception = SynchronousPerception(self)

    # -- perception ------------------------------------------------------
    def grab_frame(self, timeout_sec=1.0):
        # Integrate the previously transmitted command before rendering, so the
        # observation the step sees is a consequence of what the step commanded.
        if self.drone.commands:
            vx, vy, _vz, _vyaw = self.drone.commands[-1]
            self.scene.advance(vx, vy, self._dt)
        return object()

    #: Cycles over which the detector returns nothing. Set by the loss tests.
    blackout = range(0, 0)

    def interrupted(self) -> bool:
        """Mirrors MissionContext.interrupted: an abort or a commanded stage jump."""
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def detect(self, _frame, conf=0.5, imgsz=None):
        self._cycle = getattr(self, "_cycle", -1) + 1
        if self._cycle in self.blackout:
            return DetectionResult([])
        pixel = self.scene.pixel(self.current_tilt_deg)
        self.detected_px.append(pixel)
        return DetectionResult([Detection(pixel, 0.79)])

    def publish_annotated_stream(self, _frame, _result, text):
        self.overlays.append(text)
        return None

    # -- assertions ------------------------------------------------------
    def phases(self):
        return [
            line.split("STEP 3: ")[1].split()[0] for line in self.overlays if "STEP 3: " in line
        ]


def flight_params():
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.20
    params.kinematics.control_loop_hz = 16.0
    params.timeouts.tracking_timeout_sec = 25.0
    return params


def in_view_range(params, altitude_m=ALTITUDE_M):
    """Ground range that the search tilt centres vertically: ``h / tan(|tilt|)``.

    Stage 2 confirms the target with the camera at the search tilt, and Stage 3
    neither advances nor moves the gimbal, so this is the range the whole stage
    runs at.
    """
    return altitude_m / math.tan(math.radians(abs(params.gimbal.search_tilt_deg)))


def tolerance_px(params):
    return max(
        params.vision.optical_center_tolerance_px,
        params.vision.optical_center_tolerance_ratio * WIDTH,
    )


def offset_ctx(lateral_m=0.6, params=None):
    params = params or flight_params()
    return Ctx(params, Scene(ground_range_m=in_view_range(params), lateral_m=lateral_m))


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lateral_m", [0.6, -0.6], ids=["target-left", "target-right"])
def test_stage3_converges_with_lateral_corrections_only(simulated_time, lateral_m):
    ctx = offset_ctx(lateral_m)
    start_range = ctx.scene.ground_range_m

    status = VisualServoingStep().execute(ctx)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished is True
    assert ctx.drone.commands
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in ctx.drone.commands), "vx was transmitted"
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)
    assert any(vy != 0.0 for _vx, vy, _vz, _vyaw in ctx.drone.commands), "never moved laterally"
    assert ctx.scene.ground_range_m == start_range
    assert abs(ctx.scene.lateral_m) < abs(lateral_m)


def test_the_airframe_is_frozen_on_the_cycle_the_stage_completes(simulated_time):
    """The freeze is transmitted in the lock cycle itself, not a cycle later."""
    ctx = offset_ctx()
    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    vx, vy, _vz, _vyaw = ctx.drone.commands[-1]
    assert vx == 0.0 and vy == 0.0
    assert ctx.phases()[-1] == TrackingPhase.LOCKED.value.upper()
    lock_cycle = ctx.drone.commands[-2]
    assert lock_cycle[0] == 0.0 and lock_cycle[1] == 0.0, f"lock cycle transmitted {lock_cycle}"


def test_the_gimbal_is_not_commanded_in_stage_3(simulated_time):
    """D2: the gimbal belongs to Stage 4."""
    ctx = offset_ctx()
    ctx.blackout = range(10, 110)
    VisualServoingStep().execute(ctx)

    assert ctx.drone.tilts == []
    assert ctx.current_tilt_deg == ctx.params.gimbal.search_tilt_deg


@pytest.mark.parametrize("lateral_m", [0.6, -0.6], ids=["target-left", "target-right"])
def test_a_laterally_offset_target_is_centred_on_either_side(simulated_time, lateral_m):
    ctx = offset_ctx(lateral_m)
    first_px = ctx.scene.pixel(ctx.current_tilt_deg)[0]
    assert abs(first_px - WIDTH / 2.0) > tolerance_px(ctx.params), "premise: starts off-centre"

    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    last_x = ctx.detected_px[-1][0]
    assert abs(last_x - WIDTH / 2.0) <= tolerance_px(ctx.params)


def test_the_stage_completes_well_inside_its_deadline(simulated_time):
    """With the shipped lateral gains (output limit +/-0.04) a 0.6 m offset converges."""
    params = flight_params()
    params.lateral_pid = MissionParameters.load_from_file(SHIPPED_CONFIG).lateral_pid
    assert params.lateral_pid.output_limits[1] == pytest.approx(0.04), "premise: shipped gains"
    ctx = offset_ctx(0.6, params)

    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    assert simulated_time.now < params.timeouts.tracking_timeout_sec, (
        f"took {simulated_time.now:.1f} s of a {params.timeouts.tracking_timeout_sec:.0f} s window"
    )


def test_a_brief_occlusion_does_not_trigger_reacquisition(simulated_time):
    """The alpha-beta coast absorbs a ten-frame dropout on its own, silently."""
    ctx = offset_ctx()
    ctx.blackout = range(10, 20)

    status = VisualServoingStep().execute(ctx)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished
    assert "REACQUIRING" not in ctx.phases()
    assert any("[PREDICTED]" in line for line in ctx.overlays), "premise: the coast ran"
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in ctx.drone.commands)


def test_the_step_recovers_from_a_dropout_instead_of_ending(simulated_time):
    """A loss that outlasts the coast escalates to REACQUIRING and comes back."""
    ctx = offset_ctx()
    ctx.blackout = range(10, 110)

    status = VisualServoingStep().execute(ctx)

    phases = ctx.phases()
    assert status.name == "SUCCESS"
    assert "REACQUIRING" in phases, "recovery never engaged"
    resumed = phases.index("REACQUIRING")
    assert "CENTERING" in phases[resumed:], "never resumed centering"
    assert ctx.blackboard.approach_finished, "the stage did not survive the dropout"
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in ctx.drone.commands)


def test_a_permanent_loss_terminates_with_approach_unfinished(simulated_time):
    """Recovery is bounded: a retry, not a new way to hang."""
    ctx = offset_ctx()
    ctx.blackout = range(10, 100_000)

    status = VisualServoingStep().execute(ctx)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished is False
    assert simulated_time.now < ctx.params.timeouts.tracking_timeout_sec, (
        "recovery must end on its own window, not on the stage deadline"
    )
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in ctx.drone.commands)


class ExhaustingController:
    """Real controller whose observation path reports an exhausted reacquisition.

    ``VisualServoingController._reacquired`` reports exhaustion on frames that
    carry an unconfirmed observation; reproducing that through the tracker's
    coast in a closed-loop scene depends on fragile timing, so the report is
    injected at the step boundary instead, from the ``exhaust_at``-th call on.
    """

    def __init__(self, inner, exhaust_at):
        self._inner = inner
        self._exhaust_at = exhaust_at
        self.calls = 0

    def reset(self):
        self._inner.reset()

    def note_target_lost(self, current_tilt_deg, dt):
        return self._inner.note_target_lost(current_tilt_deg, dt)

    def compute(self, **kwargs):
        self.calls += 1
        command = self._inner.compute(**kwargs)
        if self.calls < self._exhaust_at:
            return command
        return dataclasses.replace(
            command,
            vy=0.0,
            phase=TrackingPhase.REACQUIRING,
            locked=False,
            recovery_exhausted=True,
        )


def test_an_exhaustion_reported_with_an_observation_ends_the_stage_unfinished(
    simulated_time, caplog
):
    """The stage stops on the report instead of running on to its deadline."""
    ctx = offset_ctx()
    controller = ExhaustingController(ctx.visual_controller, exhaust_at=5)
    ctx.visual_controller = controller

    # Attached directly: the mission's logging setup may stop this logger from
    # propagating to the root handler caplog normally listens on.
    step_logger = logging.getLogger("Step3Tracking")
    step_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="Step3Tracking"):
            status = VisualServoingStep().execute(ctx)
    finally:
        step_logger.removeHandler(caplog.handler)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished is False
    assert controller.calls == 5, "the loop kept servoing after the exhaustion report"
    assert simulated_time.now < 0.1 * ctx.params.timeouts.tracking_timeout_sec
    assert "without a lateral lock" in caplog.text
    vx, vy, _vz, _vyaw = ctx.drone.commands[-1]
    assert vx == 0.0 and vy == 0.0


def test_an_emergency_aborts_the_stage_without_commanding_motion(simulated_time):
    ctx = offset_ctx()
    ctx.emergency_event.set()

    status = VisualServoingStep().execute(ctx)

    assert status.name == "ABORTED"
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in ctx.drone.commands)


def test_the_vertical_invariant_holds_for_every_transmitted_command(simulated_time):
    """vz <= 0 across the whole of Stage 3, through a dropout and its recovery."""
    ctx = offset_ctx()
    ctx.blackout = range(10, 110)
    VisualServoingStep().execute(ctx)

    assert ctx.drone.commands
    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in ctx.drone.commands)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)


class DecimatedPerception(SynchronousPerception):
    """Camera at the loop rate, detector at a fraction of it.

    Every control cycle still grabs a frame -- which is what advances the
    simulated scene -- but only every ``stride``-th one is run through the
    detector. The cycles in between read the previous sample back, exactly as
    the loop does against the background worker while an inference is in
    flight.
    """

    def __init__(self, ctx, stride):
        super().__init__(ctx)
        self._stride = stride
        self._calls = 0

    def get_latest(self, max_age_sec):
        self._calls += 1
        if self._calls % self._stride != 1 and self.slot.latest() is not None:
            self._ctx.grab_frame(timeout_sec=self._frame_timeout)
            return self.slot.latest()
        return super().get_latest(max_age_sec)


def test_the_servo_converges_when_inference_lags_the_control_loop(simulated_time):
    """Inference at a third of the loop rate: the law runs once per new frame."""
    ctx = offset_ctx()
    ctx.perception = DecimatedPerception(ctx, stride=3)

    status = VisualServoingStep().execute(ctx)

    assert status.name == "SUCCESS"
    assert ctx.blackboard.approach_finished
    assert len(ctx.overlays) * 3 <= len(ctx.drone.commands) + 3, (
        "the control law ran on repeated observations"
    )
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in ctx.drone.commands)
    assert all(vz <= 0.0 for _vx, _vy, vz, _vyaw in ctx.drone.commands)


class RecordingGovernor:
    def __init__(self):
        self.calls = []

    def compute_vz(self, altitude, dt=None, vx_commanded=0.0):
        self.calls.append(vx_commanded)
        return 0.0

    def horizontal_scale(self):
        return 1.0


def test_tracking_apply_feeds_the_horizontal_demand_to_the_governor():
    """D5: the governor's feedforward is the norm of the horizontal demand, here |vy|."""
    ctx = offset_ctx()
    ctx.governor = RecordingGovernor()
    command = ServoCommand(
        vx=0.0,
        vy=-0.035,
        tilt_deg=ctx.current_tilt_deg,
        pixel_error=60.0,
        ground_range_m=None,
        depression_deg=None,
        phase=TrackingPhase.CENTERING,
        laterally_centered=False,
        locked=False,
        note="",
    )

    VisualServoingStep()._apply(ctx, command, dt=0.05)

    assert ctx.governor.calls == [pytest.approx(0.035)]
    vx, vy, _vz, _vyaw = ctx.drone.commands[-1]
    assert vx == 0.0
    assert vy == pytest.approx(-0.035), "the servo must be able to correct to the right"


def test_the_lock_publishes_the_last_pixel_and_tilt_for_stage_4(simulated_time):
    """Review Focus 7: Stage 4's open-loop pointing solution starts from here."""
    ctx = offset_ctx()
    ctx.blackboard.confirmed_target_px = (1.0, 1.0)
    ctx.blackboard.confirmed_tilt_deg = 0.0

    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    assert ctx.blackboard.confirmed_target_px == pytest.approx(ctx.detected_px[-1])
    assert ctx.blackboard.confirmed_tilt_deg == pytest.approx(ctx.params.gimbal.search_tilt_deg)


class SettlingOdometry(Odometry):
    """Ground speed above the stillness threshold until ``settle_at`` simulated seconds."""

    def __init__(self, altitude_m, clock, settle_at, moving_mps):
        super().__init__(altitude_m)
        self._clock = clock
        self._settle_at = settle_at
        self._moving_mps = moving_mps

    @property
    def horizontal_speed(self):
        return self._moving_mps if self._clock.now < self._settle_at else 0.0

    @horizontal_speed.setter
    def horizontal_speed(self, _value):
        pass


def test_a_stationary_check_gates_the_lock(simulated_time):
    params = flight_params()
    settle_at = 3.0
    ctx = Ctx(params, Scene(ground_range_m=in_view_range(params), lateral_m=0.0))
    ctx.odom_supervisor = SettlingOdometry(
        ALTITUDE_M, simulated_time, settle_at, 2.0 * params.inspection.settle_max_speed_mps
    )
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor

    VisualServoingStep().execute(ctx)

    assert ctx.blackboard.approach_finished
    assert simulated_time.now >= settle_at, (
        f"locked at {simulated_time.now:.2f} s while the airframe was still moving"
    )
    waiting = [line for line in ctx.overlays if "CENTERING" in line]
    assert len(waiting) > params.vision.alignment_dwell_cycles, "premise: credit was full early"
