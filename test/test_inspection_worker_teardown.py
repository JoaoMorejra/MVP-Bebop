"""Runner teardown of the inspection worker: bounded, and never ahead of a landing."""

from __future__ import annotations

import threading
import time

import numpy as np

from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.perception.assessment import REASON_TIMEOUT
from mvp_mission_bebop.perception.inspection_worker import InspectionInferenceWorker, InspectionJob

STAMP = "20261020_101500"


class _Detector:
    def __init__(self):
        self.gate = threading.Event()

    def load(self):
        return True

    def detect(self, _frame, **_kwargs):
        self.gate.wait(10.0)
        raise RuntimeError("released")


class _Blackboard:
    rtl_completed = True


class _Ctx:
    def __init__(self, worker):
        self.emergency_event = threading.Event()
        self.blackboard = _Blackboard()
        self.inspection_worker = worker


def _hung_worker():
    detector = _Detector()
    worker = InspectionInferenceWorker(lambda: detector, ())
    worker.start()
    worker.submit(InspectionJob.create(STAMP, np.zeros((480, 856, 3), dtype=np.uint8), None))
    return worker, detector


def test_a_landed_mission_waits_for_the_assessment_within_the_bound():
    worker, detector = _hung_worker()
    runner = MissionRunner(_Ctx(worker))
    try:
        started = time.monotonic()
        runner._stop_inspection_worker()
        elapsed = time.monotonic() - started
    finally:
        detector.gate.set()
    assert elapsed < 2.0
    assert worker.slot.get(STAMP).reason == REASON_TIMEOUT


def test_an_aborted_mission_does_not_wait_for_the_assessment():
    worker, detector = _hung_worker()
    ctx = _Ctx(worker)
    ctx.blackboard.rtl_completed = False
    runner = MissionRunner(ctx)
    try:
        started = time.monotonic()
        runner._stop_inspection_worker()
        elapsed = time.monotonic() - started
    finally:
        detector.gate.set()
    assert elapsed < 0.1
    assert worker.slot.get(STAMP).reason == REASON_TIMEOUT


def test_a_context_without_worker_is_a_no_op():
    ctx = _Ctx(None)
    MissionRunner(ctx)._stop_inspection_worker()
