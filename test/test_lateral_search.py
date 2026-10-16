"""Stage 2 cruises to the left: vx is structurally zero, vy is non-negative.

The tilted camera sees a target ahead of the drone at a fixed ground range, so
the search translates only along the body Y axis (+y is LEFT, REP-103) and the
forward channel is pinned at zero. These tests drive the real step over the
shared synchronous-perception context and assert on the transmitted Twists.
"""

from __future__ import annotations

import functools
from typing import List, Tuple

import pytest

from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.steps import search as search_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.search import ForwardSearchStep, LateralSearchStep
from test_stage_altitude_wiring import Ctx, DetectionResult, Detection

CRUISE_WINDOW_SEC = 3.0


class FakeTime:
    """Simulated monotonic clock; sleeping advances it, so loops are load-independent."""

    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def simulated_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(
        search_module,
        "LoopRate",
        functools.partial(LoopRate, clock=fake.clock, sleeper=fake.sleep),
    )
    monkeypatch.setattr(search_module, "Deadline", functools.partial(Deadline, clock=fake.clock))
    return fake


class RecordingGovernor:
    engaged = True
    climbing = False
    altitude_error_m = 0.0

    def __init__(self) -> None:
        self.demands: List[float] = []

    def compute_vz(self, _altitude, _dt=None, vx_commanded=0.0):
        self.demands.append(vx_commanded)
        return 0.0

    @staticmethod
    def horizontal_scale():
        return 1.0


def cruise_ctx(window: float = CRUISE_WINDOW_SEC, lateral_gain: float = 1.0) -> Ctx:
    ctx = Ctx(detections=False)
    ctx.params.timeouts.search_timeout_sec = window
    ctx.params.platform.edge_ahead_m = None
    ctx.governor = RecordingGovernor()
    ctx.speed_calibration = SpeedCalibration(
        normalized_to_mps=1.0, lateral_to_mps=lateral_gain
    )
    return ctx


def twists(ctx: Ctx) -> List[Tuple[float, float, float, float]]:
    return list(ctx.drone.commands)


def test_the_legacy_name_is_an_alias_of_the_lateral_step():
    assert ForwardSearchStep is LateralSearchStep
    assert LateralSearchStep().name == "STEP 2: Lateral Left Search"


def test_every_transmitted_twist_has_vx_zero_and_vy_nonnegative():
    ctx = cruise_ctx()

    assert LateralSearchStep().execute(ctx) is StepStatus.SUCCESS

    commands = twists(ctx)
    assert commands
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in commands)
    assert all(vy >= 0.0 for _vx, vy, _vz, _vyaw in commands)
    assert all(vyaw == 0.0 for _vx, _vy, _vz, vyaw in commands)
    assert any(vy > 0.0 for _vx, vy, _vz, _vyaw in commands)


def test_cruise_reaches_the_configured_setpoint():
    ctx = cruise_ctx()
    setpoint = ctx.params.kinematics.forward_cruise_velocity

    LateralSearchStep().execute(ctx)

    peak = max(vy for _vx, vy, _vz, _vyaw in twists(ctx))
    assert abs(peak - setpoint) <= ctx.params.rtl.quantization_step + 1e-9


def test_the_setpoint_is_independent_of_the_lateral_gain():
    gain = 2.0
    baseline = cruise_ctx()
    LateralSearchStep().execute(baseline)
    scaled = cruise_ctx(lateral_gain=gain)
    LateralSearchStep().execute(scaled)

    peak = lambda c: max(vy for _vx, vy, _vz, _vyaw in twists(c))
    step = baseline.params.rtl.quantization_step
    assert peak(scaled) == pytest.approx(peak(baseline), abs=step)

    setpoint = baseline.params.kinematics.forward_cruise_velocity
    mps = scaled.speed_calibration.to_mps(peak(scaled), axis="lateral")
    assert mps == pytest.approx(setpoint * gain, abs=step * gain)

    # The accel/jerk limits are in m/s: a 2x lateral gain doubles the m/s
    # cruise, so the profile needs about twice as many cycles to reach it.
    def cycles_to_peak(c: Ctx) -> int:
        vys = [vy for _vx, vy, _vz, _vyaw in twists(c)]
        return next(i for i, vy in enumerate(vys) if vy >= max(vys) - step)

    assert cycles_to_peak(scaled) > 1.5 * cycles_to_peak(baseline)


def test_the_governor_is_fed_the_lateral_demand():
    ctx = cruise_ctx()

    LateralSearchStep().execute(ctx)

    demands = ctx.governor.demands
    assert demands
    assert max(demands) > 0.0
    assert all(demand >= 0.0 for demand in demands)
    transmitted = [vy for _vx, vy, _vz, _vyaw in twists(ctx)]
    assert max(demands) == pytest.approx(max(transmitted), abs=1e-9)


def test_the_halt_ramps_vy_to_zero_without_touching_vx():
    ctx = cruise_ctx(window=5.0)
    seen = {"calls": 0}

    def detect(_frame, conf=0.5, imgsz=None):
        seen["calls"] += 1
        late = seen["calls"] > 400
        return DetectionResult([Detection()] if late else [])

    ctx.detect = detect

    assert LateralSearchStep().execute(ctx) is StepStatus.SUCCESS
    assert ctx.blackboard.target_confirmed is True

    commands = twists(ctx)
    vys = [vy for _vx, vy, _vz, _vyaw in commands]
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in commands)
    assert vys[-1] == 0.0
    peak_index = max(i for i, vy in enumerate(vys) if vy == max(vys))
    tail = vys[peak_index:]
    # The sigma-delta shaper dithers between grid levels and pulses at the
    # effective floor near rest, so monotonicity is asserted on block means.
    blocks = 8
    assert len(tail) >= blocks
    size = max(1, len(tail) // blocks)
    means = [sum(tail[k : k + size]) / len(tail[k : k + size]) for k in range(0, len(tail), size)]
    assert all(later <= earlier + 1e-9 for earlier, later in zip(means, means[1:]))


def test_a_target_closer_than_the_view_band_never_moves_the_drone_forward():
    ctx = cruise_ctx(window=0.4)
    ctx.blackboard.target_confirmed = True

    assert LateralSearchStep().execute(ctx) is StepStatus.SUCCESS

    assert ctx.blackboard.target_confirmed is False
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in twists(ctx))


def test_abort_and_health_failure_paths_are_unchanged():
    ctx = cruise_ctx(window=5.0)
    original = ctx.detect
    calls = {"n": 0}

    def detect(frame, conf=0.5, imgsz=None):
        calls["n"] += 1
        if calls["n"] == 200:
            ctx.emergency_event.set()
        return original(frame, conf=conf, imgsz=imgsz)

    ctx.detect = detect

    assert LateralSearchStep().execute(ctx) is StepStatus.ABORTED

    commands = twists(ctx)
    assert commands[-1][1] == 0.0
    assert all(vx == 0.0 for vx, _vy, _vz, _vyaw in commands)


def test_entry_asserts_the_search_tilt_before_any_translation():
    ctx = cruise_ctx()
    search_tilt = ctx.params.gimbal.search_tilt_deg
    ctx.current_tilt_deg = ctx.params.rtl.camera_tilt_deg
    events: List[str] = []
    camera_control = ctx.drone.camera_control
    move = ctx.drone.move_velocity

    def record_tilt(tilt, pan=0.0):
        events.append(f"tilt:{tilt}")
        camera_control(tilt, pan)

    def record_move(*args, **kwargs):
        events.append("move")
        return move(*args, **kwargs)

    ctx.drone.camera_control = record_tilt
    ctx.drone.move_velocity = record_move

    assert LateralSearchStep().execute(ctx) is StepStatus.SUCCESS

    assert events[0] == f"tilt:{search_tilt}"
    assert ctx.drone.tilts[0] == search_tilt
    assert ctx.current_tilt_deg == search_tilt
    assert "move" in events
