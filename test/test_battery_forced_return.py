"""Critical battery in flight: the mission returns to base, it never lands in place.

The onboard battery net used to be a health condition: a fresh reading at
``battery.land_pct`` failed ``FailsafeSupervisor.evaluate_system_health`` in
the next control cycle and the step landed the aircraft through
``trigger_emergency_land``. The mission is now the single authority for the
return: :class:`BatterySupervisor` calls a one-shot hook that jumps the runner
to Stage 5 through the same gate as the station's ``goto_stage``, and the
forced Stage 5 flies the return of the active profile even with
``rtl.enabled = false``.
"""

from __future__ import annotations

import logging
import threading
import time
import types
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from mvp_mission_bebop.blackboard import MissionBlackboard
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop import context as context_module
from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.engine.stage_gate import StageRequestHandler, refuse_stage_request
from mvp_mission_bebop.mission_mode import MissionMode, stage_five_name
from mvp_mission_bebop.parameters import (
    BatteryConfig,
    FlightKinematicsConfig,
    MissionParameters,
    TimeoutsConfig,
)
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.telemetry.battery import BATTERY_STALE_TIMEOUT_SEC, BatterySupervisor
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.milestones import MILESTONE_KEYS, encode_milestone
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor
from test_aruco_rtl import Ctx as ReturnCtx
from test_aruco_rtl import ScriptedSensor
from test_contracts import STEP_LINE
from test_simple_mode_steps import simulated_time  # noqa: F401
from test_stage_altitude_wiring import Ctx

#: Production control rate, ``FlightKinematicsConfig.control_loop_hz``.
CONTROL_LOOP_HZ = 15.0
ALL_STAGES: Tuple[int, ...] = (1, 2, 3, 4, 5)


@pytest.fixture(autouse=True)
def station_session(monkeypatch):
    """Route any failsafe announcement to an alert line instead of audio."""
    monkeypatch.setenv("BMG_GCS_SESSION", "1")


class _Records(logging.Handler):
    """``caplog`` does not capture on this host; attach to the named logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def records():
    handler = _Records()
    loggers = [logging.getLogger(name) for name in ("MissionContext", "Step5RTL", "BatterySupervisor")]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    yield handler
    for lg, level in zip(loggers, previous):
        lg.removeHandler(handler)
        lg.setLevel(level)


def battery_msg(fraction: float, present: bool = True) -> SimpleNamespace:
    return SimpleNamespace(present=present, percentage=fraction)


class DummyActuator:
    def __init__(self) -> None:
        self.landed = 0
        self.velocities: List[tuple] = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.velocities.append((vx, vy, vz, vyaw))

    def land(self):
        self.landed += 1


def supervisors(land_pct: float = 20.0, stale_timeout_sec: float = BATTERY_STALE_TIMEOUT_SEC):
    kinematics = FlightKinematicsConfig(target_altitude_m=1.0)
    timeouts = TimeoutsConfig()
    odometry = OdometrySupervisor(kinematics, timeouts)
    odometry.inject_synthetic_sample(x=0.0, y=0.0, z=1.0)
    battery = BatterySupervisor(BatteryConfig(land_pct=land_pct), stale_timeout_sec)
    actuator = DummyActuator()
    failsafe = FailsafeSupervisor(actuator, odometry, timeouts, kinematics, battery_supervisor=battery)
    failsafe.notify_frame_received()
    return failsafe, battery, actuator


def bare_context(
    *,
    stages: Tuple[int, ...] = ALL_STAGES,
    current: Optional[int] = 2,
    mode: str = "advanced",
    failsafe: Optional[FailsafeSupervisor] = None,
) -> MissionContext:
    """A ``MissionContext`` with only the state the stage jump touches; no ROS."""
    ctx = MissionContext.__new__(MissionContext)
    ctx.emergency_event = threading.Event()
    ctx.stage_jump_event = threading.Event()
    ctx.requested_stage = None
    ctx.current_stage = current
    ctx.stage_numbers = tuple(stages)
    ctx.entered_by_jump = False
    ctx.detection_reveal_enabled = False
    ctx.blackboard = MissionBlackboard()
    ctx.requested_stage_source = None
    ctx.stage_lock = threading.RLock()
    ctx.return_branch_committed = False
    ctx.params = MissionParameters()
    ctx.params.mission_mode = mode
    ctx.failsafe = failsafe if failsafe is not None else supervisors()[0]
    return ctx


# ------------------------------------------------- reproduction, now inverted


class _Trace:
    def __init__(self) -> None:
        self.injected_at: Optional[float] = None
        self.landed_at: Optional[float] = None
        self.land_calls = 0
        self.emergency_reasons: List[str] = []


def search_ctx_with_battery(simulated_time, monkeypatch, *, land_pct: float = 20.0):
    """A live (not no-fly) Stage 2 cruise at 15 Hz, battery hook wired as ``mission.py`` does."""
    ctx = Ctx(detections=False)
    ctx.drone.no_fly = False
    ctx.params.kinematics.control_loop_hz = CONTROL_LOOP_HZ
    ctx.params.timeouts.search_timeout_sec = 3.0
    ctx.params.platform.edge_ahead_m = None
    ctx.current_stage = 2
    ctx.stage_numbers = ALL_STAGES
    ctx.blackboard.battery_return = False
    ctx.request_stage = types.MethodType(MissionContext.request_stage, ctx)
    ctx.request_battery_return = types.MethodType(MissionContext.request_battery_return, ctx)
    ctx._battery_return_payload = types.MethodType(MissionContext._battery_return_payload, ctx)
    battery = BatterySupervisor(BatteryConfig(land_pct=land_pct))
    battery.inject_synthetic_reading(50.0)
    ctx.failsafe.battery_supervisor = battery
    battery.set_return_hook(
        lambda reason: MissionContext.request_battery_return_or_land(ctx, reason), lambda: True
    )
    trace = _Trace()

    original_move = ctx.drone.move_velocity

    def move(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        original_move(vx, vy, vz, vyaw, duration)
        ctx.failsafe.notify_frame_received()

    original_health = ctx.failsafe.evaluate_system_health
    evaluations = [0]

    def health(**kwargs):
        verdict = original_health(**kwargs)
        evaluations[0] += 1
        # Worst phase for the onboard loop: the reading lands just after a
        # health evaluation, so it waits a full control period for the next one.
        if evaluations[0] == 10 and trace.injected_at is None:
            trace.injected_at = simulated_time.now
            battery.inject_synthetic_reading(land_pct)
        return verdict

    def land():
        trace.land_calls += 1
        if trace.landed_at is None:
            trace.landed_at = simulated_time.now
        return True

    original_trigger = FailsafeSupervisor.trigger_emergency_land

    def trigger(self, reason):
        trace.emergency_reasons.append(reason)
        return original_trigger(self, reason)

    monkeypatch.setattr(FailsafeSupervisor, "trigger_emergency_land", trigger)
    ctx.drone.move_velocity = move
    ctx.drone.land = land
    ctx.failsafe.evaluate_system_health = health
    return ctx, battery, trace


def test_reproduction_threshold_during_the_search_loop_does_not_land_in_place(simulated_time, monkeypatch):
    ctx, _battery, trace = search_ctx_with_battery(simulated_time, monkeypatch)

    status = LateralSearchStep(mode=MissionMode.ADVANCED).execute(ctx)

    latency = None if trace.landed_at is None else trace.landed_at - trace.injected_at
    evidence = (
        f"status={status}, emergency reasons={trace.emergency_reasons}, land() calls={trace.land_calls}, "
        f"last command={ctx.drone.commands[-1]}, reading->land() latency (simulated)={latency}"
    )
    assert trace.injected_at is not None
    assert trace.emergency_reasons == [], evidence
    assert trace.land_calls == 0, evidence
    assert ctx.requested_stage == 5, evidence
    assert ctx.blackboard.battery_return is True, evidence
    assert status is StepStatus.ABORTED, evidence


# --------------------------------------------------------------- the hook


def test_threshold_requests_stage_5_instead_of_landing(monkeypatch):
    failsafe, battery, actuator = supervisors(land_pct=20.0)
    calls: List[str] = []
    landings: List[str] = []
    monkeypatch.setattr(FailsafeSupervisor, "trigger_emergency_land", lambda self, reason: landings.append(reason))
    battery.set_return_hook(lambda reason: calls.append(reason) or True, lambda: True)

    for fraction in (0.22, 0.21, 0.20):
        battery.battery_callback(battery_msg(fraction))

    assert calls == ["Battery at 20% <= return threshold 20%; returning to base."]
    assert battery.critical_reason() == calls[0]
    assert failsafe.evaluate_system_health() == (True, "Nominal")
    assert landings == []
    assert actuator.landed == 0 and actuator.velocities == []


def test_hook_fires_once_per_flight_and_rearms_on_reset():
    _failsafe, battery, _actuator = supervisors(land_pct=20.0)
    calls: List[str] = []
    battery.set_return_hook(lambda reason: calls.append(reason) or True, lambda: True)

    for fraction in (0.20, 0.19, 0.18, 0.05):
        battery.battery_callback(battery_msg(fraction))
    assert len(calls) == 1

    battery.reset_return()
    battery.battery_callback(battery_msg(0.17))
    assert len(calls) == 2
    assert calls[1].startswith("Battery at 17% <= return threshold 20%")


def test_silent_on_ground_and_without_fresh_reading():
    _failsafe, battery, _actuator = supervisors(land_pct=30.0, stale_timeout_sec=0.02)
    calls: List[str] = []
    airborne = {"value": False}
    battery.set_return_hook(lambda reason: calls.append(reason) or True, lambda: airborne["value"])

    battery.battery_callback(battery_msg(0.10))
    assert calls == [], "a critical reading on the ground must not request a return"

    time.sleep(0.05)
    airborne["value"] = True
    assert battery.current_percentage() is None
    assert battery.critical_reason() is None
    for message in (battery_msg(float("nan")), battery_msg(0.10, present=False), battery_msg(1.5)):
        battery.battery_callback(message)
    assert calls == [], "a stale or absent reading is not a reason to act"

    battery.battery_callback(battery_msg(0.10))
    assert len(calls) == 1, "the first fresh critical reading in flight requests the return"


def test_the_hook_rejects_non_callables():
    battery = BatterySupervisor(BatteryConfig())
    with pytest.raises(TypeError):
        battery.set_return_hook(None, lambda: True)
    with pytest.raises(TypeError):
        battery.set_return_hook(lambda reason: True, True)


def test_a_hook_that_raises_is_retried_on_the_next_reading():
    battery = BatterySupervisor(BatteryConfig(land_pct=20.0))
    attempts: List[str] = []

    def hook(reason: str) -> bool:
        attempts.append(reason)
        if len(attempts) == 1:
            raise RuntimeError("context not ready")
        return True

    battery.set_return_hook(hook, lambda: True)
    battery.battery_callback(battery_msg(0.20))
    battery.battery_callback(battery_msg(0.19))
    battery.battery_callback(battery_msg(0.18))

    assert len(attempts) == 2


# ---------------------------------------------------------------- the gate


def test_climb_in_stage_1_requests_the_return():
    assert (
        refuse_stage_request(
            5,
            stage_numbers=ALL_STAGES,
            current_stage=1,
            takeoff_committed=True,
            takeoff_complete=False,
            airborne=True,
        )
        is None
    )
    assert (
        refuse_stage_request(
            5,
            stage_numbers=ALL_STAGES,
            current_stage=5,
            takeoff_committed=True,
            takeoff_complete=True,
            airborne=True,
        )
        == "Stage 5 is already running"
    )

    ctx = bare_context(current=1)
    ctx.blackboard.takeoff_committed = True
    assert ctx.request_battery_return("Battery at 20% <= return threshold 20%; returning to base.") is True
    assert ctx.requested_stage == 5 and ctx.stage_jump_event.is_set()


def test_stage_5_already_running_is_not_restarted():
    ctx = bare_context(current=5)
    ctx.return_branch_committed = True

    milestones: List[Tuple[str, Any]] = []
    original = context_module.emit_milestone
    context_module.emit_milestone = lambda key, payload=None: milestones.append((key, payload))
    try:
        assert ctx.request_battery_return("Battery at 20% <= return threshold 20%; returning to base.") is True
    finally:
        context_module.emit_milestone = original
    assert ctx.requested_stage is None
    assert not ctx.stage_jump_event.is_set()
    assert milestones == [], "no return narration while Stage 5 may be landing in place"
    assert ctx.blackboard.battery_return is False


def test_refused_request_falls_back_to_emergency_land(monkeypatch):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    landings: List[str] = []
    monkeypatch.setattr(FailsafeSupervisor, "trigger_emergency_land", lambda self, reason: landings.append(reason))
    ctx = bare_context(stages=(2, 3), current=2, failsafe=failsafe)
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    reason = "Battery at 20% <= return threshold 20%; returning to base."

    assert ctx.request_battery_return(reason) is False
    assert landings == []

    battery.inject_synthetic_reading(18.0)
    assert ctx.request_battery_return_or_land(reason) is False
    assert len(landings) == 1
    assert landings[0] == "Battery at 18% <= return threshold 20%; stage 5 is not in this run, landing in place."
    assert "returning" not in landings[0]
    assert ctx.requested_stage is None and not ctx.stage_jump_event.is_set()
    assert not getattr(ctx.blackboard, "battery_return", False)
    assert milestones == []


def test_battery_return_milestone_payload(monkeypatch, records):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=3, mode="simple", failsafe=failsafe)
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    reason = battery.critical_reason()

    assert ctx.request_battery_return(reason) is True

    assert milestones == [
        (
            "mission.battery_return",
            {"battery_pct": 18.0, "threshold_pct": 20.0, "from_stage": 3, "mode": "simple"},
        )
    ]
    assert ctx.blackboard.battery_return is True
    assert f"[BATTERY] return forced: {reason}" in records.messages
    assert "mission.battery_return" in MILESTONE_KEYS
    assert encode_milestone("mission.battery_return", milestones[0][1]).startswith(
        "[MILESTONE mission.battery_return] "
    )


# ------------------------------------------------------- the forced Stage 5


def forced_return(monkeypatch, mode: MissionMode, *, forced: bool, rtl_enabled: bool = False):
    """Fly Stage 5 against a synthetic marker; record branches, phases and milestones."""
    ctx = ReturnCtx(altitude=0.30)
    ctx.params.rtl.enabled = rtl_enabled
    ctx.params.rtl.timeout_sec = 4.0
    if forced:
        ctx.blackboard.battery_return = True
    sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.35, ey=-0.25)
    milestones: List[Tuple[str, Dict[str, Any]]] = []
    monkeypatch.setattr(rtl_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    monkeypatch.setattr(ClosedLoopRTLStep, "_announce", staticmethod(lambda action, detail: None))
    step = ClosedLoopRTLStep(sensor=sensor, mode=mode, rtl_enabled=rtl_enabled)
    calls: List[str] = []
    for name in ("_land_in_place", "_land_over_base", "_reverse_search", "_center_over_marker", "_legacy_return"):
        original = getattr(step, name)

        def traced(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        setattr(step, name, traced)
    status = step.execute(ctx)
    ctx.sensor = sensor
    return status, step, ctx, calls, milestones


@pytest.mark.parametrize("mode", list(MissionMode))
def test_forced_return_flies_the_rtl_branch_with_rtl_disabled(monkeypatch, mode):
    status, _step, ctx, calls, _milestones = forced_return(monkeypatch, mode, forced=True)

    assert status is StepStatus.SUCCESS
    assert "_land_in_place" not in calls
    assert "_legacy_return" not in calls
    if mode is MissionMode.SIMPLE:
        assert calls == ["_land_over_base", "_center_over_marker"]
    else:
        assert calls == ["_reverse_search", "_center_over_marker"]
        assert ctx.blackboard.rtl_marker_sighted
    assert ctx.sensor.residual_m <= ctx.params.rtl.landing_radius_m + 0.01
    assert ctx.drone.land_calls > 0
    assert ctx.blackboard.touchdown_confirmed is True


@pytest.mark.parametrize("mode", list(MissionMode))
def test_rtl_disabled_without_battery_still_lands_in_place(monkeypatch, mode):
    status, _step, ctx, calls, milestones = forced_return(monkeypatch, mode, forced=False)

    assert status is StepStatus.SUCCESS
    assert calls == ["_land_in_place"]
    assert not ctx.blackboard.rtl_marker_sighted
    keys = [key for key, _payload in milestones]
    assert "mission.rtl_start" not in keys


@pytest.mark.parametrize("mode", list(MissionMode))
def test_return_attitude_is_the_rtl_camera_tilt(monkeypatch, mode):
    assert MissionParameters().rtl.camera_tilt_deg == -80.0

    _status, _step, ctx, _calls, _milestones = forced_return(monkeypatch, mode, forced=True)

    assert ctx.drone.tilts, "the gimbal was never commanded"
    assert ctx.drone.tilts[0] == pytest.approx(ctx.params.rtl.camera_tilt_deg)
    assert ctx.current_tilt_deg == pytest.approx(-80.0)


@pytest.mark.parametrize("mode", list(MissionMode))
def test_marker_name_is_rtl_when_forced(monkeypatch, records, mode):
    expected = f"STEP 5: {stage_five_name(mode, True)}"
    assert expected in (
        "STEP 5: ArUco RTL & Precision Landing",
        "STEP 5: ArUco Centering & Base Landing",
    ), "the forced name must be one the station already knows"

    _status, step, _ctx, _calls, _milestones = forced_return(monkeypatch, mode, forced=True)

    assert step.name == expected
    marker = f"--- [{expected}] ---"
    assert marker in records.messages
    assert STEP_LINE.search(marker).group(1) == "5"

    _status, nominal, _ctx, _calls, _milestones = forced_return(monkeypatch, mode, forced=False)
    assert nominal.name == "STEP 5: Landing at Inspection Point"


@pytest.mark.parametrize(
    "mode, forced, rtl_enabled",
    [
        (MissionMode.ADVANCED, True, False),
        (MissionMode.SIMPLE, True, False),
        (MissionMode.ADVANCED, False, True),
        (MissionMode.SIMPLE, False, True),
        (MissionMode.ADVANCED, False, False),
    ],
)
def test_return_milestones_carry_battery_flag(monkeypatch, mode, forced, rtl_enabled):
    _status, _step, _ctx, _calls, milestones = forced_return(
        monkeypatch, mode, forced=forced, rtl_enabled=rtl_enabled
    )
    by_key = {key: payload for key, payload in milestones}

    for key in ("mission.landing", "mission.touchdown"):
        assert by_key[key]["battery_return"] is forced, key
    if mode is MissionMode.ADVANCED and (forced or rtl_enabled):
        assert by_key["mission.rtl_start"]["battery_return"] is forced
    if forced:
        assert by_key["mission.landing"]["at_base"] is True
        assert by_key["mission.touchdown"]["at_base"] is True


# ------------------------------------------------------ duplicate requests


class _Cruise(BaseStep):
    """Flies until interrupted, as a stage control loop does."""

    def __init__(self, name: str, trace: List[str], started: threading.Event, hold_sec: float = 5.0):
        super().__init__(name)
        self.trace = trace
        self.started = started
        self.hold_sec = hold_sec

    def execute(self, ctx):
        self.trace.append(self.name)
        self.started.set()
        deadline = time.monotonic() + self.hold_sec
        while time.monotonic() < deadline:
            if ctx.interrupted():
                return StepStatus.ABORTED
            time.sleep(0.005)
        return StepStatus.SUCCESS


class _Instant(BaseStep):
    def __init__(self, name: str, trace: List[str]):
        super().__init__(name)
        self.trace = trace

    def execute(self, ctx):
        self.trace.append(self.name)
        return StepStatus.SUCCESS


def test_duplicate_stage_5_request_runs_stage_once(monkeypatch):
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)
    ctx = bare_context(current=None)
    ctx.blackboard.takeoff_committed = True
    ctx.blackboard.takeoff_complete = True
    trace: List[str] = []
    search_started = threading.Event()
    return_started = threading.Event()
    steps = [
        _Instant("takeoff", trace),
        _Cruise("search", trace, search_started),
        _Instant("tracking", trace),
        _Instant("inspection", trace),
        _Cruise("rtl", trace, return_started, hold_sec=0.4),
    ]
    runner = MissionRunner(ctx, steps, stage_numbers=list(ALL_STAGES))
    station = StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)
    reason = "Battery at 20% <= return threshold 20%; returning to base."
    outcomes: Dict[str, Any] = {}

    def python_and_station():
        search_started.wait(2.0)
        outcomes["python_first"] = ctx.request_battery_return(reason)
        outcomes["station_first"] = station(5)
        return_started.wait(2.0)
        outcomes["station_again"] = station(5)
        outcomes["python_again"] = ctx.request_battery_return(reason)

    worker = threading.Thread(target=python_and_station, daemon=True)
    worker.start()
    succeeded = runner.run()
    worker.join(2.0)

    assert trace == ["takeoff", "search", "rtl"]
    assert succeeded is True
    assert outcomes["python_first"] is True
    assert outcomes["station_again"] is False, "the station's duplicate during Stage 5 must be refused"
    assert outcomes["python_again"] is True
    assert not ctx.stage_jump_event.is_set()


def test_the_blackboard_declares_the_battery_return_flag():
    assert MissionBlackboard().battery_return is False


@pytest.mark.parametrize(
    "reading, expected",
    [(0.10, False), (0.25, False), (0.26, True), (float("nan"), False), (None, True)],
)
def test_one_airborne_predicate_for_the_gate_and_the_battery_hook(reading, expected):
    from mvp_mission_bebop.engine.stage_gate import is_airborne

    def altitude() -> float:
        if reading is None:
            raise RuntimeError("no odometry")
        return reading

    assert is_airborne(altitude) is expected
    handler = StageRequestHandler(SimpleNamespace(), ALL_STAGES, altitude)
    assert handler._airborne() is expected


# ------------------------------------------------- runner jump boundaries (I1)


class _ReturnStep(BaseStep):
    """Stage 5 double: an interrupt is answered with land(), as the Simple touchdown does."""

    def __init__(self, trace: List[str], started: threading.Event, hold_sec: float = 0.3):
        super().__init__("rtl")
        self.trace = trace
        self.started = started
        self.hold_sec = hold_sec

    def execute(self, ctx):
        self.trace.append(self.name)
        self.started.set()
        deadline = time.monotonic() + self.hold_sec
        while time.monotonic() < deadline:
            if ctx.interrupted():
                ctx.drone.land()
                return StepStatus.ABORTED
            time.sleep(0.005)
        return StepStatus.SUCCESS


class _CountingDrone:
    def __init__(self) -> None:
        self.land_calls = 0

    def land(self) -> None:
        self.land_calls += 1

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        pass


class _HookedEvent:
    """``threading.Event`` that runs ``on_clear`` right after each ``clear``."""

    def __init__(self, on_clear) -> None:
        self._event = threading.Event()
        self._on_clear = on_clear

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def clear(self) -> None:
        self._event.clear()
        self._on_clear()


def boundary_runner(monkeypatch, first_stage_cruises: bool):
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)
    ctx = bare_context(current=None)
    ctx.drone = _CountingDrone()
    ctx.blackboard.takeoff_committed = True
    ctx.blackboard.takeoff_complete = True
    trace: List[str] = []
    search_started = threading.Event()
    return_started = threading.Event()
    steps = [
        _Instant("takeoff", trace),
        _Cruise("search", trace, search_started) if first_stage_cruises else _Instant("search", trace),
        _Instant("tracking", trace),
        _Instant("inspection", trace),
        _ReturnStep(trace, return_started),
    ]
    runner = MissionRunner(ctx, steps, stage_numbers=list(ALL_STAGES))
    station = StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)
    return ctx, runner, station, trace, search_started


def test_a_duplicate_request_while_the_jump_is_consumed_does_not_restart_stage_5(monkeypatch):
    ctx, runner, station, trace, search_started = boundary_runner(monkeypatch, True)
    duplicates: List[bool] = []

    def station_duplicate() -> None:
        if not duplicates:
            duplicates.append(station(5))

    ctx.stage_jump_event = _HookedEvent(station_duplicate)
    reason = "Battery at 20% <= return threshold 20%; returning to base."

    def python_hook() -> None:
        search_started.wait(2.0)
        ctx.request_battery_return(reason)

    worker = threading.Thread(target=python_hook, daemon=True)
    worker.start()
    succeeded = runner.run()
    worker.join(2.0)

    assert duplicates == [False], "the duplicate must meet the gate with Stage 5 already current"
    assert trace == ["takeoff", "search", "rtl"]
    assert ctx.drone.land_calls == 0
    assert succeeded is True


def test_a_request_at_the_stage_4_to_5_boundary_runs_stage_5_once(monkeypatch):
    ctx, runner, _station, trace, _started = boundary_runner(monkeypatch, False)
    original = runner._take_stage_jump
    reason = "Battery at 20% <= return threshold 20%; returning to base."
    fired: List[bool] = []

    def take_then_hook():
        target = original()
        if target is None and ctx.current_stage == 4 and not fired:
            fired.append(ctx.request_battery_return(reason))
        return target

    runner._take_stage_jump = take_then_hook

    succeeded = runner.run()

    assert fired == [True]
    assert trace == ["takeoff", "search", "tracking", "inspection", "rtl"]
    assert ctx.drone.land_calls == 0
    assert succeeded is True
    assert ctx.blackboard.battery_return is True
    assert not ctx.stage_jump_event.is_set()


@pytest.mark.parametrize(
    "source, suffix",
    [("battery", "(retorno forçado por bateria crítica)"), ("station", "(salto comandado pela estação)")],
)
def test_the_jump_marker_names_who_asked(monkeypatch, source, suffix):
    ctx, runner, station, trace, search_started = boundary_runner(monkeypatch, True)
    reason = "Battery at 20% <= return threshold 20%; returning to base."

    def request() -> None:
        search_started.wait(2.0)
        if source == "battery":
            ctx.request_battery_return(reason)
        else:
            station(5)

    recorder = _Records()
    target = logging.getLogger("MissionRunner")
    previous = target.level
    target.setLevel(logging.INFO)
    target.addHandler(recorder)
    worker = threading.Thread(target=request, daemon=True)
    worker.start()
    try:
        runner.run()
    finally:
        worker.join(2.0)
        target.removeHandler(recorder)
        target.setLevel(previous)

    markers = [m for m in recorder.messages if "[STEP 5: rtl]" in m]
    assert markers == [f"--- [STEP 5: rtl] --- {suffix}"]
    assert STEP_LINE.search(markers[0]).group(1) == "5"


def test_request_stage_rejects_an_unknown_source():
    ctx = bare_context()
    with pytest.raises(ValueError):
        ctx.request_stage(5, source="operator")
    with pytest.raises(TypeError):
        ctx.request_stage(5, source=None)


# ------------------------------------------------------ the climb (I2)


def climb_ctx(monkeypatch, *, altitude: float):
    """Stage 1 between the takeoff command and its confirmation, no fresh flying_state."""
    from mvp_mission_bebop.steps import takeoff as takeoff_module

    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    ctx = bare_context(current=1, failsafe=failsafe)
    ctx.drone = _CountingDrone()
    ctx.drone.no_fly = False
    velocities: List[tuple] = []
    ctx.drone.move_velocity = lambda vx=0.0, vy=0.0, vz=0.0, vyaw=0.0: velocities.append((vx, vy, vz, vyaw))
    failsafe.actuator = ctx.drone
    ctx.params.kinematics.control_loop_hz = 200.0
    ctx.params.timeouts.takeoff_confirm_timeout_sec = 2.0
    ctx.flying_state = None
    ctx.flying_state_timestamp = 0.0
    ctx.blackboard.takeoff_committed = True
    ctx.odom_supervisor = SimpleNamespace(snapshot=lambda: SimpleNamespace(relative_altitude=altitude))
    battery.set_return_hook(
        ctx.request_battery_return_or_land,
        lambda: altitude > 0.25,
    )
    frames = [0]

    def grab_frame(timeout_sec: float = 1.0):
        frames[0] += 1
        if frames[0] == 3:
            battery.inject_synthetic_reading(20.0)
        return None

    ctx.grab_frame = grab_frame
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)
    monkeypatch.setattr(takeoff_module.time, "sleep", lambda _s: None)
    return ctx, velocities


def test_the_takeoff_confirmation_answers_the_return_with_abort_not_a_landing(monkeypatch):
    from mvp_mission_bebop.steps.takeoff import TakeoffStep

    ctx, velocities = climb_ctx(monkeypatch, altitude=0.28)
    step = TakeoffStep()
    step._t_takeoff_cmd = 0.0
    spoken: List[Tuple[Any, ...]] = []
    monkeypatch.setattr(step, "_announce", lambda *args, **kwargs: spoken.append(args))

    status = step._flight_sequence(ctx)

    assert status is StepStatus.ABORTED
    assert ctx.drone.land_calls == 0
    assert velocities == []
    assert spoken == []
    assert ctx.blackboard.takeoff_committed is True
    assert ctx.failsafe.failsafe_active is False
    assert ctx.requested_stage == 5 and ctx.blackboard.battery_return is True


def test_the_climb_return_then_flies_stage_5(monkeypatch):
    from mvp_mission_bebop.steps.takeoff import TakeoffStep

    ctx, _velocities = climb_ctx(monkeypatch, altitude=0.28)
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: None))
    takeoff = TakeoffStep()
    takeoff._t_takeoff_cmd = 0.0
    monkeypatch.setattr(takeoff, "_announce", lambda *args, **kwargs: None)

    class _FlightOnly(BaseStep):
        def execute(self, step_ctx):
            return takeoff._flight_sequence(step_ctx)

    trace: List[str] = []
    steps = [
        _FlightOnly("STEP 1: flight"),
        _Instant("search", trace),
        _Instant("tracking", trace),
        _Instant("inspection", trace),
        _ReturnStep(trace, threading.Event(), hold_sec=0.05),
    ]
    ctx.current_stage = None
    runner = MissionRunner(ctx, steps, stage_numbers=list(ALL_STAGES))

    assert runner.run() is True
    assert trace == ["rtl"]
    assert ctx.drone.land_calls == 0


# ------------------------------------- station request on a critical charge


@pytest.mark.parametrize("mode", list(MissionMode))
def test_a_station_stage_5_request_on_a_critical_charge_is_forced(monkeypatch, mode):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=3, mode=mode.value, failsafe=failsafe)
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    station = StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)

    assert station(5) is True

    assert ctx.requested_stage == 5
    assert ctx.requested_stage_source == "station"
    assert ctx.blackboard.battery_return is True
    assert [key for key, _payload in milestones] == ["mission.battery_return"]

    _status, _step, _rctx, calls, _milestones = forced_return(
        monkeypatch, mode, forced=ctx.blackboard.battery_return
    )
    assert "_land_in_place" not in calls


@pytest.mark.parametrize("reading", [50.0, None, "stale"])
def test_a_station_stage_5_request_without_a_critical_fresh_reading_stays_unforced(monkeypatch, reading):
    failsafe, battery, _actuator = supervisors(land_pct=20.0, stale_timeout_sec=0.02)
    if reading == "stale":
        battery.inject_synthetic_reading(10.0)
        time.sleep(0.05)
    elif reading is not None:
        battery.inject_synthetic_reading(reading)
    ctx = bare_context(current=3, failsafe=failsafe)
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    station = StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)

    assert station(5) is True

    assert ctx.requested_stage == 5
    assert ctx.blackboard.battery_return is False
    assert milestones == []


def test_a_station_request_for_another_stage_ignores_the_battery(monkeypatch):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=2, failsafe=failsafe)
    ctx.blackboard.takeoff_complete = True
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)

    assert StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)(4) is True
    assert ctx.requested_stage == 4
    assert ctx.blackboard.battery_return is False


# ------------------------------------------------ round 2: atomic stage 5 entry


def runner_over_the_return(monkeypatch, mode: MissionMode):
    """A runner whose Stage 5 is the real ``ClosedLoopRTLStep`` with ``rtl.enabled = false``."""
    ctx = ReturnCtx(altitude=0.30)
    ctx.params.rtl.enabled = False
    ctx.params.rtl.timeout_sec = 4.0
    ctx.params.mission_mode = mode.value
    ctx.blackboard.battery_return = False
    ctx.blackboard.takeoff_committed = True
    ctx.blackboard.takeoff_complete = True
    ctx.current_stage = None
    ctx.stage_numbers = ALL_STAGES
    ctx.entered_by_jump = False
    ctx.detection_reveal_enabled = True
    ctx.requested_stage_source = None
    ctx.stage_lock = threading.RLock()
    ctx.return_branch_committed = False
    for name in ("request_stage", "request_battery_return", "_battery_return_payload"):
        setattr(ctx, name, types.MethodType(getattr(MissionContext, name), ctx))
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)
    monkeypatch.setattr(rtl_module, "emit_milestone", lambda key, payload=None: None)
    monkeypatch.setattr(ClosedLoopRTLStep, "_announce", staticmethod(lambda action, detail: None))
    sensor = ScriptedSensor(ctx.drone, acquire_after=3, ex=0.35, ey=-0.25)
    step = ClosedLoopRTLStep(sensor=sensor, mode=mode, rtl_enabled=False)
    calls: List[str] = []
    for name in ("execute", "_land_in_place", "_reverse_search", "_land_over_base"):
        original = getattr(step, name)

        def traced(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        setattr(step, name, traced)
    trace: List[str] = []
    steps = [_Instant(name, trace) for name in ("takeoff", "search", "tracking", "inspection")] + [step]
    runner = MissionRunner(ctx, steps, stage_numbers=list(ALL_STAGES))
    return ctx, runner, step, calls


@pytest.mark.parametrize("who", ["battery", "station"])
@pytest.mark.parametrize("mode", list(MissionMode))
def test_a_request_after_stage_5_is_named_but_before_it_runs_is_forced_once(monkeypatch, mode, who):
    ctx, runner, step, calls = runner_over_the_return(monkeypatch, mode)
    reason = "Battery at 18% <= return threshold 20%; returning to base."
    original = runner._open_reveal_past_the_search
    outcomes: List[bool] = []

    def named_then_request(stage: int) -> None:
        if stage == 5 and not outcomes:
            assert ctx.current_stage == 5
            outcomes.append(ctx.request_battery_return(reason, source=who))
        original(stage)

    runner._open_reveal_past_the_search = named_then_request

    assert runner.run() is True
    assert outcomes == [True]
    assert calls.count("execute") == 1, calls
    assert "_land_in_place" not in calls
    if mode is MissionMode.ADVANCED:
        assert "_reverse_search" in calls
    else:
        assert "_land_over_base" in calls
    assert ctx.blackboard.battery_return is True
    assert step.name == f"STEP 5: {stage_five_name(mode, True)}"
    assert ctx.blackboard.touchdown_confirmed is True


def test_a_request_after_stage_5_committed_its_branch_changes_nothing(monkeypatch):
    ctx, runner, _step, calls = runner_over_the_return(monkeypatch, MissionMode.ADVANCED)
    reason = "Battery at 18% <= return threshold 20%; returning to base."
    outcomes: List[bool] = []
    original_land_in_place = _step._land_in_place

    def land_in_place_then_request(*args, **kwargs):
        outcomes.append(ctx.request_battery_return(reason))
        return original_land_in_place(*args, **kwargs)

    _step._land_in_place = land_in_place_then_request

    assert runner.run() is True
    assert outcomes == [True]
    assert calls.count("execute") == 1
    assert ctx.blackboard.battery_return is False
    assert not ctx.stage_jump_event.is_set()


@pytest.mark.parametrize(
    "altitude, no_fly, forced",
    [(1.0, False, True), (0.10, False, False), (1.0, True, False)],
)
def test_a_station_request_is_forced_only_airborne_and_flying_for_real(monkeypatch, altitude, no_fly, forced):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=3, failsafe=failsafe)
    ctx.params.no_fly = no_fly
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)

    assert StageRequestHandler(ctx, ALL_STAGES, lambda: altitude)(5) is True
    assert ctx.requested_stage == 5
    assert ctx.blackboard.battery_return is forced


def test_the_crossing_is_announced_once_when_hook_and_station_both_ask(monkeypatch):
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=3, failsafe=failsafe)
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    reason = battery.critical_reason()

    assert ctx.request_battery_return(reason) is True
    assert StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)(5) is True
    assert ctx.request_battery_return(reason) is True

    assert [key for key, _payload in milestones] == ["mission.battery_return"]
    assert ctx.requested_stage == 5 and ctx.requested_stage_source == "battery"


# ------------------------------- round 3: leaving and re-entering the return


def test_a_station_goto_5_after_the_operator_left_the_forced_return_is_requested_again(monkeypatch):
    """5 -> operator goto 3 -> critical goto 5: the second request must fly, not be swallowed."""
    failsafe, battery, _actuator = supervisors(land_pct=20.0)
    battery.inject_synthetic_reading(18.0)
    ctx = bare_context(current=5, failsafe=failsafe)
    ctx.blackboard.takeoff_complete = True
    ctx.blackboard.battery_return = True
    ctx.return_branch_committed = True
    milestones: List[Tuple[str, Any]] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: milestones.append((key, payload)))
    station = StageRequestHandler(ctx, ALL_STAGES, lambda: 1.0)

    assert station(3) is True and ctx.requested_stage == 3
    # The runner takes the operator's jump and Stage 3 starts.
    ctx.stage_jump_event.clear()
    ctx.requested_stage = None
    ctx.current_stage = 3
    ctx.return_branch_committed = False

    assert station(5) is True

    assert ctx.requested_stage == 5 and ctx.stage_jump_event.is_set()
    assert ctx.requested_stage_source == "station"
    assert ctx.blackboard.battery_return is True
    assert milestones == [], "the crossing was already narrated on the first return"


def test_the_hook_re_requests_the_return_when_no_jump_is_pending(monkeypatch):
    ctx = bare_context(current=4)
    ctx.blackboard.takeoff_complete = True
    ctx.blackboard.battery_return = True
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)

    assert ctx.request_battery_return("Battery at 18% <= return threshold 20%; returning to base.") is True
    assert ctx.requested_stage == 5 and ctx.requested_stage_source == "battery"


def test_a_pending_jump_to_5_is_not_requested_twice(monkeypatch):
    ctx = bare_context(current=3)
    ctx.blackboard.takeoff_complete = True
    requests: List[int] = []
    monkeypatch.setattr(context_module, "emit_milestone", lambda key, payload=None: None)
    reason = "Battery at 18% <= return threshold 20%; returning to base."

    assert ctx.request_battery_return(reason) is True
    original = ctx.request_stage
    ctx.request_stage = lambda stage, *, source="station": requests.append(stage) or original(stage, source=source)

    assert ctx.request_battery_return(reason, source="station") is True

    assert requests == [], "a jump to Stage 5 is already pending"
    assert ctx.requested_stage_source == "battery"


def test_re_entering_stage_5_clears_the_committed_branch(monkeypatch):
    ctx, runner, step, calls = runner_over_the_return(monkeypatch, MissionMode.ADVANCED)
    ctx.return_branch_committed = True
    reason = "Battery at 18% <= return threshold 20%; returning to base."
    original = runner._open_reveal_past_the_search
    outcomes: List[bool] = []

    def named_then_request(stage: int) -> None:
        if stage == 5 and not outcomes:
            assert ctx.return_branch_committed is False, "the naming block must clear the previous pass"
            outcomes.append(ctx.request_battery_return(reason))
        original(stage)

    runner._open_reveal_past_the_search = named_then_request
    land_calls_at_return: List[int] = []
    traced_reverse_search = step._reverse_search

    def reverse_search_counting_landings(*args, **kwargs):
        land_calls_at_return.append(ctx.drone.land_calls)
        return traced_reverse_search(*args, **kwargs)

    step._reverse_search = reverse_search_counting_landings

    assert runner.run() is True
    assert outcomes == [True]
    assert calls.count("execute") == 1
    assert "_land_in_place" not in calls
    assert "_reverse_search" in calls
    assert land_calls_at_return == [0], "land() was commanded before the return flight"
    assert ctx.blackboard.battery_return is True
    assert ctx.blackboard.touchdown_confirmed is True
    assert step.name == f"STEP 5: {stage_five_name(MissionMode.ADVANCED, True)}"


# ---------------------------------- round 4: the jump consumed onto Stage 5


class _JumpToReturn(BaseStep):
    """Unwinds on an unforced station ``goto 5`` with a commitment left by a previous Stage 5 pass.

    The naming block clears the commitment when this step is named, so the
    stale value is restored here: the jump must clear it on its own.
    """

    def __init__(self) -> None:
        super().__init__("inspection")

    def execute(self, ctx):
        ctx.return_branch_committed = True
        ctx.request_stage(5, source="station")
        return StepStatus.ABORTED


def test_a_request_right_after_the_jump_onto_a_re_entered_stage_5_is_forced(monkeypatch):
    ctx, runner, step, calls = runner_over_the_return(monkeypatch, MissionMode.ADVANCED)
    runner.steps[3] = _JumpToReturn()
    reason = "Battery at 18% <= return threshold 20%; returning to base."
    original = runner._take_stage_jump
    outcomes: List[bool] = []

    def take_then_request() -> Optional[int]:
        index = original()
        if index == 4 and not outcomes:
            assert ctx.current_stage == 5
            outcomes.append(ctx.request_battery_return(reason))
        return index

    runner._take_stage_jump = take_then_request

    runner.run()
    assert outcomes == [True]
    assert ctx.blackboard.battery_return is True
    assert calls.count("execute") == 1
    assert "_land_in_place" not in calls
    assert "_reverse_search" in calls
    assert ctx.blackboard.touchdown_confirmed is True
    assert step.name == f"STEP 5: {stage_five_name(MissionMode.ADVANCED, True)}"
