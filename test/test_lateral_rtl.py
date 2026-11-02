"""Stage 5 returns to the right: vy is the return channel, vx is pinned or trimmed.

The outbound search flies to the LEFT (+y, REP-103), so the return leg is the
mirror image along the body Y axis. Four properties are pinned here:

*The marker search cruise* transmits ``vx`` as the literal ``0.0`` and ``vy``
saturated non-positive at the actuator boundary, at the outbound speed in
magnitude (rule E05).

*The pad edge guard* receives the along-track position the marker implies on the
lateral axis: the marker sits on the takeoff point, so ``along = -ey_body_m``.

*The re-framing creep* of the centering law goes LEFT, which is the side of the
frame an overshot marker leaves through when the airframe flies to the right.

*The odometric return* flies the cruise on Y and only trims X.
"""

from __future__ import annotations

import functools
import logging
import math
from typing import List, Optional

import pytest

from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.rtl_guidance import (
    ReturnReference,
    RTLGuidanceController,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker
from mvp_mission_bebop.parameters import (
    DeadReckoningConfig,
    FlightKinematicsConfig,
    ReturnToLaunchConfig,
)
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep, _PhaseOutcome
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot
from test_aruco_rtl import Ctx, OverrideOdometry, ScriptedSensor, law, sighting

DT = 1.0 / 15.0
SEARCH_WINDOW_SEC = 3.0



@pytest.fixture(autouse=True)
def _lens_frame_centering(monkeypatch):
    """Pin the centering law in the lens frame (camera-to-CoG offset zero).

    These tests simulate the marker relative to the camera and exercise the
    law's dynamics, gates and recovery; the camera-to-CoG extrinsic that
    shifts the equilibrium is covered by ``test_camera_extrinsics.py``.
    """
    from mvp_mission_bebop.controllers import rtl_guidance as _rtl_guidance

    monkeypatch.setattr(_rtl_guidance, "_camera_offset", lambda _cfg, _name: 0.0)

class FakeTime:
    """Simulated monotonic clock; sleeping advances it, so loops are load-independent."""

    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture
def simulated_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(
        rtl_module,
        "LoopRate",
        functools.partial(LoopRate, clock=fake.clock, sleeper=fake.sleep),
    )
    monkeypatch.setattr(rtl_module, "Deadline", functools.partial(Deadline, clock=fake.clock))
    return fake


class RecordingGovernor:
    engaged = True
    climbing = False
    altitude_error_m = 0.0

    def __init__(self) -> None:
        self.calls: List[float] = []

    def compute_vz(self, _altitude, _dt=None, vx_commanded=0.0):
        self.calls.append(vx_commanded)
        return 0.0

    @staticmethod
    def horizontal_scale():
        return 1.0


def search(ctx: Ctx, fake: FakeTime, *, mode: MissionMode = MissionMode.ADVANCED):
    sensor = ScriptedSensor(ctx.drone)
    step = ClosedLoopRTLStep(sensor=sensor, mode=mode)
    outcome = step._reverse_search(
        ctx, sensor, Deadline(ctx.params.rtl.timeout_sec, clock=fake.clock)
    )
    return outcome


def search_ctx(*, lateral_gain: float = 1.0) -> Ctx:
    ctx = Ctx()
    ctx.params.rtl.timeout_sec = SEARCH_WINDOW_SEC
    # Rule E05: the station writes the return speed as minus the outbound one.
    ctx.params.rtl.reverse_cruise_velocity = -ctx.params.kinematics.forward_cruise_velocity
    ctx.speed_calibration = SpeedCalibration(normalized_to_mps=1.0, lateral_to_mps=lateral_gain)
    return ctx


# ═════════════════════════════════════════════════════════ the search cruise


@pytest.mark.parametrize("mode", [MissionMode.SIMPLE, MissionMode.ADVANCED])
def test_the_reverse_search_flies_right_with_vx_zero(simulated_time, mode):
    ctx = search_ctx()

    outcome = search(ctx, simulated_time, mode=mode)

    assert ctx.horizontal, "the search sent no commands at all"
    assert all(vx == 0.0 for vx, _vy in ctx.horizontal), "vx must be the literal zero"
    assert all(vy <= 0.0 for _vx, vy in ctx.horizontal), "the return must never fly left"
    assert any(vy < 0.0 for _vx, vy in ctx.horizontal), "it never actually flew"
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)
    assert outcome.value == "exhausted"


def test_reverse_search_marker_acquired_halts_and_achieves(simulated_time):
    """When the marker is confirmed during the search, the drone halts and completes phase."""
    ctx = search_ctx()
    ctx.params.rtl.timeout_sec = 8.0
    sensor = ScriptedSensor(ctx.drone, acquire_after=15, ex=-0.1, ey=-0.2)
    step = ClosedLoopRTLStep(sensor=sensor, mode=MissionMode.ADVANCED)
    outcome = step._reverse_search(
        ctx, sensor, Deadline(ctx.params.rtl.timeout_sec, clock=simulated_time.clock)
    )

    assert outcome is _PhaseOutcome.ACHIEVED
    assert ctx.blackboard.rtl_marker_sighted
    final_vx, final_vy = ctx.horizontal[-1]
    assert final_vx == pytest.approx(0.0)
    assert final_vy == pytest.approx(0.0)


@pytest.mark.parametrize("lateral_gain", [1.0, 0.5])
def test_the_search_speed_equals_the_outbound_speed_in_magnitude(simulated_time, lateral_gain):
    """E05: same normalized magnitude both ways, whatever the lateral calibration."""
    ctx = search_ctx(lateral_gain=lateral_gain)

    search(ctx, simulated_time)

    peak = max(abs(vy) for _vx, vy in ctx.horizontal)
    tolerance = 1.5 * ctx.params.rtl.quantization_step
    assert peak == pytest.approx(abs(ctx.params.rtl.reverse_cruise_velocity), abs=tolerance)
    assert peak == pytest.approx(ctx.params.kinematics.forward_cruise_velocity, abs=tolerance)


def test_the_search_never_commands_leftward_flight(simulated_time):
    """A sign error in configuration cannot turn the return into an outbound cruise."""
    ctx = search_ctx()
    ctx.params.rtl.reverse_cruise_velocity = abs(ctx.params.rtl.reverse_cruise_velocity)

    search(ctx, simulated_time)

    assert all(vy <= 0.0 for _vx, vy in ctx.horizontal)
    assert any(vy < 0.0 for _vx, vy in ctx.horizontal)
    assert all(vx == 0.0 for vx, _vy in ctx.horizontal)


def test_the_governor_is_fed_the_lateral_magnitude():
    ctx = Ctx()
    ctx.governor = RecordingGovernor()
    profile = JerkLimitedProfile(ProfileLimits(max_velocity=0.3, max_accel=0.4, max_jerk=2.0))
    shaper = QuantizedCommandShaper(0.06, 0.01)
    step = ClosedLoopRTLStep(sensor=ScriptedSensor(ctx.drone))

    sent = [
        step._cruise_rightward(ctx, profile, shaper, dt=0.05, cruise_mps=0.2) for _ in range(40)
    ]

    assert all(vy <= 0.0 for vy in sent)
    assert any(vy < 0.0 for vy in sent), "the cruise never left standstill"
    assert len(ctx.governor.calls) == len(sent)
    assert all(fed >= 0.0 for fed in ctx.governor.calls)
    assert ctx.governor.calls == pytest.approx([abs(vy) for vy in sent])
    assert all(vx == 0.0 for vx, _vy in ctx.horizontal)


# ═════════════════════════════════════════════════════ the pad edge guard


def test_the_marker_offset_feeds_the_platform_monitor_along_the_lateral_axis():
    """The outbound track is to the left, so along-track is minus the left error."""
    ctx = Ctx()
    ctx.params.platform.edge_ahead_m = 0.30
    ctx.odom_supervisor = OverrideOdometry(ctx.drone)
    step = ClosedLoopRTLStep()

    step._report_pad_position(ctx, -0.4)
    step._report_pad_position(ctx, 0.2)

    assert ctx.odom_supervisor.overrides == [
        (pytest.approx(0.4), pytest.approx(0.5)),
        (pytest.approx(-0.2), pytest.approx(0.5)),
    ]


def test_the_centering_phase_reports_the_lateral_offset_of_the_marker(simulated_time):
    """The call site hands the monitor the left error, not the forward one."""
    ctx = Ctx(altitude=0.30)
    ctx.params.rtl.timeout_sec = 4.0
    ctx.params.platform.edge_ahead_m = 0.30
    ctx.odom_supervisor = OverrideOdometry(ctx.drone, 0.30, period=1.0 / 200.0)
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.35, ey=-0.25)

    ClosedLoopRTLStep(sensor=sensor).execute(ctx)

    overrides = ctx.odom_supervisor.overrides
    assert overrides, "the marker never reported the pad position"
    assert overrides[0][0] == pytest.approx(0.25, abs=0.01)


# ═════════════════════════════════════════════════════ the re-framing creep


def test_overshooting_the_marker_creeps_left_not_aft():
    """Review Focus 5: flying right, an overshot marker leaves the frame on the left."""
    controller = law()
    moving = None
    for _ in range(40):
        moving = controller.update(sighting(0.0, -0.25), DT)
    assert moving.vy < 0.0, "the approach must be flown to the right"
    assert moving.commanded_speed_mps > controller.rtl_cfg.settle_max_speed_mps

    creep_ceiling = abs(
        controller.calibration.to_normalized(controller.rtl_cfg.reacquire_creep_speed)
    ) + 1.5 * controller.rtl_cfg.quantization_step
    commands = [
        controller.update(None, DT)
        for _ in range(int(controller.rtl_cfg.reacquire_creep_sec / DT) + 1)
    ]

    assert all(command.vx == 0.0 for command in commands), "the creep must never use vx"
    assert any(command.vy > 0.0 for command in commands), "the creep never went left"
    assert all(command.vy <= creep_ceiling + 1e-9 for command in commands)
    assert any("left" in command.note for command in commands)


def test_the_marker_leaving_after_a_rest_does_not_creep():
    controller = law()
    for _ in range(5):
        controller.update(sighting(0.0, 0.0), DT)

    for _ in range(int(controller.rtl_cfg.reacquire_creep_sec / DT) + 1):
        command = controller.update(None, DT)
        assert command.vx == 0.0 and command.vy == 0.0, "an at-rest loss must hold station"


# ═════════════════════════════════════════════════════ the odometric return


def snapshot(x: float = 0.0, y: float = 0.0, vx: float = 0.0, vy: float = 0.0) -> OdometrySnapshot:
    return OdometrySnapshot(
        x=x,
        y=y,
        raw_altitude=1.0,
        relative_altitude=1.0,
        vx=vx,
        vy=vy,
        vz=0.0,
        yaw=0.0,
        timestamp=0.0,
        sample_count=1,
        ground_reference=0.0,
        takeoff_x=0.0,
        takeoff_y=0.0,
    )


def reference(ex: float, ey: float) -> ReturnReference:
    return ReturnReference(
        ex_body_m=ex,
        ey_body_m=ey,
        distance_m=math.hypot(ex, ey),
        x_m=-ex,
        y_m=-ey,
        source="dead_reckoning",
    )


def guidance(cfg: Optional[ReturnToLaunchConfig] = None) -> RTLGuidanceController:
    controller = RTLGuidanceController(
        cfg or ReturnToLaunchConfig(), FlightKinematicsConfig(), SpeedCalibration(1.0)
    )
    controller.reset()
    return controller


def hold_reference(controller, ex, ey, cycles=200):
    return [
        controller.compute(snapshot(), 0.0, DT, reference=reference(ex, ey))
        for _ in range(cycles)
    ]


def test_the_odometric_return_navigates_right_when_the_origin_is_to_the_right():
    cfg = ReturnToLaunchConfig()
    controller = guidance(cfg)

    commands = hold_reference(controller, 0.0, -1.5)

    assert all(command.vx == 0.0 for command in commands)
    assert all(command.vy <= 0.0 for command in commands)
    assert any(command.vy < 0.0 for command in commands)
    assert commands[-1].target_speed_mps == pytest.approx(-controller.cruise_speed_mps)
    tail = [command.vy for command in commands[-60:]]
    assert sum(tail) / len(tail) == pytest.approx(-cfg.max_speed, abs=0.005)


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_the_odometric_return_recovers_a_bounded_overshoot_to_the_left():
    cfg = ReturnToLaunchConfig()

    recovering = hold_reference(guidance(cfg), 0.0, 0.3, cycles=120)
    assert all(command.vy <= cfg.overshoot_recovery_speed for command in recovering)
    assert 0.0 < recovering[-1].vy <= cfg.overshoot_recovery_speed
    assert "overshoot recovery" in recovering[-1].note

    handler = _Recorder()
    target = logging.getLogger("RTLGuidance")
    previous = target.level
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        far = hold_reference(guidance(cfg), 0.0, cfg.overshoot_recovery_radius_m + 1.5, cycles=120)
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)

    assert all(command.vy == 0.0 for command in far), "beyond the radius the axis is held"
    assert "beyond recovery range" in far[-1].note
    warnings = [r for r in handler.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, "the overshoot warning is emitted once"


@pytest.mark.parametrize("ex, sign", [(0.2, 1.0), (-0.2, -1.0)])
def test_off_track_error_is_trimmed_with_a_small_bilateral_cap(ex, sign):
    cfg = ReturnToLaunchConfig()

    commands = hold_reference(guidance(cfg), ex, 0.0, cycles=150)

    trims = [command.vx for command in commands if command.vx != 0.0]
    assert trims, "the off-track error was never trimmed"
    assert all(math.copysign(1.0, vx) == sign for vx in trims)
    assert all(abs(command.vx) <= cfg.max_lateral_speed + 1e-12 for command in commands)
    assert all(command.vy == 0.0 for command in commands)


def test_dead_reckoning_of_a_lateral_outbound_gives_a_rightward_return():
    clock = FakeTime()
    tracker = DeadReckoningTracker(
        SpeedCalibration(normalized_to_mps=1.0), DeadReckoningConfig(), clock=clock.clock
    )
    tracker.arm()
    tracker.command(vy=0.15)
    for _ in range(int(round(5.0 / DT))):
        clock.now += DT
        tracker.advance()

    ex, ey, distance = tracker.state().body_frame_origin_error()

    assert ey == pytest.approx(-0.75, abs=0.01), "the origin lies to the right"
    assert ex == pytest.approx(0.0, abs=1e-9)
    commands = hold_reference(guidance(), ex, ey, cycles=60)
    assert all(command.vx == 0.0 for command in commands)
    assert any(command.vy < 0.0 for command in commands)
    assert distance == pytest.approx(0.75, abs=0.01)


def test_the_return_channel_converts_on_the_lateral_gain():
    """``max_speed`` and ``overshoot_recovery_speed`` cap the Y channel in m/s on the
    lateral gain, and ``vy`` is normalized on the same gain, so the normalized
    command keeps the configured magnitude when the roll authority differs."""
    cfg = ReturnToLaunchConfig()
    calibration = SpeedCalibration(normalized_to_mps=1.0, lateral_to_mps=0.5)
    controller = RTLGuidanceController(cfg, FlightKinematicsConfig(), calibration)
    controller.reset()

    assert controller.cruise_speed_mps == pytest.approx(cfg.max_speed * 0.5)

    cruising = hold_reference(controller, 0.0, -3.0, cycles=200)
    assert cruising[-1].target_speed_mps == pytest.approx(-cfg.max_speed * 0.5)
    tail = [command.vy for command in cruising[-60:]]
    assert sum(tail) / len(tail) == pytest.approx(-cfg.max_speed, abs=0.005)

    controller.reset()
    recovering = hold_reference(controller, 0.0, 0.3, cycles=150)
    assert recovering[-1].target_speed_mps == pytest.approx(cfg.overshoot_recovery_speed * 0.5)
    assert all(command.vy <= cfg.overshoot_recovery_speed for command in recovering)
    tail = [command.vy for command in recovering[-60:]]
    assert sum(tail) / len(tail) == pytest.approx(cfg.overshoot_recovery_speed, abs=0.005)


def test_the_centering_transmission_feeds_the_governor_the_horizontal_norm():
    """D5: the governor's feedforward argument is the norm of the horizontal demand."""
    ctx = Ctx()
    ctx.governor = RecordingGovernor()
    controller = law()
    step = ClosedLoopRTLStep()

    command = None
    for _ in range(40):
        command = controller.update(sighting(0.3, -0.3), DT)
        step._transmit_centering(ctx, command, DT)
    assert command.vx != 0.0 and command.vy != 0.0, "both axes must be live for the case"

    assert ctx.governor.calls[-1] == pytest.approx(math.hypot(command.vx, command.vy))
    assert ctx.governor.calls[-1] > abs(command.vx)


# ════════════════════════════════════════════════════════ the whole stage


def test_a_complete_rtl_from_the_left_lands_over_the_marker(simulated_time):
    ctx = Ctx(altitude=0.30)
    ctx.params.rtl.timeout_sec = 4.0
    ctx.params.rtl.centering_timeout_sec = 12.0
    sensor = ScriptedSensor(ctx.drone, acquire_after=20, ex=0.0, ey=-0.35)

    status = ClosedLoopRTLStep(sensor=sensor).execute(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.blackboard.rtl_marker_sighted
    assert sensor.residual_m <= ctx.params.rtl.landing_radius_m + 0.01
    assert ctx.drone.land_calls > 0
    before_landing = [
        command
        for command, landing in zip(ctx.drone.commands, ctx.drone.landing_phase)
        if not landing
    ]
    vx, vy, _vz, vyaw = before_landing[-1]
    assert vx == 0.0 and vy == 0.0 and vyaw == 0.0
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in ctx.drone.commands)
