"""Unit tests for the Stage 1 ascent to the configured operating altitude.

The Bebop firmware ends its launch profile at its own hover height and ignores
the altitude the SDK was handed, so every mission configured above that used to
fly its whole profile a metre low. These tests exercise the closed-loop climb
that closes the gap, and the invariant that it is the only phase allowed to.
"""

import threading
import types
import time

import pytest

from mvp_mission_bebop.actuators import simulator as simulator_module
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.simulator import KinematicSimulator
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class StubDrone:
    """Stands in for the Nectar drone; the proxy never reaches it in no-fly."""

    def __init__(self):
        self.calls = []

    def flat_trim(self):
        self.calls.append(("flat_trim",))

    def takeoff(self, altitude):
        self.calls.append(("takeoff", altitude))
        return True

    def land(self, timeout=10.0):
        self.calls.append(("land",))
        return True

    def camera_control(self, tilt, pan):
        self.calls.append(("camera_control", tilt, pan))

    def snapshot(self):
        self.calls.append(("snapshot",))

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append(("move_velocity", vx, vy, vz, vyaw))

    def delay(self, seconds):
        self.calls.append(("delay", seconds))

    def connect(self):
        return True

    def cleanup(self):
        self.calls.append(("cleanup",))


class StubHandler:
    """No frames. The no-fly path skips the camera health gate anyway."""

    @staticmethod
    def take_photo(timeout_sec=1.0):
        return None


class ClimbContext:
    """The attribute surface ``TakeoffStep._ascend`` actually touches.

    A real ``MissionContext`` drags in CvBridge and a live ROS publisher, which
    the ascent never uses. Duck-typing keeps the test on the control law.
    """

    def __init__(self, params, drone, odom_supervisor, failsafe):
        self.params = params
        self.drone = drone
        self.odom_supervisor = odom_supervisor
        self.failsafe = failsafe
        self.handler = StubHandler()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.requested_stage = None
        self.speed_calibration = SpeedCalibration(params.kinematics.normalized_to_mps)

    def interrupted(self) -> bool:
        """Mirrors MissionContext.interrupted: an abort or a commanded stage jump."""
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def grab_frame(self, timeout_sec=1.0):
        """Mirror ``MissionContext.grab_frame``.

        Perception now goes through the context rather than straight to the
        handler, because ``ImageHandler.take_photo`` silently returns a cached
        frame against this camera and so can never report a stream loss.
        """
        return self.handler.take_photo(timeout_sec=timeout_sec)


class OvershootingSupervisor(OdometrySupervisor):
    """Reports a ceiling breach after a set number of checks.

    Stands in for the firmware driving the airframe through its ceiling mid
    ascent -- the one fault the climb has to catch while altitude can still be
    given back.
    """

    def __init__(self, *args, breach_after, **kwargs):
        super().__init__(*args, **kwargs)
        self._checks = 0
        self._breach_after = breach_after

    def is_ceiling_breached(self):
        self._checks += 1
        return self._checks > self._breach_after


def climb_params(target=1.80):
    """Parameters tuned so the ascent converges in about a second of real time."""
    params = MissionParameters()
    kinematics = params.kinematics
    kinematics.target_altitude_m = target
    kinematics.max_climb_speed_mps = 0.80
    kinematics.max_accel_mps2 = 2.0
    kinematics.max_jerk_mps3 = 20.0
    kinematics.climb_deadband_m = 0.05
    kinematics.climb_timeout_sec = 15.0
    kinematics.climb_settle_window_sec = 0.10
    kinematics.climb_settle_min_samples = 3
    kinematics.climb_settle_max_speed_mps = 0.30
    kinematics.climb_settle_max_position_sigma_m = 0.10
    kinematics.control_loop_hz = 100.0
    params.no_fly = True
    return params


def airborne_context(monkeypatch, params, supervisor_factory=OdometrySupervisor, **kwargs):
    """Build a context already hovering at the firmware's launch height."""
    # The launch transient is firmware, not the behaviour under test; running it
    # at its real 0.60 m/s would spend 1.7 s of wall clock proving nothing.
    monkeypatch.setattr(simulator_module, "CLIMB_RATE_MPS", 40.0)

    odom = supervisor_factory(params.kinematics, params.timeouts, params.calibration, **kwargs)
    calibration = SpeedCalibration(params.kinematics.normalized_to_mps)
    simulator = KinematicSimulator(odom, calibration)
    drone = BenchtopDroneProxy(StubDrone(), no_fly=True, simulator=simulator)

    simulator.publish_initial_state()
    assert odom.calibrate_ground_reference()

    drone.takeoff(altitude=params.kinematics.target_altitude_m)

    # Integration advances on the real clock, so a tight loop of integrate()
    # calls covers microseconds and leaves the drone on the ground. Drive the
    # transient until it actually levels off, bounded so a regression fails
    # loudly rather than hanging.
    hover = min(params.kinematics.target_altitude_m, simulator_module.FIRMWARE_HOVER_ALTITUDE_M)
    give_up_at = time.monotonic() + 2.0
    while time.monotonic() < give_up_at:
        simulator.integrate()
        if odom.snapshot().relative_altitude >= hover - 1e-3:
            break
        time.sleep(0.002)
    else:  # pragma: no cover - only on a regression in the launch transient
        raise AssertionError("the simulated launch never reached the firmware hover height")

    failsafe = FailsafeSupervisor(drone, odom, params.timeouts)
    return ClimbContext(params, drone, odom, failsafe), simulator


def test_firmware_leaves_the_drone_short_of_a_raised_target(monkeypatch):
    """The premise of the bug: liftoff alone does not reach 1.80 m."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(monkeypatch, params)

    altitude = ctx.odom_supervisor.snapshot().relative_altitude

    assert altitude == pytest.approx(simulator_module.FIRMWARE_HOVER_ALTITUDE_M, abs=0.02)
    assert altitude < params.kinematics.target_altitude_m - params.kinematics.climb_deadband_m


def test_ascent_reaches_the_configured_target_altitude(monkeypatch):
    """Requirement 1: Stage 1 climbs from the firmware height to the target."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(monkeypatch, params)

    status = TakeoffStep()._ascend(ctx)

    altitude = ctx.odom_supervisor.snapshot().relative_altitude
    assert status is StepStatus.SUCCESS
    assert altitude == pytest.approx(1.80, abs=params.kinematics.climb_deadband_m)


def test_ascent_stays_under_the_safety_ceiling(monkeypatch):
    """The profile is sized to stop inside the margin, not to overshoot it."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(monkeypatch, params)
    ceiling = ctx.odom_supervisor.altitude_ceiling

    TakeoffStep()._ascend(ctx)

    assert ctx.odom_supervisor.snapshot().relative_altitude <= ceiling


def test_ascent_revokes_climb_authority_on_completion(monkeypatch):
    """Requirement 2: the exception closes behind itself before Stage 2."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(monkeypatch, params)

    TakeoffStep()._ascend(ctx)

    assert ctx.failsafe.climb_authorized is False
    safe_vz, _ = ctx.failsafe.clamp_kinematics(0.20, 0.0, allow_climb=True)
    assert safe_vz == 0.0


def test_ascent_leaves_the_vertical_command_zeroed(monkeypatch):
    """The Bebop latches its last Twist forever; a climb must not be left held.

    Asserted behaviourally: once the ascent returns, letting the simulation run
    on must not carry the drone any higher.
    """
    params = climb_params(target=1.80)
    ctx, simulator = airborne_context(monkeypatch, params)

    TakeoffStep()._ascend(ctx)
    settled = ctx.odom_supervisor.snapshot().relative_altitude

    for _ in range(30):
        time.sleep(0.002)
        simulator.integrate()

    assert ctx.odom_supervisor.snapshot().relative_altitude == pytest.approx(settled, abs=0.01)


def test_no_ascent_is_commanded_when_already_at_target(monkeypatch):
    """The default 1.00 m mission must behave exactly as it always did."""
    params = climb_params(target=1.00)
    ctx, simulator = airborne_context(monkeypatch, params)
    before = ctx.odom_supervisor.snapshot().relative_altitude

    status = TakeoffStep()._ascend(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.failsafe.climb_authorized is False
    assert ctx.odom_supervisor.snapshot().relative_altitude == pytest.approx(before, abs=0.05)


def test_ceiling_breach_during_ascent_triggers_emergency_landing(monkeypatch):
    """Requirement 3: an overshoot aborts the climb and lands the drone."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(
        monkeypatch, params, supervisor_factory=OvershootingSupervisor, breach_after=3
    )

    status = TakeoffStep()._ascend(ctx)

    assert status is StepStatus.FAILURE
    assert ctx.failsafe.failsafe_active is True
    assert ctx.failsafe.climb_authorized is False


def test_emergency_event_aborts_the_ascent(monkeypatch):
    """A stop request mid-climb aborts rather than finishing the manoeuvre."""
    params = climb_params(target=1.80)
    ctx, _ = airborne_context(monkeypatch, params)
    ctx.emergency_event.set()

    status = TakeoffStep()._ascend(ctx)

    assert status is StepStatus.ABORTED
    assert ctx.failsafe.climb_authorized is False


def test_ascent_never_commands_more_than_the_authorized_climb_rate(monkeypatch):
    """The jerk-limited profile stays inside the window's ceiling throughout."""
    params = climb_params(target=1.80)
    ctx, simulator = airborne_context(monkeypatch, params)

    commanded = []
    original = ctx.drone.move_velocity

    def record(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        commanded.append(vz)
        original(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    monkeypatch.setattr(ctx.drone, "move_velocity", record)
    TakeoffStep()._ascend(ctx)

    assert commanded, "the ascent issued no vertical command at all"
    assert max(commanded) <= params.kinematics.max_climb_speed_mps + 1e-6
    assert min(commanded) >= 0.0, "the ascent must never command descent"
    assert commanded[-1] == 0.0, "the held command must be released on completion"


# ---------------------------------------------------------------- stabilization
#
# The post-takeoff hover used to be a blind ``takeoff_stabilize_duration_sec``
# wait. It is now a convergence gate with that duration as its ceiling: it must
# leave early once the liftoff transient has decayed, and never stay longer
# than the ceiling however unsettled the airframe is.


def stabilize_params(ceiling=4.0):
    params = climb_params()
    kinematics = params.kinematics
    kinematics.takeoff_stabilize_duration_sec = ceiling
    kinematics.takeoff_settle_window_sec = 0.30
    kinematics.takeoff_settle_min_samples = 4
    return params


class TelemetryTimer:
    """Drives the simulator the way the mission's telemetry timer does.

    ``_stabilize`` commands nothing, so without this the simulated odometry
    would never produce a new sample and the gate would -- correctly -- refuse
    to call the hover settled.
    """

    def __init__(self, simulator, period_sec=0.01):
        self._simulator = simulator
        self._period = period_sec
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._halt.wait(self._period):
            self._simulator.integrate()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._halt.set()
        self._thread.join(timeout=1.0)


class ScriptedOdometry:
    """Odometry whose every snapshot is produced by a function of the call count."""

    def __init__(self, sample):
        self._sample = sample
        self._calls = 0
        self.altitude_ceiling = 3.0

    def snapshot(self):
        self._calls += 1
        altitude, vz, fresh = self._sample(self._calls)
        return types.SimpleNamespace(
            relative_altitude=altitude,
            vz=vz,
            sample_count=self._calls if fresh else 1,
            timestamp=time.monotonic(),
        )


def scripted_context(params, sample):
    return ClimbContext(params, StubDrone(), ScriptedOdometry(sample), failsafe=None)


def timed_stabilize(ctx):
    started = time.monotonic()
    status = TakeoffStep()._stabilize(ctx)
    return status, time.monotonic() - started


def test_stabilization_ends_as_soon_as_the_hover_settles(monkeypatch):
    """The point of the change: a settled airframe does not wait out the ceiling."""
    params = stabilize_params(ceiling=4.0)
    ctx, simulator = airborne_context(monkeypatch, params)

    with TelemetryTimer(simulator):
        status, elapsed = timed_stabilize(ctx)

    assert status is StepStatus.SUCCESS
    assert elapsed < 1.5, f"a settled hover still waited {elapsed:.2f} s of a 4.0 s ceiling"
    assert elapsed >= params.kinematics.takeoff_settle_window_sec, (
        "settlement was declared before one full observation window had elapsed"
    )


def test_stabilization_never_outlasts_its_ceiling():
    """An airframe that keeps moving is handed on at the ceiling, not held."""
    params = stabilize_params(ceiling=0.5)
    ctx = scripted_context(params, lambda n: (1.0 + 0.2 * (n % 2), 0.4, True))

    status, elapsed = timed_stabilize(ctx)

    assert status is StepStatus.SUCCESS
    assert 0.5 <= elapsed < 0.8, f"stabilization took {elapsed:.2f} s against a 0.5 s ceiling"


def test_an_aircraft_still_on_the_ground_is_not_settled():
    """Motors spooling when ``takeoff`` returns: zero vz, zero spread, zero height."""
    params = stabilize_params(ceiling=0.5)
    ctx = scripted_context(params, lambda n: (0.0, 0.0, True))

    _status, elapsed = timed_stabilize(ctx)

    assert elapsed >= 0.5, "a grounded airframe was declared settled"


def test_a_stalled_odometry_stream_is_not_settled():
    """Frozen telemetry has zero variance by construction; it proves nothing."""
    params = stabilize_params(ceiling=0.5)
    ctx = scripted_context(params, lambda n: (1.0, 0.0, False))

    _status, elapsed = timed_stabilize(ctx)

    assert elapsed >= 0.5, "repeats of one odometry sample were accepted as a settled hover"


def test_a_liftoff_transient_holds_the_gate_until_it_decays():
    """Climbing samples first, then a steady hover: exit only after the latter."""
    params = stabilize_params(ceiling=4.0)
    params.kinematics.control_loop_hz = 50.0

    def sample(n):
        if n <= 25:
            return 0.35 + 0.6 * n / 50.0, 0.6, True
        return 0.65, 0.0, True

    ctx = scripted_context(params, sample)
    status, elapsed = timed_stabilize(ctx)

    assert status is StepStatus.SUCCESS
    transient_sec = 25 / 50.0
    assert elapsed >= transient_sec + params.kinematics.takeoff_settle_window_sec - 0.05
    assert elapsed < 4.0


def test_an_emergency_aborts_the_stabilization():
    params = stabilize_params(ceiling=4.0)
    ctx = scripted_context(params, lambda n: (1.0, 0.4, True))
    ctx.emergency_event.set()

    assert TakeoffStep()._stabilize(ctx) is StepStatus.ABORTED


def test_the_flat_trim_settle_is_untouched():
    """The IMU calibration wait is not a transient gate and must stay fixed."""
    from mvp_mission_bebop.steps import takeoff as takeoff_module

    assert takeoff_module._FLAT_TRIM_SETTLE_SEC == 2.0


# ------------------------------------------------ takeoff call on confirmation


@pytest.fixture
def milestones(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "mvp_mission_bebop.steps.takeoff.emit_milestone",
        lambda key, payload=None: emitted.append((key, dict(payload or {}))),
    )
    return emitted


@pytest.mark.parametrize(
    "flying_state, altitude, expected",
    [
        (None, 0.0, False),
        (0, 0.0, False),
        (6, 0.0, False),
        (1, 0.0, True),
        (2, 0.0, True),
        (None, 0.29, False),
        (None, 0.31, True),
        (0, 0.31, True),
    ],
)
def test_takeoff_is_confirmed_by_flying_state_or_odometry(flying_state, altitude, expected):
    from mvp_mission_bebop.steps.takeoff import takeoff_confirmed

    assert takeoff_confirmed(flying_state, altitude, 0.30) is expected


def test_the_takeoff_command_alone_says_nothing(milestones):
    """Regression: ``BebopDrone.takeoff`` returns True after sleep(3) always."""
    params = stabilize_params()
    ctx = scripted_context(params, lambda n: (0.0, 0.0, True))
    ctx.drone.takeoff = lambda altitude: True
    assert TakeoffStep()._launch(ctx) is StepStatus.SUCCESS
    assert milestones == []


def test_takeoff_is_called_when_odometry_leaves_the_ground_with_the_configured_altitude(milestones):
    params = stabilize_params(ceiling=0.6)
    params.kinematics.target_altitude_m = 2.3
    ctx = scripted_context(params, lambda n: (0.0 if n < 5 else 0.6, 0.0, True))
    step = TakeoffStep()
    step._stabilize(ctx)
    assert milestones == [("mission.takeoff", {"altitude_m": 2.3})]


def test_takeoff_is_never_called_for_an_airframe_that_stayed_down(milestones):
    params = stabilize_params(ceiling=0.3)
    ctx = scripted_context(params, lambda n: (0.0, 0.0, True))
    TakeoffStep()._stabilize(ctx)
    assert milestones == []


def test_a_reported_flying_state_confirms_before_the_odometry_does(milestones):
    params = stabilize_params(ceiling=0.3)
    ctx = scripted_context(params, lambda n: (0.0, 0.0, True))
    ctx.flying_state = 1
    TakeoffStep()._stabilize(ctx)
    assert [key for key, _ in milestones] == ["mission.takeoff"]


# ------------------------------------------------ countdown driven by the mission


def countdown_ctx(duration):
    params = stabilize_params()
    params.kinematics.countdown_sec = duration
    ctx = scripted_context(params, lambda n: (0.0, 0.0, True))
    ctx.failsafe = types.SimpleNamespace(notify_frame_received=lambda: None)
    ctx.perception = RecordingPerception()
    return ctx


def test_the_countdown_is_reported_every_whole_second_down_to_zero(milestones):
    """Regression: the station timed it from the spawn, ~14 s ahead of the real one."""
    TakeoffStep()._countdown(countdown_ctx(4.0))
    ticks = [payload["remaining_sec"] for key, payload in milestones if key == "mission.countdown"]
    assert ticks == [4, 3, 2, 1, 0]


def test_the_countdown_clearance_call_lands_at_three_point_two_seconds(milestones):
    TakeoffStep()._countdown(countdown_ctx(4.0))
    sequence = [
        key if key != "mission.countdown" else f"t{payload['remaining_sec']}" for key, payload in milestones
    ]
    assert sequence == ["t4", "mission.countdown_3", "t3", "t2", "t1", "t0"]


def test_a_zero_countdown_still_clears_and_reports_zero(milestones):
    TakeoffStep()._countdown(countdown_ctx(0.0))
    assert [key for key, _ in milestones] == ["mission.countdown_3", "mission.countdown"]
    assert milestones[-1][1] == {"remaining_sec": 0}


# ------------------------------------------------ countdown through the worker (4.5e / 4.4)


class RecordingPerception:
    """Stand-in pipeline: records engagement and status, serves one sample."""

    def __init__(self):
        self.engaged_with = []
        self.statuses = []
        self.disengaged = 0

    def session(self, conf=None, imgsz=None):
        import contextlib

        @contextlib.contextmanager
        def scope():
            self.engaged_with.append(imgsz)
            try:
                yield self
            finally:
                self.disengaged += 1

        return scope()

    def set_status(self, text):
        self.statuses.append(text)

    def get_latest(self, max_age_sec):
        return types.SimpleNamespace(frame=object(), result=None)


def test_the_countdown_warms_yolo_on_the_worker_not_on_the_control_thread(milestones):
    ctx = countdown_ctx(2.0)
    ctx.perception = RecordingPerception()
    ctx.params.vision.inference_imgsz = 480
    calls = []
    ctx.detector = types.SimpleNamespace(detect=lambda *a, **k: calls.append(1))
    ctx.publish_annotated_stream = lambda *a, **k: calls.append("annotated")

    assert TakeoffStep()._countdown(ctx) is StepStatus.SUCCESS
    assert calls == []
    assert ctx.perception.engaged_with == [480]
    assert ctx.perception.disengaged == 1
    assert any("CONTAGEM REGRESSIVA" in text for text in ctx.perception.statuses)
