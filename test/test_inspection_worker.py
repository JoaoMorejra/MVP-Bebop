"""Background victim inference: the flight never waits for it, and it never lies.

The worker runs in the window between the Stage 4 capture and touchdown. The
properties pinned here are the two the design rests on: ``submit`` costs the
capture nothing measurable however slow the detector is, and every failure is
delivered as ``unavailable`` -- never as ``not_detected``, which the report
would read out as "no visible victim".
"""

from __future__ import annotations

import statistics
import threading
import time

import numpy as np
import pytest
from nectar.ai.detection.core.types import Detection, DetectionResult

from mvp_mission_bebop.perception.assessment import (
    REASON_DETECTOR_ERROR,
    REASON_DETECTOR_UNAVAILABLE,
    REASON_TIMEOUT,
    AssessmentStatus,
    VictimStatus,
)
from mvp_mission_bebop.perception.inspection_worker import (
    INSPECTION_MILESTONE,
    SIDECAR_KEY,
    InspectionAssessmentSlot,
    InspectionInferenceWorker,
    InspectionJob,
    MilestoneSink,
    SidecarSink,
    assessment_topic,
)

FRAME = np.zeros((480, 856, 3), dtype=np.uint8)
STAMP = "20261020_101500"


class FakeDetector:
    """Blocks ``delay_sec`` per call; reports a person where the frame is bright."""

    def __init__(self, delay_sec=0.0, fail=False):
        self.delay_sec = delay_sec
        self.fail = fail
        self.loaded = False
        self.calls = []
        self.gate = None

    def load(self):
        self.loaded = True
        return True

    def detect(self, frame, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.delay_sec:
            time.sleep(self.delay_sec)
        if self.fail:
            raise RuntimeError("CUDA error: device-side assert")
        detections = []
        if frame[200, 400, 0] > 0:
            detections.append(
                Detection(xyxy=np.array([380.0, 180.0, 460.0, 300.0]), confidence=0.8, class_id=0, class_name="person")
            )
        return DetectionResult(detections=detections)


class RecordingSink:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def deliver(self, assessment, job):
        self.calls.append((assessment, job))
        if self.fail:
            raise OSError("disk full")


def _worker(detector, sinks=(), **kwargs):
    worker = InspectionInferenceWorker(lambda: detector, sinks, model="yolov8n.pt", **kwargs)
    worker.start()
    return worker


def _median_ms(action, repeats=7):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000.0)
    # Median, not maximum: one sample can absorb a GIL hand-off to another
    # runnable thread (sys.getswitchinterval() is 5 ms).
    return statistics.median(samples)


def test_submit_returns_in_under_5_ms_while_the_detector_blocks_300_ms():
    detector = FakeDetector(delay_sec=0.3)
    worker = _worker(detector)
    try:
        assert worker.submit(InspectionJob.create(STAMP, FRAME, None)) is True
        jobs = [InspectionJob.create(STAMP, FRAME, None) for _ in range(7)]
        assert _median_ms(lambda: worker.submit(jobs.pop())) < 5.0

        assessment = worker.slot.wait(STAMP, 3.0)
        assert assessment is not None and assessment.status is AssessmentStatus.COMPLETE
    finally:
        worker.stop(2.0)


def test_job_creation_copies_the_frame_in_under_5_ms():
    assert _median_ms(lambda: InspectionJob.create(STAMP, FRAME, None)) < 5.0


def test_modifying_the_frame_after_submit_does_not_change_the_result():
    detector = FakeDetector()
    detector.gate = threading.Event()
    worker = _worker(detector)
    try:
        frame = FRAME.copy()
        worker.submit(InspectionJob.create(STAMP, frame, None))
        frame[:, :, :] = 255
        detector.gate.set()

        assessment = worker.slot.wait(STAMP, 3.0)
        assert assessment.victim_status is VictimStatus.NOT_DETECTED
    finally:
        worker.stop(2.0)


def test_a_detected_person_is_reported_with_the_inspection_settings():
    detector = FakeDetector()
    worker = _worker(detector)
    try:
        frame = FRAME.copy()
        frame[:, :, :] = 255
        worker.submit(InspectionJob.create(STAMP, frame, None))

        assessment = worker.slot.wait(STAMP, 3.0)
        assert assessment.victim_status is VictimStatus.DETECTED
        assert assessment.imgsz == 640
        assert assessment.model == "yolov8n.pt"
        assert detector.calls == [{"conf": 0.35, "imgsz": 640}]
    finally:
        worker.stop(2.0)


def test_a_detector_exception_is_unavailable_never_not_detected():
    worker = _worker(FakeDetector(fail=True))
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))

        assessment = worker.slot.wait(STAMP, 3.0)
        assert assessment.status is AssessmentStatus.UNAVAILABLE
        assert assessment.reason == REASON_DETECTOR_ERROR
        assert assessment.victim_status is VictimStatus.UNKNOWN
    finally:
        worker.stop(2.0)


def test_a_detector_that_cannot_be_built_is_unavailable():
    def factory():
        raise FileNotFoundError("OpenVINO IR not found")

    worker = InspectionInferenceWorker(factory, ())
    worker.start()
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))

        assessment = worker.slot.wait(STAMP, 3.0)
        assert assessment.reason == REASON_DETECTOR_UNAVAILABLE
        assert assessment.victim_status is VictimStatus.UNKNOWN
    finally:
        worker.stop(2.0)


def test_a_job_not_yet_started_is_replaced_by_the_newer_one():
    detector = FakeDetector()
    detector.gate = threading.Event()
    sink = RecordingSink()
    worker = _worker(detector, [sink])
    try:
        worker.submit(InspectionJob.create("20261020_101500", FRAME, None))
        deadline = time.monotonic() + 2.0
        while not detector.calls and time.monotonic() < deadline:
            time.sleep(0.005)
        worker.submit(InspectionJob.create("20261020_101501", FRAME, None))
        worker.submit(InspectionJob.create("20261020_101502", FRAME, None))
        detector.gate.set()

        assert worker.drain(3.0) is True
        assert [assessment.capture_stamp for assessment, _ in sink.calls] == [
            "20261020_101500",
            "20261020_101502",
        ]
    finally:
        worker.stop(2.0)


def test_drain_and_stop_are_bounded_and_settle_a_pending_job_as_timeout():
    detector = FakeDetector()
    detector.gate = threading.Event()
    sink = RecordingSink()
    worker = _worker(detector, [sink])
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))

        started = time.monotonic()
        assert worker.drain(0.2) is False
        assert worker.stop(0.2) is False
        assert time.monotonic() - started < 1.0

        assert len(sink.calls) == 1
        assessment = sink.calls[0][0]
        assert assessment.reason == REASON_TIMEOUT
        assert assessment.victim_status is VictimStatus.UNKNOWN
        assert worker.submit(InspectionJob.create("20261020_101501", FRAME, None)) is False
    finally:
        detector.gate.set()
        worker.join(2.0)

    assert len(sink.calls) == 1, "the late inference was delivered a second time"
    assert worker.slot.get(STAMP).reason == REASON_TIMEOUT


def test_stop_of_an_idle_worker_returns_promptly():
    worker = _worker(FakeDetector())
    started = time.monotonic()
    assert worker.stop(1.0) is True
    assert time.monotonic() - started < 0.5


def test_every_sink_is_called_once_and_one_failing_does_not_affect_the_others():
    first, broken, last = RecordingSink(), RecordingSink(fail=True), RecordingSink()
    worker = _worker(FakeDetector(), [first, broken, last])
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, "/tmp/accident_metadata_x.json"))
        assert worker.drain(3.0) is True
    finally:
        worker.stop(2.0)

    assert len(first.calls) == len(broken.calls) == len(last.calls) == 1
    assert last.calls[0][0].capture_stamp == STAMP


def test_sidecar_sink_merges_under_the_inspection_key():
    merged = []
    sink = SidecarSink(lambda path, fields: merged.append((path, fields)))
    worker = _worker(FakeDetector(), [sink])
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, "/out/accident_metadata_20261020_101500.json"))
        assert worker.drain(3.0) is True
    finally:
        worker.stop(2.0)

    assert len(merged) == 1
    path, fields = merged[0]
    assert path == "/out/accident_metadata_20261020_101500.json"
    assert fields[SIDECAR_KEY]["schema"] == "bmg.inspection.v1"
    assert fields[SIDECAR_KEY]["capture_stamp"] == STAMP


def test_sidecar_sink_skips_a_capture_without_sidecar():
    merged = []
    worker = _worker(FakeDetector(), [SidecarSink(lambda *args: merged.append(args))])
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))
        assert worker.drain(3.0) is True
    finally:
        worker.stop(2.0)
    assert merged == []


def test_milestone_sink_emits_the_inspection_ready_milestone():
    emitted = []
    worker = _worker(FakeDetector(), [MilestoneSink(lambda key, payload: emitted.append((key, payload)))])
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))
        assert worker.drain(3.0) is True
    finally:
        worker.stop(2.0)

    assert [key for key, _ in emitted] == [INSPECTION_MILESTONE]
    assert emitted[0][1]["victim_status"] == "not_detected"


def test_the_milestone_payload_is_accepted_by_the_encoder():
    from mvp_mission_bebop.telemetry.milestones import encode_milestone

    worker = _worker(FakeDetector())
    try:
        worker.submit(InspectionJob.create(STAMP, FRAME, None))
        assessment = worker.slot.wait(STAMP, 3.0)
    finally:
        worker.stop(2.0)

    line = encode_milestone(INSPECTION_MILESTONE, assessment.to_payload())
    assert line.startswith("[MILESTONE mission.inspection_ready] {")


def test_the_slot_returns_only_the_assessment_of_the_requested_capture():
    slot = InspectionAssessmentSlot()
    assert slot.get(STAMP) is None
    assert slot.wait(STAMP, 0.05) is None


def test_assessment_topic_follows_the_namespace():
    assert assessment_topic("bebop") == "/bebop/inspection/assessment"
    assert assessment_topic("/bebop/") == "/bebop/inspection/assessment"
    with pytest.raises(ValueError):
        assessment_topic("  ")
    with pytest.raises(TypeError):
        assessment_topic(None)


def test_invalid_jobs_are_refused():
    with pytest.raises(TypeError):
        InspectionJob.create(STAMP, [[0]], None)
    with pytest.raises(ValueError):
        InspectionJob.create("", FRAME, None)
    worker = InspectionInferenceWorker(lambda: FakeDetector(), ())
    with pytest.raises(TypeError):
        worker.submit("job")
