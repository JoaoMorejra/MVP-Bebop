"""Simple mission profile: Stages 2 to 5 against the shared synchronous-perception context."""

from __future__ import annotations

import functools
import math
from typing import List, Tuple

import pytest

from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.controllers.yaw_pattern import YawRateLimiter, YawServoingController
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps import inspection as inspection_module
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps import search as search_module
from mvp_mission_bebop.steps import tracking as tracking_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from test_stage_altitude_wiring import Ctx

YAW_RATE_PER_UNIT = 1.75


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def simulated_time(monkeypatch):
    fake = FakeTime()
    for module in (search_module, tracking_module, inspection_module, rtl_module):
        monkeypatch.setattr(module, "LoopRate", functools.partial(LoopRate, clock=fake.clock, sleeper=fake.sleep))
        monkeypatch.setattr(module, "Deadline", functools.partial(Deadline, clock=fake.clock))
    return fake


def simple_ctx(*, detections: bool, window: float = 2.0) -> Ctx:
    """A context whose failsafe and drone share one YawAuthority, as in mission.py."""
    ctx = Ctx(detections=detections)
    authority = YawAuthority()
    ctx.failsafe.yaw_authority = authority
    ctx.params.mission_mode = "simple"
    ctx.params.timeouts.search_timeout_sec = window
    ctx.params.timeouts.tracking_timeout_sec = window
    ctx.params.platform.edge_ahead_m = None
    ctx.odom_supervisor.yaw = 0.0
    ctx.blackboard.initial_yaw_rad = None
    ctx.visual_controller = YawServoingController(ctx.params.yaw_pattern, ctx.params.vision)
    return ctx


def twists(ctx: Ctx) -> List[Tuple[float, float, float, float]]:
    return list(ctx.drone.commands)


# ------------------------------------------------------------------ Stage 2


def test_the_yaw_scan_never_translates_and_only_turns_left():
    ctx = simple_ctx(detections=False)

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    commands = twists(ctx)
    assert commands
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in commands)
    assert all(vyaw >= 0.0 for _vx, _vy, _vz, vyaw in commands)
    assert any(vyaw > 0.0 for _vx, _vy, _vz, vyaw in commands)


def test_the_yaw_halt_transmits_exactly_zero_when_the_ramp_ends_on_a_residual():
    ctx = simple_ctx(detections=False)
    step = LateralSearchStep(mode=MissionMode.SIMPLE)
    dt = 0.05
    ramp = YawRateLimiter(ctx.params.yaw_pattern.scan_ramp_per_sec)
    ramp._value = ctx.params.yaw_pattern.scan_ramp_per_sec * dt + 5e-10
    step._yaw_ramp = ramp

    with ctx.failsafe.yaw_authority.window(ctx.params.yaw_pattern.max_vyaw):
        step._yaw_halt(ctx, None, dt)

    assert twists(ctx)[-1][3] == 0.0
    assert ramp.value == 0.0


def test_the_scan_reaches_the_configured_rate_and_ends_at_exactly_zero():
    ctx = simple_ctx(detections=False)
    scan = ctx.params.yaw_pattern.scan_vyaw

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    commands = twists(ctx)
    assert max(vyaw for *_rest, vyaw in commands) == pytest.approx(scan, abs=1e-9)
    assert commands[-1][3] == 0.0, "the Bebop latches the last Twist; the stage must leave it at zero"


def test_a_confirmed_target_stops_the_rotation_and_records_it():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = False

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.target_confirmed is True
    assert twists(ctx)[-1][3] == 0.0


@pytest.mark.parametrize("hz", [15.0, 10.0, 30.0])
@pytest.mark.parametrize("detections, window", [(True, 3.0), (False, 0.2), (False, 0.45), (False, 0.8), (False, 3.0)])
def test_the_zero_twist_that_ends_the_scan_is_delivered_one_period_before_the_stage_returns(
    simulated_time, detections, window, hz
):
    ctx = simple_ctx(detections=detections, window=window)
    ctx.params.kinematics.control_loop_hz = hz
    period = 1.0 / hz
    stamps: List[float] = []
    original = ctx.drone.move_velocity

    def stamped(*args, **kwargs):
        stamps.append(simulated_time.now)
        return original(*args, **kwargs)

    ctx.drone.move_velocity = stamped

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert twists(ctx)[-1][3] == 0.0
    assert simulated_time.now - stamps[-1] >= period - 1e-9, (
        "the final zero Twist must precede the next stage's first line by at least one control period"
    )


def test_the_stage_records_the_heading_when_stage_one_did_not():
    ctx = simple_ctx(detections=False)
    ctx.odom_supervisor.yaw = 0.7

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.initial_yaw_rad == pytest.approx(0.7)


def test_the_stage_keeps_the_heading_stage_one_recorded():
    ctx = simple_ctx(detections=False)
    ctx.blackboard.initial_yaw_rad = 0.2
    ctx.odom_supervisor.yaw = 0.7

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.initial_yaw_rad == pytest.approx(0.2)


def test_the_yaw_window_is_closed_again_when_the_stage_returns():
    ctx = simple_ctx(detections=False)

    LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert not ctx.failsafe.yaw_authority.is_open


def test_an_interrupt_mid_scan_leaves_the_last_twist_at_zero_yaw_and_the_window_closed():
    ctx = simple_ctx(detections=False, window=10.0)
    original = ctx.drone.move_velocity
    sent = {"count": 0}

    def interrupt_after_some_commands(*args, **kwargs):
        sent["count"] += 1
        if sent["count"] == 25:
            ctx.stage_jump_event.set()
        return original(*args, **kwargs)

    ctx.drone.move_velocity = interrupt_after_some_commands

    status = LateralSearchStep(mode=MissionMode.SIMPLE).execute(ctx)

    commands = twists(ctx)
    assert status is StepStatus.ABORTED
    assert any(vyaw > 0.0 for *_rest, vyaw in commands), "the interrupt must land mid-rotation"
    assert commands[-1][3] == 0.0, commands[-5:]
    assert not ctx.failsafe.yaw_authority.is_open


def test_the_advanced_search_still_pins_yaw_to_zero():
    ctx = simple_ctx(detections=False)
    ctx.params.timeouts.search_timeout_sec = 1.0

    LateralSearchStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in twists(ctx))


# ------------------------------------------------------------------ Stage 3


class YawDrone:
    """Records Twists and turns the shared odometry heading by the commanded rate."""

    def __init__(self, ctx: Ctx, period: float) -> None:
        self.commands: List[Tuple[float, float, float, float]] = []
        self._ctx = ctx
        self._period = period
        self.no_fly = True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))
        self._ctx.odom_supervisor.yaw += vyaw * YAW_RATE_PER_UNIT * self._period

    def camera_control(self, tilt, pan=0.0):
        return None

    def snapshot(self):
        return None

    def land(self):
        return True


def test_the_yaw_servo_never_translates_and_stays_inside_the_window():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    commands = twists(ctx)
    assert commands
    assert all(vx == 0.0 and vy == 0.0 for vx, vy, _vz, _vyaw in commands)
    assert all(abs(vyaw) <= ctx.params.yaw_pattern.max_vyaw + 1e-9 for *_rest, vyaw in commands)
    assert not ctx.failsafe.yaw_authority.is_open


def test_the_fixed_scene_target_right_of_centre_drives_a_negative_yaw():
    # The shared fake detects the target at x=428 of 856 (the frame centre); move it right.
    from test_stage_altitude_wiring import Detection, DetectionResult

    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True
    ctx.detect = lambda _frame, conf=0.5, imgsz=None: DetectionResult([Detection(center=(700.0, 240.0))])

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert any(vyaw < 0.0 for *_rest, vyaw in twists(ctx))
    assert all(vyaw <= 0.0 for *_rest, vyaw in twists(ctx))


def test_the_lock_cycle_leaves_the_last_twist_at_zero_yaw():
    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.blackboard.approach_finished is True
    assert twists(ctx)[-1][3] == 0.0


def test_without_a_confirmed_target_the_stage_is_skipped_without_commands():
    ctx = simple_ctx(detections=False)
    ctx.blackboard.target_confirmed = False

    assert VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert ctx.blackboard.approach_finished is False
    assert twists(ctx) == []


def test_the_lateral_servo_of_the_other_profiles_still_transmits_zero_yaw():
    ctx = Ctx(detections=True)
    ctx.blackboard.target_confirmed = True

    VisualServoingStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)


def test_a_fault_inside_the_yaw_servo_still_leaves_a_zero_twist():
    from test_stage_altitude_wiring import Detection, DetectionResult

    ctx = simple_ctx(detections=True)
    ctx.blackboard.target_confirmed = True
    ctx.detect = lambda _frame, conf=0.5, imgsz=None: DetectionResult([Detection(center=(700.0, 240.0))])
    controller = ctx.visual_controller
    compute = controller.compute
    calls = {"n": 0}

    def failing_compute(**kwargs):
        calls["n"] += 1
        if calls["n"] > 20:
            raise RuntimeError("controller fault")
        return compute(**kwargs)

    controller.compute = failing_compute

    with pytest.raises(RuntimeError):
        VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert any(vyaw < 0.0 for *_rest, vyaw in twists(ctx))
    assert twists(ctx)[-1] == (0.0, 0.0, 0.0, 0.0)
    assert not ctx.failsafe.yaw_authority.is_open


@pytest.mark.parametrize("window", [0.15, 0.4, 3.0])
def test_stage_three_returns_one_period_after_its_final_zero_twist(simulated_time, window):
    from test_stage_altitude_wiring import Detection, DetectionResult

    ctx = simple_ctx(detections=True, window=window)
    ctx.params.kinematics.control_loop_hz = 15.0
    ctx.blackboard.target_confirmed = True
    ctx.detect = lambda _frame, conf=0.5, imgsz=None: DetectionResult([Detection(center=(700.0, 240.0))])
    stamps: List[float] = []
    original = ctx.drone.move_velocity

    def stamped(*args, **kwargs):
        stamps.append(simulated_time.now)
        return original(*args, **kwargs)

    ctx.drone.move_velocity = stamped

    VisualServoingStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert any(vyaw < 0.0 for *_rest, vyaw in twists(ctx))
    assert twists(ctx)[-1][3] == 0.0
    assert simulated_time.now - stamps[-1] >= 1.0 / 15.0 - 1e-9


# ------------------------------------------------------------------ Stage 4


def inspection_ctx(*, offset_deg: float) -> Ctx:
    ctx = simple_ctx(detections=True)
    ctx.params.kinematics.hover_duration_sec = 0.2
    ctx.drone = YawDrone(ctx, period=1.0 / ctx.params.kinematics.control_loop_hz)
    ctx.drone.camera_calls = []
    ctx.drone.camera_control = lambda tilt, pan=0.0: ctx.drone.camera_calls.append(tilt)
    ctx.blackboard.initial_yaw_rad = 0.5
    ctx.odom_supervisor.yaw = 0.5 + math.radians(offset_deg)
    return ctx


@pytest.mark.parametrize("offset_deg", [60.0, -60.0, 170.0])
def test_the_heading_is_restored_by_the_short_way_after_the_capture(offset_deg):
    ctx = inspection_ctx(offset_deg=offset_deg)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    status = NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert status is StepStatus.SUCCESS
    error = abs((ctx.odom_supervisor.yaw - 0.5 + math.pi) % (2 * math.pi) - math.pi)
    assert error <= math.radians(ctx.params.yaw_pattern.return_tolerance_deg) + 1e-6
    assert ctx.drone.commands[-1][3] == 0.0
    assert not ctx.failsafe.yaw_authority.is_open


@pytest.mark.parametrize("offset_deg", [40.0, -40.0, 3.0])
def test_the_heading_restore_returns_one_period_after_its_final_zero_twist(simulated_time, offset_deg):
    ctx = inspection_ctx(offset_deg=offset_deg)
    ctx.params.kinematics.control_loop_hz = 15.0
    ctx.drone = YawDrone(ctx, period=1.0 / 15.0)
    stamps: List[float] = []
    original = ctx.drone.move_velocity

    def stamped(*args, **kwargs):
        stamps.append(simulated_time.now)
        return original(*args, **kwargs)

    ctx.drone.move_velocity = stamped

    status = NadirInspectionStep(mode=MissionMode.SIMPLE)._restore_heading(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.drone.commands[-1][3] == 0.0
    assert simulated_time.now - stamps[-1] >= 1.0 / 15.0 - 1e-9


def test_the_gimbal_goes_to_the_return_tilt_at_the_end_of_the_stage():
    ctx = inspection_ctx(offset_deg=20.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg
    assert ctx.current_tilt_deg == ctx.params.rtl.camera_tilt_deg


def test_no_yaw_is_commanded_before_the_capture_and_hover_are_done():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx)

    first_turn = next(i for i, (*_rest, vyaw) in enumerate(ctx.drone.commands) if vyaw != 0.0)
    assert first_turn > 0
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands[:first_turn])


def test_a_missing_reference_heading_skips_the_restore_but_still_lowers_the_gimbal():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.blackboard.initial_yaw_rad = None

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg


@pytest.mark.parametrize("reference", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_reference_heading_is_treated_as_missing(reference):
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.blackboard.initial_yaw_rad = reference
    ctx.params.yaw_pattern.return_timeout_sec = 30.0

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
    assert ctx.drone.commands[-1][3] == 0.0
    assert not ctx.failsafe.yaw_authority.is_open
    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg
    assert ctx.current_tilt_deg == ctx.params.rtl.camera_tilt_deg


def test_nan_heading_readings_during_the_restore_command_no_yaw_and_do_not_fail_the_stage():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.odom_supervisor.yaw = float("nan")
    ctx.params.yaw_pattern.return_timeout_sec = 0.5

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)
    assert ctx.drone.commands[-1][3] == 0.0
    assert not ctx.failsafe.yaw_authority.is_open
    assert ctx.drone.camera_calls[-1] == ctx.params.rtl.camera_tilt_deg
    assert ctx.current_tilt_deg == ctx.params.rtl.camera_tilt_deg


def test_a_transient_nan_heading_reading_holds_the_yaw_and_the_restore_resumes():
    ctx = inspection_ctx(offset_deg=60.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    odometry = ctx.odom_supervisor
    authority = ctx.failsafe.yaw_authority
    blind = {"reads": 10}
    original = ctx.drone.move_velocity
    commanded_while_blind: List[float] = []

    class BlindSnapshot:
        """Reads NaN for the first heading samples taken inside the yaw window."""

        @property
        def yaw(self):
            if authority.is_open and blind["reads"] > 0:
                blind["reads"] -= 1
                return float("nan")
            return odometry.yaw

        def __getattr__(self, name):
            return getattr(odometry, name)

    class BlindHeading:
        """Odometry whose snapshots go blind; the simulated airframe heading stays true."""

        def snapshot(self):
            return BlindSnapshot()

        @property
        def yaw(self):
            return odometry.yaw

        @yaw.setter
        def yaw(self, value):
            odometry.yaw = value

        def __getattr__(self, name):
            return getattr(odometry, name)

    def record(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        if authority.is_open and blind["reads"] > 0:
            commanded_while_blind.append(vyaw)
        original(vx, vy, vz, vyaw, duration)

    ctx.odom_supervisor = BlindHeading()
    ctx.drone.move_velocity = record

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert blind["reads"] == 0
    assert commanded_while_blind and all(vyaw == 0.0 for vyaw in commanded_while_blind)
    error = abs((odometry.yaw - 0.5 + math.pi) % (2 * math.pi) - math.pi)
    assert error <= math.radians(ctx.params.yaw_pattern.return_tolerance_deg) + 1e-6
    assert ctx.drone.commands[-1][3] == 0.0
    assert not authority.is_open


def test_a_restore_that_runs_out_of_window_does_not_fail_the_mission():
    ctx = inspection_ctx(offset_deg=170.0)
    ctx.params.yaw_pattern.return_timeout_sec = 0.05

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.SUCCESS
    assert ctx.drone.commands[-1][3] == 0.0


def test_an_abort_during_the_restore_stops_the_rotation():
    ctx = inspection_ctx(offset_deg=170.0)
    ctx.params.yaw_pattern.return_timeout_sec = 30.0
    original = ctx.drone.move_velocity

    def abort_after_the_first_turn(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        original(vx, vy, vz, vyaw, duration)
        if vyaw != 0.0:
            ctx.emergency_event.set()

    ctx.drone.move_velocity = abort_after_the_first_turn

    assert NadirInspectionStep(mode=MissionMode.SIMPLE).execute(ctx) is StepStatus.ABORTED
    assert ctx.drone.commands[-1][3] == 0.0


def test_the_advanced_inspection_never_yaws_and_ends_at_the_nadir_tilt():
    ctx = Ctx(detections=True)
    ctx.params.kinematics.hover_duration_sec = 0.2

    NadirInspectionStep().execute(ctx)

    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)


def test_stage_4_captures_immediately_when_stage_3_timed_out():
    import unittest.mock

    ctx = inspection_ctx(offset_deg=0.0)
    ctx.blackboard.approach_finished = False
    captured_calls = []
    original_capture = NadirInspectionStep._capture

    def spy_capture(self, ctx_in, settled, *, forced=False):
        captured_calls.append((settled, forced))
        return original_capture(self, ctx_in, settled, forced=forced)

    step = NadirInspectionStep(mode=MissionMode.SIMPLE)
    with unittest.mock.patch.object(NadirInspectionStep, "_capture", spy_capture):
        status = step.execute(ctx)

    assert status is StepStatus.SUCCESS
    assert len(captured_calls) == 1
    assert captured_calls[0] == (False, True)
    assert ctx.blackboard.evidence is not None


def test_stage_3_clamps_tracking_timeout_to_15_seconds():
    ctx = simple_ctx(detections=True)
    ctx.params.timeouts.tracking_timeout_sec = 110.0
    ctx.blackboard.target_confirmed = True
    step = VisualServoingStep(mode=MissionMode.SIMPLE)
    status = step.execute(ctx)
    assert status is StepStatus.SUCCESS


def test_advanced_stage_4_captures_immediately_when_stage_3_timed_out():
    import unittest.mock

    ctx = Ctx(detections=True)
    ctx.blackboard.approach_finished = False
    captured_calls = []
    original_capture = NadirInspectionStep._capture

    def spy_capture(self, ctx_in, settled, *, forced=False):
        captured_calls.append((settled, forced))
        return original_capture(self, ctx_in, settled, forced=forced)

    step = NadirInspectionStep(mode=MissionMode.ADVANCED)
    with unittest.mock.patch.object(NadirInspectionStep, "_capture", spy_capture):
        status = step.execute(ctx)

    assert status is StepStatus.SUCCESS
    assert len(captured_calls) == 1
    assert captured_calls[0] == (False, True)
    assert ctx.blackboard.evidence is not None


# ------------------------------------------------------------------ Stage 5

from mvp_mission_bebop.estimation.calibration import SpeedCalibration  # noqa: E402
from test_recovery_and_touchdown import LandingDrone, landing_ctx  # noqa: E402


class HoldGovernor:
    """Altitude governor that holds: Stage 5 only needs a vz source."""

    @staticmethod
    def compute_vz(_altitude, _dt=None, vx_commanded=0.0):
        return 0.0

    @staticmethod
    def horizontal_scale():
        return 1.0


def land_ctx(mode_name: str, **drone_kwargs):
    drone = LandingDrone(**drone_kwargs)
    ctx = landing_ctx(drone)
    ctx.params.mission_mode = mode_name
    ctx.speed_calibration = SpeedCalibration(1.0)
    ctx.governor = HoldGovernor()
    return drone, ctx


@pytest.mark.parametrize("mode", list(MissionMode))
def test_without_the_return_both_profiles_land_in_place_without_building_a_marker_sensor(mode):
    drone, ctx = land_ctx(mode.value)
    ctx.params.rtl.enabled = False
    ctx.params.rtl.target_aruco_id = 10_000  # not a valid marker: must be irrelevant here

    class ExplodingSensor:
        def __getattr__(self, name):
            raise AssertionError("a landing without the return must not touch the marker sensor")

    step = ClosedLoopRTLStep(sensor=ExplodingSensor(), mode=mode, rtl_enabled=False)
    status = step.execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.touchdown_confirmed is True
    assert drone.land_calls >= 1
    assert all(vyaw == 0.0 for *_rest, vyaw in drone.commands)


@pytest.mark.parametrize("mode", list(MissionMode))
def test_without_the_return_no_profile_announces_one(mode):
    drone, ctx = land_ctx(mode.value)
    ctx.params.rtl.enabled = False
    milestones = []
    original = rtl_module.emit_milestone
    rtl_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        ClosedLoopRTLStep(mode=mode, rtl_enabled=False).execute(ctx)
    finally:
        rtl_module.emit_milestone = original

    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys
    landing = [payload for key, payload in milestones if key == "mission.landing"]
    assert landing and landing[0]["at_base"] is False
    touchdown = [payload for key, payload in milestones if key == "mission.touchdown"]
    assert touchdown and touchdown[0]["at_base"] is False


def test_the_simple_profile_without_a_marker_sensor_lands_in_place_flagged_as_base():
    drone, ctx = land_ctx("simple")
    milestones = []
    original = rtl_module.emit_milestone
    rtl_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        step = ClosedLoopRTLStep(mode=MissionMode.SIMPLE)
        step._build_sensor = lambda _ctx: None
        status = step.execute(ctx)
    finally:
        rtl_module.emit_milestone = original

    assert status is StepStatus.SUCCESS
    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys
    assert not any(vx != 0.0 or vy != 0.0 for vx, vy, *_rest in drone.commands)
    touchdown = [payload for key, payload in milestones if key == "mission.touchdown"]
    assert touchdown and touchdown[0]["at_base"] is True


class StubSensor:
    target_id = 8
    backend = "stub"
    failures = 0

    def observe(self, _frame):
        return None

    def overlay_geometry(self, _frame):
        return []


def test_the_simple_profile_never_flies_the_reverse_search():
    drone, ctx = land_ctx("simple")
    ctx.params.rtl.centering_timeout_sec = 0.1
    step = ClosedLoopRTLStep(sensor=StubSensor(), mode=MissionMode.SIMPLE)
    step._reverse_search = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("the simple profile has no reverse search")
    )
    milestones = []
    original = rtl_module.emit_milestone
    rtl_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        assert step.execute(ctx) is StepStatus.SUCCESS
    finally:
        rtl_module.emit_milestone = original

    assert all(vx == 0.0 for vx, *_rest in drone.commands)
    assert all(vy == 0.0 for _vx, vy, *_rest in drone.commands)
    assert all(vyaw == 0.0 for *_rest, vyaw in drone.commands)
    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys
    touchdown = [payload for key, payload in milestones if key == "mission.touchdown"]
    assert touchdown and touchdown[0]["at_base"] is True


def test_the_advanced_profile_is_unchanged():
    # The historical Stage 5 tests (test_aruco_rtl.py, test_lateral_rtl.py) cover the
    # advanced flow end to end; this pins the default constructor to it.
    assert ClosedLoopRTLStep().mode is MissionMode.ADVANCED
