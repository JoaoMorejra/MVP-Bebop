"""Stage 1 timing: the countdown is the warmup window, and the takeoff is on its zero.

Drives :meth:`TakeoffStep.execute` down the real-flight path with a real
:class:`PerceptionWorker` whose detector blocks, a flat trim the aircraft
acknowledges after a delay, ground odometry for the reference, and a flying
state that confirms the takeoff at once. The run stops at the hand-over to the
stabilization, so what is measured is the ground sequence, the countdown and
the gap between the countdown's zero and the takeoff command.

Run with ``-s`` to print the intervals (``[TIMING]`` lines).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from types import SimpleNamespace
from typing import Dict, List, Optional

import numpy as np

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import PerceptionWorker
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.flat_trim_ack import FlatTrimAckTracker
from mvp_mission_bebop.telemetry.gimbal import GimbalStateMonitor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

FRAME = np.zeros((48, 64, 3), dtype=np.uint8)

#: How long the simulated aircraft takes to acknowledge the flat trim, seconds.
FLAT_TRIM_ACK_SEC = 0.3


class _StepLog(logging.Handler):
    """Stage 1 log lines; the mission's logging setup does not propagate them to pytest."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def captured_step_log():
    handler = _StepLog()
    target = logging.getLogger("Step1Takeoff")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


class SleepingDrone:
    """Real-flight drone stand-in whose ``delay`` sleeps, as ``BebopDrone.delay`` does."""

    no_fly = False

    def __init__(self, ack: FlatTrimAckTracker, ack_after_sec: float, marks: Dict[str, float]) -> None:
        self._ack = ack
        self._ack_after = ack_after_sec
        self._marks = marks
        self.calls: List[str] = []
        # Stage 1 refuses a flight whose gimbal does not move; this one follows its commands.
        self.gimbal = GimbalStateMonitor()
        self.gimbal.orientation_callback(SimpleNamespace(x=-80.0, y=0.0))

    def camera_control(self, tilt, pan=0.0):
        self.calls.append("camera_control")
        self.gimbal.orientation_callback(SimpleNamespace(x=tilt, y=pan))

    def flat_trim(self):
        self.calls.append("flat_trim")

        def acknowledge():
            self._marks["flat_trim_ack"] = time.monotonic()
            self._ack.update((self._ack.sequence or 0) + 1)

        threading.Timer(self._ack_after, acknowledge).start()

    def delay(self, seconds):
        self.calls.append("delay")
        time.sleep(seconds)

    def takeoff(self, altitude):
        self.calls.append("takeoff")
        self._marks["takeoff_published"] = time.monotonic()
        return True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.calls.append("move_velocity")

    def land(self):
        self.calls.append("land")


class BlockingDetector:
    """Camera and detector surface for the worker; every inference blocks."""

    def __init__(self, inference_sec: float) -> None:
        self.detector = self
        self.inference_sec = inference_sec
        self.inferences = 0

    def grab_frame(self, timeout_sec=1.0):
        return FRAME

    def detect(self, _frame, **_kwargs):
        self.inferences += 1
        time.sleep(self.inference_sec)
        return SimpleNamespace(detections=[])

    def publish_detection_summary(self, sample, text):
        return None

    def publish_annotated_stream(self, frame, result, text):
        return frame


class StubFailsafe:
    def notify_frame_received(self):
        return None


def _bench_context(countdown_sec: float, inference_sec: float, marks: Dict[str, float]):
    params = MissionParameters()
    params.no_fly = False
    params.kinematics.countdown_sec = countdown_sec
    odometry = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)
    for _ in range(params.calibration.min_ground_samples + 2):
        odometry.inject_synthetic_sample(x=0.0, y=0.0, z=0.0)
    ack = FlatTrimAckTracker()
    ack.update(0)
    camera = BlockingDetector(inference_sec)
    worker = PerceptionWorker(camera, frame_timeout_sec=0.05)
    worker.start()
    drone = SleepingDrone(ack, FLAT_TRIM_ACK_SEC, marks)
    ctx = SimpleNamespace(
        params=params,
        drone=drone,
        gimbal_monitor=drone.gimbal,
        failsafe=StubFailsafe(),
        odom_supervisor=odometry,
        flat_trim_ack=ack,
        perception=worker,
        blackboard=SimpleNamespace(),
        current_tilt_deg=0.0,
        interrupted=lambda: False,
        grab_frame=lambda timeout_sec=1.0: FRAME,
        flying_state=2,
        flying_state_timestamp=float("inf"),
    )
    return ctx, worker


def run_stage1(monkeypatch, countdown_sec: float, inference_sec: float) -> Dict[str, float]:
    """Run Stage 1 up to the stabilization and return its instants, in seconds from the start."""
    marks: Dict[str, float] = {}
    monkeypatch.setattr(TakeoffStep, "_announce", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("mvp_mission_bebop.steps.takeoff.emit_milestone", lambda key, payload=None: None)

    original_calibrate = TakeoffStep._calibrate
    original_countdown = TakeoffStep._countdown

    def calibrate(self, ctx):
        marks["calibration_start"] = time.monotonic()
        marks["inferences_before_calibration"] = float(ctx.perception._ctx.inferences)
        result = original_calibrate(self, ctx)
        marks["calibration_end"] = time.monotonic()
        return result

    def countdown(self, ctx):
        result = original_countdown(self, ctx)
        marks["countdown_return"] = time.monotonic()
        return result

    def stabilize(self, ctx):
        marks["stabilize"] = time.monotonic()
        marks["engaged_at_stabilize"] = float(ctx.perception.engaged)
        return StepStatus.ABORTED

    monkeypatch.setattr(TakeoffStep, "_calibrate", calibrate)
    monkeypatch.setattr(TakeoffStep, "_countdown", countdown)
    monkeypatch.setattr(TakeoffStep, "_stabilize", stabilize)

    ctx, worker = _bench_context(countdown_sec, inference_sec, marks)
    try:
        started = time.monotonic()
        status = TakeoffStep().execute(ctx)
    finally:
        worker.stop(timeout_sec=5.0)
    assert status is StepStatus.ABORTED

    bb = ctx.blackboard
    zero: Optional[float] = getattr(bb, "t_countdown_zero", None)
    marks["countdown_zero"] = zero if zero is not None else marks["countdown_return"]
    marks["takeoff_cmd"] = bb.t_takeoff_cmd
    counts = ("engaged_at_stabilize", "inferences_before_calibration")
    timings = {key: value - started for key, value in marks.items() if key not in counts}
    for key in counts:
        timings[key] = marks[key]
    timings["inferences"] = float(ctx.perception._ctx.inferences)
    timings["zero_to_takeoff_cmd"] = marks["takeoff_cmd"] - marks["countdown_zero"]
    print(
        "[TIMING] countdown_sec=%.1f inference_sec=%.2f %s"
        % (countdown_sec, inference_sec, " ".join(f"{k}={v:.3f}" for k, v in sorted(timings.items())))
    )
    return timings


def test_a_cold_countdown_counts_from_the_start_of_stage1_not_after_the_ground_sequence(monkeypatch):
    timings = run_stage1(monkeypatch, countdown_sec=3.0, inference_sec=0.05)
    assert 2.95 <= timings["takeoff_cmd"] < 3.3, timings


def test_the_takeoff_is_commanded_on_the_zero_even_with_a_hung_detector(monkeypatch):
    timings = run_stage1(monkeypatch, countdown_sec=3.0, inference_sec=2.0)
    assert timings["zero_to_takeoff_cmd"] < 0.100, timings
    assert timings["engaged_at_stabilize"] == 0.0, "the detector must be released before the confirmation"


def test_the_detector_warms_up_during_the_flat_trim_and_the_calibration(monkeypatch):
    timings = run_stage1(monkeypatch, countdown_sec=3.0, inference_sec=0.05)
    assert timings["inferences_before_calibration"] > 0, timings
    # The flat trim acknowledgement arrives FLAT_TRIM_ACK_SEC in; with the
    # settle no longer blocking, the ground reference follows it at once.
    assert timings["calibration_start"] < FLAT_TRIM_ACK_SEC + 0.3, timings


def test_a_ground_sequence_longer_than_the_countdown_delays_the_takeoff_and_skips_nothing(monkeypatch):
    with captured_step_log() as caplog:
        timings = run_stage1(monkeypatch, countdown_sec=1.0, inference_sec=0.05)
    # Flat trim acknowledged at 0.3 s plus the 2.0 s IMU settle: the takeoff
    # waits for the settle, never for less.
    assert timings["takeoff_cmd"] >= FLAT_TRIM_ACK_SEC + 2.0 - 0.02, timings
    assert "ground_sequence_overrun_sec=" in caplog.text
    assert "[TIMING] ground_sequence_sec=" in caplog.text


def test_a_zero_countdown_still_waits_for_the_imu_settle(monkeypatch):
    timings = run_stage1(monkeypatch, countdown_sec=0.0, inference_sec=0.05)
    assert timings["takeoff_cmd"] >= FLAT_TRIM_ACK_SEC + 2.0 - 0.02, timings


def test_the_bench_takes_off_without_an_imu_settle(monkeypatch):
    """``--no-fly`` never paid the settle: the proxy's ``delay`` was a no-op there."""
    monkeypatch.setattr(SleepingDrone, "no_fly", True)
    timings = run_stage1(monkeypatch, countdown_sec=0.0, inference_sec=0.05)
    assert timings["takeoff_cmd"] < FLAT_TRIM_ACK_SEC + 0.3, timings
