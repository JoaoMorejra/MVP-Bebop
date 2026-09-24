"""Latest-result perception: the slot, the worker thread, and the loops reading it.

The defect this module exists for was a timing one. Every closed-loop stage ran
YOLOv8n inline between two ``LoopRate.tick`` calls, so on the ground station's
CPU a 15 Hz loop ran at the inference rate, four to eight hertz, and every
guidance law integrated against that degraded period. The tests below pin the
three properties the fix depends on: the loop keeps its own cadence while
inference is slow, an observation is counted once however many cycles read it,
and a stage that disengages the pipeline really does get the camera back.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.worker import (
    LatestResultSlot,
    PerceptionSample,
    PerceptionWorker,
    SynchronousPerception,
)
from mvp_mission_bebop.steps import search as search_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.search import ForwardSearchStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

FRAME = np.zeros((480, 856, 3), dtype=np.uint8)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class Detection:
    def __init__(self, center=(428.0, 240.0), confidence=0.8):
        self.center = center
        self.confidence = confidence
        self.class_name = "bicycle"


class Result:
    def __init__(self, detections):
        self._detections = list(detections)

    def filter_by_class(self, _classes):
        return list(self._detections)


class PerceptionCtx:
    """The surface a perception pipeline touches: frames, detector, stream."""

    def __init__(self, *, inference_sec=0.0, detections=True, frame_period_sec=0.0):
        self.detector = self
        self.inference_sec = inference_sec
        self.frame_period_sec = frame_period_sec
        self.detections = detections
        self.grabs = 0
        self.detect_calls = []
        self.overlays = []
        self.summaries = []
        self.annotated_frames = 0
        self.fail_next = 0
        self._lock = threading.Lock()

    def grab_frame(self, timeout_sec=1.0):
        if self.frame_period_sec:
            time.sleep(self.frame_period_sec)
        with self._lock:
            self.grabs += 1
        return FRAME

    def detect(self, _frame, **kwargs):
        with self._lock:
            self.detect_calls.append(kwargs)
            failing = self.fail_next > 0
            if failing:
                self.fail_next -= 1
        if failing:
            raise RuntimeError("simulated detector fault")
        if self.inference_sec:
            time.sleep(self.inference_sec)
        return Result([Detection()] if self.detections else [])

    def publish_annotated_stream(self, frame, result, text):
        with self._lock:
            self.overlays.append(text)
        return frame

    def publish_detection_summary(self, sample, text):
        with self._lock:
            self.overlays.append(text)
            self.summaries.append(sample)


def wait_until(predicate, timeout_sec=2.0):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def worker_factory():
    workers = []

    def build(ctx, **kwargs):
        worker = PerceptionWorker(ctx, frame_timeout_sec=0.05, **kwargs)
        workers.append(worker)
        worker.start()
        return worker

    yield build
    for worker in workers:
        worker.stop()


# ------------------------------------------------------------------ the slot


def test_the_slot_assigns_strictly_increasing_generations():
    slot = LatestResultSlot(clock=Clock())
    first = slot.publish(FRAME, "a", stamp=100.0)
    second = slot.publish(FRAME, "b", stamp=100.1)

    assert (first.generation, second.generation) == (1, 2)
    assert slot.latest() is second
    assert slot.generation == 2


def test_a_stale_result_reads_as_no_observation():
    clock = Clock(100.0)
    slot = LatestResultSlot(clock=clock)
    slot.publish(FRAME, "a", stamp=100.0)

    clock.now = 100.4
    assert slot.get_latest(max_age_sec=0.5) is not None
    clock.now = 100.6
    assert slot.get_latest(max_age_sec=0.5) is None


def test_age_is_measured_from_acquisition_not_from_publication():
    sample = PerceptionSample(frame=FRAME, result=None, stamp=10.0, generation=1)
    assert sample.age_sec(10.25) == pytest.approx(0.25)
    assert sample.age_sec(9.0) == 0.0


def test_clearing_never_rewinds_the_generation():
    slot = LatestResultSlot(clock=Clock())
    slot.publish(FRAME, "a", stamp=100.0)
    slot.clear()

    assert slot.latest() is None
    assert slot.publish(FRAME, "b", stamp=100.0).generation == 2


def test_waiting_returns_only_something_newer():
    slot = LatestResultSlot()
    slot.publish(FRAME, "a", stamp=time.monotonic())
    assert slot.wait_newer(1, timeout_sec=0.02) is None

    threading.Timer(0.02, lambda: slot.publish(FRAME, "b", stamp=time.monotonic())).start()
    newer = slot.wait_newer(1, timeout_sec=1.0)
    assert newer is not None and newer.result == "b"


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_meaningless_staleness_bound_is_refused(bad):
    with pytest.raises(ValueError):
        LatestResultSlot().get_latest(bad)


@pytest.mark.parametrize("bad", ["0.5", None, True])
def test_a_non_numeric_staleness_bound_is_a_type_error(bad):
    with pytest.raises(TypeError):
        LatestResultSlot().get_latest(bad)


# ---------------------------------------------------------------- the worker


def test_an_idle_worker_never_touches_the_camera(worker_factory):
    ctx = PerceptionCtx()
    worker_factory(ctx)
    time.sleep(0.1)
    assert ctx.grabs == 0
    assert ctx.detect_calls == []


def test_an_engaged_worker_publishes_results_and_overlays(worker_factory):
    ctx = PerceptionCtx()
    worker = worker_factory(ctx)
    worker.set_status("STEP 2: SEARCH")
    worker.engage(conf=0.5)

    assert wait_until(lambda: worker.get_latest(1.0) is not None)
    assert wait_until(lambda: ctx.overlays)
    assert ctx.overlays[0] == "STEP 2: SEARCH"
    assert ctx.detect_calls[0] == {"conf": 0.5}


def test_the_worker_publishes_detections_not_images(worker_factory):
    """Phase 5: the frame is not re-encoded; the GCS bridge draws the boxes."""
    ctx = PerceptionCtx()
    original = ctx.publish_annotated_stream

    def counting(frame, result, text):
        ctx.annotated_frames += 1
        return original(frame, result, text)

    ctx.publish_annotated_stream = counting
    worker = worker_factory(ctx)
    worker.set_status("STEP 3: APPROACHING")
    worker.engage(conf=0.5)

    assert wait_until(lambda: len(ctx.summaries) >= 3)
    assert ctx.annotated_frames == 0
    generations = [sample.generation for sample in ctx.summaries[:3]]
    assert generations == sorted(set(generations)), "one summary per inference, in order"


def test_disengage_returns_the_camera_to_the_caller(worker_factory):
    """After disengage returns, no acquisition or inference may start."""
    ctx = PerceptionCtx(inference_sec=0.05)
    worker = worker_factory(ctx)
    worker.engage(conf=0.5)
    assert wait_until(lambda: ctx.grabs >= 2)

    assert worker.disengage(timeout_sec=1.0)
    grabs, detections = ctx.grabs, len(ctx.detect_calls)
    time.sleep(0.15)
    assert (ctx.grabs, len(ctx.detect_calls)) == (grabs, detections)


def test_a_result_finishing_after_disengage_is_discarded(worker_factory):
    """The next stage must not inherit the previous stage's last inference."""
    ctx = PerceptionCtx(inference_sec=0.15)
    worker = worker_factory(ctx)
    worker.engage(conf=0.5)
    assert wait_until(lambda: len(ctx.detect_calls) >= 1)

    assert worker.disengage(timeout_sec=1.0)
    assert worker.slot.latest() is None
    assert worker.cycles == 0


def test_engaging_clears_the_previous_stage_result(worker_factory):
    ctx = PerceptionCtx(inference_sec=0.1)
    worker = worker_factory(ctx)
    worker.engage(conf=0.5)
    assert wait_until(lambda: worker.get_latest(1.0) is not None)
    worker.disengage()
    assert worker.slot.latest() is not None

    worker.engage(conf=0.6)
    assert worker.slot.latest() is None


def test_input_size_is_forwarded_only_when_configured(worker_factory):
    """Omitting it keeps the call valid against a detector without the keyword."""
    ctx = PerceptionCtx()
    worker = worker_factory(ctx)
    worker.engage(conf=0.5, imgsz=480)
    assert wait_until(lambda: ctx.detect_calls)
    assert ctx.detect_calls[0] == {"conf": 0.5, "imgsz": 480}

    worker.disengage()
    ctx.detect_calls.clear()
    worker.engage(conf=0.5)
    assert wait_until(lambda: ctx.detect_calls)
    assert "imgsz" not in ctx.detect_calls[0]


@pytest.mark.parametrize("imgsz", [0, 479, -32])
def test_an_input_size_off_the_feature_stride_is_refused(worker_factory, imgsz):
    worker = worker_factory(PerceptionCtx())
    with pytest.raises(ValueError):
        worker.engage(conf=0.5, imgsz=imgsz)


@pytest.mark.parametrize("conf", [-0.1, 1.5])
def test_a_confidence_outside_the_unit_interval_is_refused(worker_factory, conf):
    worker = worker_factory(PerceptionCtx())
    with pytest.raises(ValueError):
        worker.engage(conf=conf)


def test_a_detector_fault_does_not_kill_the_worker(worker_factory):
    ctx = PerceptionCtx()
    ctx.fail_next = 3
    worker = worker_factory(ctx)
    worker.engage(conf=0.5)

    assert wait_until(lambda: worker.get_latest(1.0) is not None, timeout_sec=3.0)
    assert worker.is_alive()


def test_stop_joins_the_thread():
    worker = PerceptionWorker(PerceptionCtx(inference_sec=0.05), frame_timeout_sec=0.05)
    worker.start()
    worker.engage(conf=0.5)
    time.sleep(0.1)
    worker.stop(timeout_sec=2.0)
    assert not worker.is_alive()


# ------------------------------------------------------- synchronous stand-in


def test_the_synchronous_pipeline_is_inert_until_engaged():
    ctx = PerceptionCtx()
    pipeline = SynchronousPerception(ctx)
    assert pipeline.get_latest(0.5) is None
    assert ctx.grabs == 0


def test_the_synchronous_pipeline_publishes_one_overlay_per_frame():
    ctx = PerceptionCtx()
    pipeline = SynchronousPerception(ctx)
    with pipeline.session(conf=0.5):
        first = pipeline.get_latest(0.5)
        pipeline.set_status("one")
        pipeline.set_status("one again")
        second = pipeline.get_latest(0.5)
        pipeline.set_status("two")

    assert second.generation == first.generation + 1
    assert ctx.overlays == ["one", "two"]
    assert not pipeline.engaged


# --------------------------------------------------------- the search loop


class Drone:
    no_fly = True

    def __init__(self):
        self.commands = []
        self.stamps = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.commands.append((vx, vy, vz, vyaw))
        self.stamps.append(time.monotonic())


class Odometry:
    relative_altitude = 1.0
    speed = vx = vy = vz = 0.0
    x = y = 0.0

    def snapshot(self):
        return self

    @staticmethod
    def telemetry_health():
        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


class Governor:
    @staticmethod
    def compute_vz(_altitude, _dt=None, vx_commanded=0.0):
        return 0.0

    @staticmethod
    def horizontal_scale():
        return 1.0


class Blackboard:
    target_confirmed = False
    confirmed_tilt_deg = None
    confirmed_target_px = None


class SearchCtx(PerceptionCtx):
    """The attribute surface ``ForwardSearchStep`` touches, over real perception."""

    def __init__(self, *, search_sec, **kwargs):
        from mvp_mission_bebop.estimation.calibration import SpeedCalibration

        super().__init__(**kwargs)
        self.params = MissionParameters()
        self.params.timeouts.search_timeout_sec = search_sec
        self.drone = Drone()
        self.odom_supervisor = Odometry()
        self.governor = Governor()
        self.failsafe = FailsafeSupervisor(
            drone_actuator=self.drone,
            odom_supervisor=self.odom_supervisor,
            timeouts_cfg=self.params.timeouts,
            kinematics_cfg=self.params.kinematics,
        )
        self.speed_calibration = SpeedCalibration(1.0)
        self.blackboard = Blackboard()
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.current_tilt_deg = self.params.gimbal.search_tilt_deg
        self.perception = None

    def interrupted(self):
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()


class RecordingLoopRate(search_module.LoopRate):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        RecordingLoopRate.instances.append(self)


def test_slow_inference_no_longer_sets_the_control_cadence(monkeypatch, worker_factory):
    """200 ms inference against a 15 Hz loop: the loop must keep its own period.

    Inline, this configuration ran at five hertz and overran every tick. With
    the worker, ``tick`` measures only snapshot, control law and transmission.
    """
    RecordingLoopRate.instances.clear()
    monkeypatch.setattr(search_module, "LoopRate", RecordingLoopRate)
    ctx = SearchCtx(search_sec=1.2, inference_sec=0.2, detections=False, frame_period_sec=0.03)
    ctx.perception = worker_factory(ctx)

    started = time.monotonic()
    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS
    elapsed = time.monotonic() - started

    rate = RecordingLoopRate.instances[0]
    loop_hz = ctx.params.kinematics.control_loop_hz
    assert rate.ticks >= 0.8 * loop_hz * elapsed, (
        f"only {rate.ticks} control cycles in {elapsed:.2f} s"
    )
    assert rate.overruns <= 2, f"{rate.overruns} overruns: inference is still on the control path"
    inferences = len(ctx.detect_calls)
    assert inferences <= elapsed / ctx.inference_sec + 1, "inference ran faster than it can"
    assert rate.ticks >= 2 * inferences, "the loop is still paced by the detector"


def test_one_frame_read_many_times_is_counted_once(monkeypatch):
    """A single image must never satisfy a three-frame confirmation by itself."""
    ctx = SearchCtx(search_sec=0.4)
    ctx.params.kinematics.control_loop_hz = 100.0
    only = PerceptionSample(frame=FRAME, result=Result([Detection()]), stamp=0.0, generation=7)

    class Frozen(SynchronousPerception):
        def get_latest(self, max_age_sec):
            return only

    ctx.perception = Frozen(ctx)
    ForwardSearchStep().execute(ctx)

    assert ctx.blackboard.target_confirmed is False
    assert all(vx == 0.0 for vx, *_ in ctx.drone.commands), (
        "a visible candidate must keep the drone on station between frames"
    )


def test_three_distinct_frames_still_confirm():
    ctx = SearchCtx(search_sec=2.0)
    ctx.perception = SynchronousPerception(ctx)
    ForwardSearchStep().execute(ctx)
    assert ctx.blackboard.target_confirmed is True


def test_a_stale_pipeline_keeps_the_cruise_alive():
    """No usable observation is not a reason to stop commanding the airframe."""
    ctx = SearchCtx(search_sec=0.3)
    ctx.params.kinematics.control_loop_hz = 100.0

    class Silent(SynchronousPerception):
        def get_latest(self, max_age_sec):
            return None

    ctx.perception = Silent(ctx)
    ForwardSearchStep().execute(ctx)
    assert max(vx for vx, *_ in ctx.drone.commands) > 0.0
    assert ctx.blackboard.target_confirmed is False
