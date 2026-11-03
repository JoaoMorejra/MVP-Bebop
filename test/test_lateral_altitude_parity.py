"""Altitude conservation over the takeoff pad: the lateral pattern keeps the forward pattern's effect.

The takeoff is on a 1.0 m pad. Everything that conserves altitude over it -- the
platform monitor, the altitude-hold edge guard, the re-entry ceiling allowance
and breach grace, the floor reference, the touchdown threshold off the pad and
the departure settle -- consumes ONE producer: the along-track position fed by
``OdometrySupervisor`` to the platform monitor. The forward pattern projected the
displacement on the body X axis; the lateral pattern projects it on the body
left axis (:func:`outbound_track_m`).

The parity argument is numerical: the same path, once flown along X with the
frontal projection the forward pattern used and once flown along Y with the
lateral one, must produce the same sequence of platform states and the same
governor decisions. The frontal projection is replicated here only as the
reference; the mission no longer carries it.
"""

from __future__ import annotations

import functools
import math
import random
import types
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.ceiling_schedule import TOTEM_CEILING_HEADROOM_M
from mvp_mission_bebop.estimation.platform_monitor import (
    DIRECTION_CONFIRM_SAMPLES,
    HYSTERESIS_M,
    PlatformState,
)
from mvp_mission_bebop.mission_mode import NATIVE_HOVER_HEIGHT_M
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import search as search_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.rtl import _MARKER_POSITION_TTL_SEC, ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import ForwardSearchStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep
from mvp_mission_bebop.telemetry import odometry as odometry_module
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, OdometrySupervisor

PAD_HEIGHT_M = 1.0
TARGET_FLOOR_M = 1.8
TARGET = TARGET_FLOOR_M - PAD_HEIGHT_M
CEILING_MARGIN_M = 0.6
ALLOWANCE_M = 0.4
VENUE_AGL_M = 3.0
EDGES_M = (0.30, 1.0)
DT = 1.0 / 15.0
#: Path resolution, metres. Incommensurate with the window bounds (``on_pad`` is
#: 0.15 m or 0.85 m), so no sample lands on a threshold, where the frontal and
#: lateral projections may differ in the last ulp.
STEP_M = 0.007
FAR_M = 3.0

ON = PlatformState.ON
LEAVING = PlatformState.LEAVING
OFF = PlatformState.OFF
ENTERING = PlatformState.ENTERING


class SimulatedClock:
    """Monotonic clock advanced by the test, so the monitor and the grace see sample time."""

    def __init__(self) -> None:
        self.now = 5000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> SimulatedClock:
    simulated = SimulatedClock()
    monkeypatch.setattr(odometry_module, "time", types.SimpleNamespace(monotonic=simulated.monotonic))
    return simulated


def frontal_track_m(origin_dx: float, origin_dy: float, yaw_rad: float) -> float:
    """The forward pattern's producer: displacement projected on the body X axis."""
    return -(math.cos(yaw_rad) * origin_dx + math.sin(yaw_rad) * origin_dy)


def pad_params(edge: Optional[float] = 0.30) -> MissionParameters:
    params = MissionParameters()
    params.kinematics.target_altitude_m = TARGET_FLOOR_M
    params.kinematics.altitude_ceiling_margin_m = CEILING_MARGIN_M
    params.platform.height_m = PAD_HEIGHT_M
    params.platform.edge_ahead_m = edge
    params.platform.sonar_half_angle_deg = 20.0
    params.platform.ceiling_allowance_m = ALLOWANCE_M
    params.platform.venue_ceiling_agl_m = VENUE_AGL_M
    return params


class Flight:
    """A supervisor on the pad, origin frozen at ``origin``, sampled at ``DT`` of simulated time."""

    def __init__(
        self,
        params: MissionParameters,
        clock: SimulatedClock,
        origin: Tuple[float, float] = (0.4, -0.2),
        yaw: float = 0.0,
    ) -> None:
        self.params = params
        self.clock = clock
        self.origin = origin
        self.yaw = yaw
        self.odom = OdometrySupervisor(
            params.kinematics, params.timeouts, params.calibration, params.platform
        )
        for _ in range(params.calibration.min_ground_samples):
            self._inject(origin[0], origin[1], 0.0)
        assert self.odom.calibrate_ground_reference()
        self._inject(origin[0], origin[1], TARGET)
        assert self.odom.freeze_hover_takeoff_origin()

    def _inject(
        self, x: float, y: float, z: float, vx: float = 0.0, vy: float = 0.0, advance: bool = True
    ) -> None:
        if advance:
            self.clock.now += DT
        self.odom.inject_synthetic_sample(x=x, y=y, z=z, vx=vx, vy=vy, yaw=self.yaw)

    def at(
        self, s: float, axis: str, z: float = TARGET, speed: float = 0.0, advance: bool = True
    ) -> None:
        """One sample ``s`` metres from the origin along the body ``axis`` (``"x"`` or ``"y"``).

        ``advance`` moves the simulated clock by ``DT`` first; a caller paced by
        its own loop clock passes ``False``.
        """
        if axis == "x":
            ux, uy = math.cos(self.yaw), math.sin(self.yaw)
            vx, vy = speed, 0.0
        else:
            ux, uy = -math.sin(self.yaw), math.cos(self.yaw)
            vx, vy = 0.0, speed
        self._inject(self.origin[0] + s * ux, self.origin[1] + s * uy, z, vx, vy, advance)

    @property
    def state(self) -> PlatformState:
        return self.odom.platform_report().state


def round_trip(far: float = FAR_M) -> List[float]:
    count = int(round(far / STEP_M))
    out = [k * STEP_M for k in range(count + 1)]
    return out + out[-2::-1]


def compress(states: Sequence[PlatformState]) -> List[PlatformState]:
    out: List[PlatformState] = []
    for state in states:
        if not out or out[-1] is not state:
            out.append(state)
    return out


def window(params: MissionParameters, height: float = TARGET) -> Dict[str, float]:
    """Guard window bounds of ``PlatformMonitor.update`` for this geometry."""
    cfg = params.platform
    s_star = cfg.edge_ahead_m + max(height, 0.3) * math.tan(math.radians(cfg.sonar_half_angle_deg))
    return {
        "s_star": s_star,
        "lower": s_star - cfg.edge_uncertainty_m - cfg.guard_lead_m,
        "upper": s_star + cfg.edge_uncertainty_m + cfg.guard_extent_m,
        "on_pad": cfg.edge_ahead_m - cfg.edge_uncertainty_m,
    }


Trace = List[Tuple[PlatformState, bool, Optional[float], float, float, bool]]


def trace(flight: Flight, path: Sequence[float], axis: str) -> Trace:
    """Per sample: state, guard flag, along-track, ceiling, governor ``vz`` and guard engagement.

    The altitude wanders around the target so every branch of the law runs,
    with and without the guard.
    """
    gov = lateral_governor(flight)
    out: Trace = []
    for k, s in enumerate(path):
        heading = 1.0 if k == 0 or s >= path[k - 1] else -1.0
        z = TARGET + 0.04 * math.sin(k / 9.0)
        flight.at(s, axis, z=z, speed=0.2 * heading)
        vz = gov.compute_vz(z, DT, vx_commanded=0.2)
        report = flight.odom.platform_report()
        out.append(
            (
                report.state,
                report.guard_active,
                report.along_track_m,
                flight.odom.altitude_ceiling,
                vz,
                gov.guard_engaged,
            )
        )
    return out


def lateral_governor(flight: Flight) -> AltitudeHoldGovernor:
    """The governor exactly as ``mission.py`` builds it."""
    return AltitudeHoldGovernor(
        target_altitude=flight.params.relative_target_altitude_m,
        config=flight.params.governor,
        guard_source=flight.odom.platform_report,
        platform_cfg=flight.params.platform,
        descent_demand=flight.odom.reentry_descent_demanded,
    )


# ═════════════════════════════════════════════════════════ the producer


@pytest.mark.parametrize("yaw", [0.0, 0.7])
@pytest.mark.parametrize("edge", EDGES_M)
def test_the_state_sequence_is_the_same_on_the_lateral_axis(monkeypatch, clock, edge, yaw):
    """Out 3 m and back: X with the frontal producer, Y with the lateral one, sample by sample."""
    path = round_trip()
    with monkeypatch.context() as patch:
        patch.setattr(odometry_module, "outbound_track_m", frontal_track_m)
        forward = trace(Flight(pad_params(edge), clock, yaw=yaw), path, "x")
    lateral = trace(Flight(pad_params(edge), clock, yaw=yaw), path, "y")

    def decisions(rows: Trace) -> List[Tuple[PlatformState, bool, float, float, bool]]:
        return [(state, guard, ceiling, vz, engaged) for state, guard, _a, ceiling, vz, engaged in rows]

    assert decisions(lateral) == decisions(forward)
    assert [row[2] for row in lateral] == pytest.approx([row[2] for row in forward], abs=1e-9)
    assert any(vz > 0.0 for *_rest, vz, _e in lateral) and any(vz < 0.0 for *_rest, vz, _e in lateral)
    assert {row[0] for row in lateral if row[5]} == {LEAVING, ENTERING}
    states = [row[0] for row in lateral]
    assert compress(states) == [ON, LEAVING, OFF, ENTERING, ON]
    turn = len(path) // 2
    assert compress(states[: turn + 1]) == [ON, LEAVING, OFF]
    assert compress(states[turn:]) == [OFF, ENTERING, ON]
    assert all(guard == (state in (LEAVING, ENTERING)) for state, guard, *_rest in lateral)


@pytest.mark.parametrize("edge", EDGES_M)
def test_motion_along_x_never_arms_the_guard(clock, edge):
    """Review Focus 2: the monitor measures the lateral track only.

    Off-track drift along X past the pad half-width (0.30 m) leaves the pad in
    the real world without the guard engaging. It is the open risk of the
    lateral pattern: Stages 2 and 3 never command ``vx``, and the residual
    drift is measured in the field.
    """
    flight = Flight(pad_params(edge), clock)
    base = flight.odom.altitude_ceiling
    for s in round_trip():
        flight.at(s, "x", speed=0.2)
        report = flight.odom.platform_report()
        assert report.state is ON
        assert not report.guard_active
        assert flight.odom.altitude_ceiling == base


# ═════════════════════════════════════════════════════════ the right return


@pytest.mark.parametrize("edge", EDGES_M)
def test_the_return_from_the_right_enters_the_pad_through_entering(clock, edge):
    params = pad_params(edge)
    flight = Flight(params, clock)
    base = max(TARGET_FLOOR_M - PAD_HEIGHT_M, NATIVE_HOVER_HEIGHT_M) + TOTEM_CEILING_HEADROOM_M
    assert flight.odom.altitude_ceiling == pytest.approx(base)
    for s in round_trip()[: int(round(FAR_M / STEP_M)) + 1]:
        flight.at(s, "y", speed=0.2)
    assert flight.state is OFF

    returning: List[PlatformState] = []
    s = FAR_M
    while flight.state is not ENTERING:
        s -= STEP_M
        flight.at(s, "y", speed=-0.2)
        returning.append(flight.state)
        assert s > 0.0, "the rightward return never entered the guard window"

    report = flight.odom.platform_report()
    assert report.guard_active
    assert report.direction == -1
    assert flight.odom.altitude_ceiling == pytest.approx(base + ALLOWANCE_M)

    gov = lateral_governor(flight)
    false_climb = base + ALLOWANCE_M + 0.15
    commands = []
    for _ in range(15):
        flight.at(s, "y", z=false_climb, speed=-0.2)
        commands.append(gov.compute_vz(false_climb, DT))
    assert flight.state is ENTERING
    assert flight.odom.ceiling_breach_streak >= 3
    assert 15 * DT < params.platform.reentry_breach_grace_sec
    assert not flight.odom.is_ceiling_breached(), "breach inside the re-entry grace was reported"
    assert flight.odom.reentry_descent_demanded()
    assert gov.guard_engaged
    assert all(vz <= 0.0 for vz in commands)
    assert min(commands) < 0.0

    grace_ends = clock.now - 14 * DT + params.platform.reentry_breach_grace_sec
    while clock.now < grace_ends:
        flight.at(s, "y", z=false_climb, speed=-0.2)
        assert flight.state is ENTERING
    flight.at(s, "y", z=false_climb, speed=-0.2)
    assert flight.state is ENTERING
    assert flight.odom.is_ceiling_breached(), "the breach was never reported once the grace expired"
    assert not flight.odom.reentry_descent_demanded()

    while flight.state is not ON:
        s -= STEP_M
        flight.at(s, "y", speed=-0.2)
        returning.append(flight.state)
        assert s > -1.0, "the rightward return never reached the pad top"
    assert compress([OFF] + returning) == [OFF, ENTERING, ON]
    assert s <= window(params)["on_pad"]
    assert flight.odom.altitude_ceiling == pytest.approx(base)


@pytest.mark.parametrize("where", ["on", "off"])
def test_the_same_climb_outside_entering_is_a_breach_at_once(clock, where):
    """Positive control of the re-entry grace: the breach path is live outside ``ENTERING``."""
    params = pad_params(0.30)
    flight = Flight(params, clock)
    s = 0.0
    flight.at(s, "y")
    if where == "off":
        while s < FAR_M:
            s += STEP_M
            flight.at(s, "y", speed=0.2)
    expected = ON if where == "on" else OFF
    assert flight.state is expected
    false_climb = TARGET_FLOOR_M - PAD_HEIGHT_M + CEILING_MARGIN_M + ALLOWANCE_M + 0.15
    for _ in range(3):
        flight.at(s, "y", z=false_climb)
    assert flight.state is expected
    assert flight.odom.is_ceiling_breached()
    assert not flight.odom.reentry_descent_demanded()


@pytest.mark.parametrize("edge", EDGES_M)
def test_the_governor_engages_the_edge_guard_laterally(clock, edge):
    params = pad_params(edge)
    flight = Flight(params, clock)
    gov = lateral_governor(flight)
    global_cfg = params.governor
    cfg = params.platform
    engaged_in = set()
    for s in round_trip():
        flight.at(s, "y", speed=0.2)
        gov.compute_vz(TARGET, DT, vx_commanded=0.2)
        state = flight.state
        assert gov.guard_engaged == (state in (LEAVING, ENTERING)), state
        if gov.guard_engaged:
            engaged_in.add(state)
            assert gov.gains.kp == pytest.approx(global_cfg.kp * cfg.guard_kp_scale)
            assert gov.gains.output_limits == pytest.approx(
                (-cfg.guard_descent_cap, cfg.guard_climb_authority)
            )
        else:
            assert gov.gains.kp == pytest.approx(global_cfg.kp)
            assert gov.gains.output_limits == pytest.approx(
                (-global_cfg.max_descent_speed, global_cfg.max_climb_speed)
            )
    assert engaged_in == {LEAVING, ENTERING}


def test_the_marker_override_dominates_odometry_drift(clock):
    """Odometry 1 m off on the return; the marker over the pad still places the airframe ON."""
    params = pad_params(1.0)
    flight = Flight(params, clock)
    ctx = types.SimpleNamespace(params=params, odom_supervisor=flight.odom)
    for s in round_trip()[: int(round(FAR_M / STEP_M)) + 1]:
        flight.at(s, "y", speed=0.2)
    true_along = 0.10
    odometric = true_along + 1.0
    s = FAR_M
    while s > odometric + 1e-9:
        s -= STEP_M
        flight.at(s, "y", speed=-0.2)
    assert flight.state is ENTERING, "the drifted odometry alone keeps the airframe in the window"
    assert odometric > window(params)["on_pad"]

    step = ClosedLoopRTLStep()
    # The marker sits on the takeoff point: 0.10 m left of it, the marker is to the right.
    step._report_pad_position(ctx, -true_along)
    flight.at(odometric, "y")
    report = flight.odom.platform_report()
    assert report.state is ON
    assert report.along_track_m == pytest.approx(true_along)
    assert not report.guard_active

    held = 0
    while flight.odom.platform_report().along_track_m == pytest.approx(true_along):
        flight.at(odometric, "y")
        held += 1
        assert held < 100, "the override never expired"
    assert held * DT == pytest.approx(_MARKER_POSITION_TTL_SEC, abs=DT + 1e-9)
    assert flight.odom.platform_report().along_track_m == pytest.approx(odometric)


# ═════════════════════════════════════════════════════════ floor reference and touchdown


def _visit_every_state(flight: Flight, axis: str = "y") -> Iterator[OdometrySnapshot]:
    for k, s in enumerate(round_trip()):
        z = TARGET + 0.05 * math.sin(k / 7.0)
        flight.at(s, axis, z=z, speed=0.2)
        yield flight.odom.snapshot()


def test_the_floor_reference_follows_the_pad_height_in_lateral_flight(clock):
    params = pad_params(0.30)
    flight = Flight(params, clock)
    assert params.relative_target_altitude_m == pytest.approx(TARGET_FLOOR_M - PAD_HEIGHT_M)
    assert lateral_governor(flight).target_altitude == pytest.approx(TARGET)

    seen = set()
    ctx = types.SimpleNamespace(frame_width=856, frame_height=480, params=params, current_tilt_deg=-20.0)
    target = types.SimpleNamespace(center=(500.0, 300.0), class_name="bicycle", confidence=0.9)
    for snap in _visit_every_state(flight):
        seen.add(snap.platform_state)
        assert snap.floor_offset_m == PAD_HEIGHT_M
        assert snap.height_above_floor_m == pytest.approx(snap.relative_altitude + PAD_HEIGHT_M)
        assert VisualServoingStep._height_above_target(snap) == pytest.approx(snap.height_above_floor_m)
        from_floor = OdometrySnapshot(
            x=0.0, y=0.0, raw_altitude=snap.height_above_floor_m,
            relative_altitude=snap.height_above_floor_m, vx=0.0, vy=0.0, vz=0.0, yaw=0.0,
            timestamp=0.0, sample_count=1, ground_reference=0.0, takeoff_x=0.0, takeoff_y=0.0,
        )
        assert ForwardSearchStep._locate(ctx, target, snap) == ForwardSearchStep._locate(
            ctx, target, from_floor
        )
    assert seen == {"on", "leaving", "off", "entering"}


def test_the_touchdown_threshold_depends_on_the_platform_state_only(monkeypatch, clock):
    params = pad_params(0.30)
    ctx = types.SimpleNamespace(params=params)
    touchdown = params.rtl.touchdown_altitude_m
    grounded = ClosedLoopRTLStep._grounded_altitude

    def thresholds(flight: Flight, axis: str) -> Dict[Tuple[str, bool], float]:
        out: Dict[Tuple[str, bool], float] = {}
        for snap in _visit_every_state(flight, axis):
            for at_base in (False, True):
                value = grounded(ctx, snap, at_base)
                assert out.setdefault((snap.platform_state, at_base), value) == value
        return out

    lateral = thresholds(Flight(params, clock), "y")
    with monkeypatch.context() as patch:
        patch.setattr(odometry_module, "outbound_track_m", frontal_track_m)
        forward = thresholds(Flight(params, clock), "x")

    assert lateral == forward
    assert lateral[("off", False)] == pytest.approx(touchdown - PAD_HEIGHT_M)
    assert lateral[("off", True)] == touchdown
    for state in ("on", "leaving", "entering"):
        assert lateral[(state, False)] == touchdown
        assert lateral[(state, True)] == touchdown


# ═════════════════════════════════════════════════════════ departure settle


class PadPlant:
    """Integrates the transmitted Twist into the supervisor: ``vy`` moves left, ``vz`` climbs."""

    no_fly = True

    def __init__(self, flight: Flight, z: float) -> None:
        self.flight = flight
        self.y = 0.0
        self.z = z
        self.last = flight.clock.now
        self.commands: List[Tuple[float, float, float, float, PlatformState]] = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        clock = self.flight.clock
        dt = max(0.0, clock.now - self.last)
        self.last = clock.now
        self.y += vy * dt
        self.z += vz * dt
        self.flight.at(self.y, "y", z=self.z, speed=vy, advance=False)
        self.commands.append((clock.now, vx, vy, self.z, self.flight.state))

    def camera_control(self, tilt, pan=0.0):
        pass

    def land(self):
        return True


def test_departure_settle_precedes_the_lateral_edge_crossing(monkeypatch, clock):
    """Launch at the firmware's 1.0 m over the pad; no ``vy > 0`` until the error holds inside the departure band."""
    from test_stage_altitude_wiring import Ctx

    monkeypatch.setattr(
        search_module, "LoopRate", functools.partial(LoopRate, clock=clock.monotonic, sleeper=clock.sleep)
    )
    monkeypatch.setattr(search_module, "Deadline", functools.partial(Deadline, clock=clock.monotonic))

    ctx = Ctx(detections=False)
    params = ctx.params
    params.kinematics.target_altitude_m = TARGET_FLOOR_M
    params.kinematics.altitude_ceiling_margin_m = CEILING_MARGIN_M
    params.kinematics.control_loop_hz = 30.0
    params.timeouts.search_timeout_sec = 20.0
    params.platform.height_m = PAD_HEIGHT_M
    params.platform.edge_ahead_m = 0.30
    params.platform.ceiling_allowance_m = ALLOWANCE_M
    params.platform.venue_ceiling_agl_m = VENUE_AGL_M
    flight = Flight(params, clock, origin=(0.0, 0.0))
    plant = PadPlant(flight, z=1.0)
    ctx.drone = plant
    ctx.odom_supervisor = flight.odom
    ctx.failsafe = FailsafeSupervisor(
        drone_actuator=plant,
        odom_supervisor=flight.odom,
        timeouts_cfg=params.timeouts,
        kinematics_cfg=params.kinematics,
    )
    ctx.governor = lateral_governor(flight)

    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS

    commands = plant.commands
    assert all(vx == 0.0 for _t, vx, _vy, _z, _s in commands)
    first = next(k for k, (_t, _vx, vy, _z, _s) in enumerate(commands) if vy > 0.0)
    released_at = commands[first][0]
    governor_cfg = params.governor
    band = max(governor_cfg.deadband_m, governor_cfg.climb_deadband_m, governor_cfg.horizontal_throttle_error_m)
    settle = [c for c in commands[:first] if c[0] > released_at - params.platform.departure_settle_window_sec]
    assert settle, "translated without settling"
    assert all(abs(z - TARGET) <= band for _t, _vx, _vy, z, _s in settle)
    assert all(state is ON for *_rest, state in commands[:first])
    assert commands[0][3] > TARGET + band, "the launch did not start above the relative target"

    states = compress([state for *_rest, state in commands])
    assert states == [ON, LEAVING, OFF]
    leaving = next(c for c in commands if c[4] is LEAVING)
    assert plant.y > window(params)["upper"]
    assert leaving[0] > released_at


# ═════════════════════════════════════════════════════════ Stage 3 hysteresis


NOISE_M = 0.012
AMPLITUDE_M = 0.15
PERIOD_SEC = 3.0
CYCLES = 5


def dither(centre: float, seed: int) -> Tuple[List[float], List[float]]:
    """A lateral servo hunting +/-0.15 m around ``centre``: true and measured positions."""
    rng = random.Random(seed)
    count = int(CYCLES * PERIOD_SEC / DT)
    true = [centre + AMPLITUDE_M * math.cos(2.0 * math.pi * k * DT / PERIOD_SEC) for k in range(count)]
    measured = [p + rng.uniform(-NOISE_M, NOISE_M) for p in true]
    return true, measured


def real_crossings(path: Sequence[float], high: float, low: float) -> int:
    """Passes of the true path through the hysteresis band ``[low, high]``."""
    region = 1 if path[0] > high else -1
    count = 0
    for p in path:
        side = 1 if p > high else (-1 if p < low else region)
        if side != region:
            count += 1
            region = side
    return count


def approach(flight: Flight, boundary: str, start: float) -> None:
    """Fly into position: out past the window and back for ``upper``, out for ``lower``."""
    s = 0.0
    if boundary == "upper":
        while s < FAR_M:
            s += STEP_M
            flight.at(s, "y", speed=0.2)
        assert flight.state is OFF
        while s > start + 1e-9:
            s -= STEP_M
            flight.at(s, "y", speed=-0.2)
        while flight.state is not OFF:
            s += STEP_M
            flight.at(s, "y", speed=0.2)
    else:
        while s < start - 1e-9:
            s += STEP_M
            flight.at(s, "y", speed=0.2)
        assert flight.state is LEAVING


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("boundary", ["upper", "lower"])
@pytest.mark.parametrize("edge", EDGES_M)
def test_stage_3_corrections_back_toward_the_pad_do_not_flap_the_guard(clock, edge, boundary, seed):
    """The servo hunts across a guard threshold: at most one transition per real crossing.

    Inside the hysteresis band (the lowest swing still above the threshold that
    re-enters the region) nothing changes at all. A swing that genuinely crosses
    it changes the state once per crossing, never on the odometric jitter,
    which is kept below half of ``HYSTERESIS_M``.
    """
    assert 2.0 * NOISE_M < HYSTERESIS_M
    params = pad_params(edge)
    bounds = window(params)
    high = bounds[boundary]
    low = high - HYSTERESIS_M

    inside = Flight(params, clock)
    centre = low + 0.02 + AMPLITUDE_M + NOISE_M
    approach(inside, boundary, centre + AMPLITUDE_M)
    held = inside.state
    true, measured = dither(centre, seed)
    for p in measured:
        inside.at(p, "y")
        assert inside.state is held, f"flapped inside the hysteresis band at s={p:.3f}"

    crossing = Flight(params, clock)
    centre = high - HYSTERESIS_M / 2.0
    approach(crossing, boundary, centre + AMPLITUDE_M)
    true, measured = dither(centre, seed)
    states = [crossing.state]
    for p in measured:
        crossing.at(p, "y")
        states.append(crossing.state)
    transitions = len(compress(states)) - 1
    crossings = real_crossings(true, high, low)
    assert crossings >= 2 * CYCLES - 1
    assert 1 <= transitions <= crossings


def test_a_reversal_shorter_than_the_confirmation_does_not_enter(clock):
    """A correction toward the pad lasting fewer than ``DIRECTION_CONFIRM_SAMPLES`` is not a return."""
    params = pad_params(0.30)
    upper = window(params)["upper"]
    flight = Flight(params, clock)
    approach(flight, "upper", upper + 0.20)
    s = upper + 0.02
    for k in range(5):
        flight.at(s + 0.03 * k, "y", speed=0.2)
    assert flight.odom.platform_report().direction == 1
    for _ in range(DIRECTION_CONFIRM_SAMPLES - 1):
        flight.at(upper - 2.0 * HYSTERESIS_M, "y", speed=-0.2)
        assert flight.state is OFF
    flight.at(s + 0.15, "y", speed=0.2)
    assert flight.state is OFF
    assert flight.odom.platform_report().direction == 1
