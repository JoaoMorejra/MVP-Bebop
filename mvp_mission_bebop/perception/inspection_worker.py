"""Background victim inference on the Stage 4 inspection frame.

The forensic report decides its victim line from an inference on the inspection
frame. That inference is not needed by the flight: the report is read out on
the ground, and between the capture and touchdown lie the rest of the hover,
the heading restore, the whole Stage 5 return and the landing -- seconds to
tens of seconds. :class:`InspectionInferenceWorker` runs it in that window, on
a thread of its own, and never makes the flight wait for it:

* :meth:`InspectionInferenceWorker.submit` is a ``put_nowait`` of a job whose
  frame was already copied; it never blocks on the detector;
* the queue holds one job and the newest wins: a second capture replaces a job
  not yet started, which is logged;
* the outcome is handed to a :class:`InspectionAssessmentSlot` and to each
  :class:`InspectionSink`, every sink isolated from the others;
* any failure -- no detector, a detector exception, a job still pending when
  the worker is stopped -- is delivered as an ``unavailable`` assessment, never
  as ``not_detected``.

Dedicated detector
------------------
The mission detector cannot be shared. The Ultralytics predictor is not
re-entrant, and the Stage 4 capture and the countdown warmup hold it exclusively
under the :class:`~mvp_mission_bebop.perception.worker.PerceptionWorker`
contract. The worker therefore builds its own instance, on first use and on its
own thread, from the factory the mission detector was built with (same weights,
same backend). It never reads the camera: the frame arrives in the job.

One job per flight, never a loop: YOLO without a rate limit has taken the
camera from 27 to 11 Hz on this station (``parameters.py``,
``vision.inference_min_period_sec``).

Nectar SDK review, performed before this module was written: the SDK has no
background inference outside the PySide6 ``DetectionWorker`` of
``nectar/interface/tabs/vision_tab.py`` (see :mod:`.worker`). ``Detector.detect``
and ``DetectionResult.filter_by_class`` are used as they are.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Final, List, Optional, Protocol, Sequence, Set

import numpy as np

from mvp_mission_bebop.perception.assessment import (
    DEFAULT_MIN_BOX_AREA_FRAC,
    DEFAULT_VICTIM_CONF,
    REASON_DETECTOR_ERROR,
    REASON_DETECTOR_UNAVAILABLE,
    REASON_TIMEOUT,
    InspectionAssessment,
    assess_detections,
    unavailable,
)
from mvp_mission_bebop.perception.worker import detector_kwargs, normalize_imgsz
from mvp_mission_bebop.telemetry.milestones import emit_milestone

logger = logging.getLogger("InspectionInference")

Clock = Callable[[], float]
UtcClock = Callable[[], str]
DetectorFactory = Callable[[], Any]

#: Milestone raised once the assessment of a capture is settled.
INSPECTION_MILESTONE: Final[str] = "mission.inspection_ready"

#: Sidecar key under which the assessment is merged into
#: ``accident_metadata_<ts>.json``.
SIDECAR_KEY: Final[str] = "inspection"

#: Topic of the assessment, relative to the driver namespace:
#: ``/bebop/inspection/assessment`` under the default ``bebop``.
ASSESSMENT_TOPIC_SUFFIX: Final[str] = "inspection/assessment"

#: Inference input size of the victim pass, in pixels. Larger than the flight
#: detector's 480: a person seen from above at inspection altitude is a small
#: object, and this pass has seconds, not a control period, to run.
DEFAULT_INSPECTION_IMGSZ: Final[int] = 640

#: Niceness added to the worker thread (Linux ``setpriority`` on its TID), so
#: the control loops win the CPU when both are runnable.
DEFAULT_NICENESS: Final[int] = 10

#: Queue poll period of the worker loop, in seconds. Bounds how long ``stop``
#: waits for an idle worker to notice the halt flag.
_POLL_SEC: Final[float] = 0.1


def assessment_topic(namespace: str) -> str:
    """Absolute assessment topic under the driver ``namespace``.

    Raises
    ------
    TypeError
        If ``namespace`` is not a string.
    ValueError
        If ``namespace`` is empty.
    """
    if not isinstance(namespace, str):
        raise TypeError(f"namespace must be a string, got {type(namespace).__name__}")
    stripped = namespace.strip().strip("/")
    if not stripped:
        raise ValueError("namespace must not be empty")
    return f"/{stripped}/{ASSESSMENT_TOPIC_SUFFIX}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, eq=False)
class InspectionJob:
    """One inspection frame awaiting victim inference.

    Attributes
    ----------
    capture_stamp : str
        ``%Y%m%d_%H%M%S`` timestamp of the capture.
    frame : np.ndarray
        Private contiguous copy of the BGR frame; the caller's buffer may be
        reused or modified after submission.
    metadata_path : Optional[str]
        Sidecar of the capture, or ``None`` when none was written.
    submitted_at : float
        Monotonic submission instant, in seconds.

    Jobs compare and hash by identity: two captures stamped within the same
    second are still two jobs.
    """

    capture_stamp: str
    frame: np.ndarray
    metadata_path: Optional[str]
    submitted_at: float

    @classmethod
    def create(
        cls,
        capture_stamp: str,
        frame: np.ndarray,
        metadata_path: Optional[str] = None,
        *,
        clock: Clock = time.monotonic,
    ) -> "InspectionJob":
        """Build a job holding its own copy of ``frame``.

        Raises
        ------
        TypeError
            If ``capture_stamp`` is not a string, ``frame`` is not an array or
            ``metadata_path`` is neither a string nor ``None``.
        ValueError
            If ``capture_stamp`` is empty or ``frame`` is not an image.
        """
        if not isinstance(capture_stamp, str):
            raise TypeError(f"capture_stamp must be a string, got {type(capture_stamp).__name__}")
        if not capture_stamp.strip():
            raise ValueError("capture_stamp must not be empty")
        if not isinstance(frame, np.ndarray):
            raise TypeError(f"frame must be a numpy array, got {type(frame).__name__}")
        if frame.ndim not in (2, 3) or frame.shape[0] < 1 or frame.shape[1] < 1:
            raise ValueError(f"frame must be an image, got shape {frame.shape}")
        if metadata_path is not None and not isinstance(metadata_path, str):
            raise TypeError(f"metadata_path must be a string or None, got {type(metadata_path).__name__}")
        return cls(
            capture_stamp=capture_stamp,
            frame=np.ascontiguousarray(frame).copy(),
            metadata_path=metadata_path,
            submitted_at=float(clock()),
        )


class InspectionAssessmentSlot:
    """Thread-safe holder of the latest assessment, keyed by capture stamp."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: Optional[InspectionAssessment] = None

    def publish(self, assessment: InspectionAssessment) -> None:
        """Store ``assessment`` as the latest and wake every waiter.

        Raises
        ------
        TypeError
            If ``assessment`` is not an :class:`InspectionAssessment`.
        """
        if not isinstance(assessment, InspectionAssessment):
            raise TypeError(f"expected an InspectionAssessment, got {type(assessment).__name__}")
        with self._condition:
            self._latest = assessment
            self._condition.notify_all()

    @property
    def latest(self) -> Optional[InspectionAssessment]:
        """The latest assessment of any capture, or ``None``."""
        with self._condition:
            return self._latest

    def get(self, capture_stamp: str) -> Optional[InspectionAssessment]:
        """The latest assessment if it belongs to ``capture_stamp``, else ``None``."""
        with self._condition:
            latest = self._latest
        if latest is not None and latest.capture_stamp == capture_stamp:
            return latest
        return None

    def wait(self, capture_stamp: str, timeout_sec: float) -> Optional[InspectionAssessment]:
        """Wait up to ``timeout_sec`` for the assessment of ``capture_stamp``.

        Never call from a control loop: it blocks.

        Raises
        ------
        ValueError
            If ``timeout_sec`` is negative or not finite.
        """
        timeout = float(timeout_sec)
        if not timeout >= 0.0 or timeout == float("inf"):
            raise ValueError(f"timeout_sec must be finite and non-negative, got {timeout_sec!r}")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                latest = self._latest
                if latest is not None and latest.capture_stamp == capture_stamp:
                    return latest
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return None
                self._condition.wait(remaining)


class InspectionSink(Protocol):
    """Consumer of a settled assessment."""

    def deliver(self, assessment: InspectionAssessment, job: InspectionJob) -> None:
        """Hand ``assessment`` of ``job`` on. May raise; the worker isolates it."""
        ...


class SidecarSink:
    """Merges the assessment into the capture's sidecar under :data:`SIDECAR_KEY`.

    Parameters
    ----------
    update : Callable[[str, Dict[str, Any]], None]
        Atomic sidecar merge, ``MissionContext.update_evidence_metadata``.
    """

    def __init__(self, update: Callable[[str, Dict[str, Any]], None]) -> None:
        if not callable(update):
            raise TypeError("update must be callable")
        self._update = update

    def deliver(self, assessment: InspectionAssessment, job: InspectionJob) -> None:
        if job.metadata_path is None:
            logger.info("No sidecar for capture %s; assessment not persisted.", job.capture_stamp)
            return
        self._update(job.metadata_path, {SIDECAR_KEY: assessment.to_payload()})


class MilestoneSink:
    """Raises :data:`INSPECTION_MILESTONE` on stdout for the station.

    Parameters
    ----------
    emit : Callable
        Milestone emitter, :func:`~mvp_mission_bebop.telemetry.milestones.emit_milestone`.
    """

    def __init__(self, emit: Callable[[str, Dict[str, Any]], None] = emit_milestone) -> None:
        if not callable(emit):
            raise TypeError("emit must be callable")
        self._emit = emit

    def deliver(self, assessment: InspectionAssessment, job: InspectionJob) -> None:
        self._emit(INSPECTION_MILESTONE, assessment.to_payload())


class RosTopicSink:
    """Publishes the assessment as ``std_msgs/String`` JSON.

    Reliable and transient-local at depth 1, so a bridge that subscribes after
    the publication still receives the assessment of the flight.

    Parameters
    ----------
    node : rclpy.node.Node
        Node the publisher is created on.
    topic : str
        Absolute topic, :func:`assessment_topic`.
    """

    def __init__(self, node: Any, topic: str) -> None:
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String

        if not isinstance(topic, str) or not topic.startswith("/"):
            raise ValueError(f"topic must be an absolute ROS name, got {topic!r}")
        self._message = String
        self.topic = topic
        self._publisher = node.create_publisher(
            String,
            topic,
            QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            ),
        )

    def deliver(self, assessment: InspectionAssessment, job: InspectionJob) -> None:
        body = json.dumps(assessment.to_payload(), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        self._publisher.publish(self._message(data=body))


class InspectionInferenceWorker(threading.Thread):
    """Daemon thread running the victim inference of submitted inspection frames.

    Parameters
    ----------
    detector_factory : Callable[[], Any]
        Builds an unloaded SDK-compatible detector. Called once, on the worker
        thread, at the first job; ``load`` is called on its result.
    sinks : Sequence[InspectionSink]
        Consumers of every settled assessment, called in order.
    slot : Optional[InspectionAssessmentSlot]
        Cache of the latest assessment; a private one by default.
    model : str
        Weights label recorded in the assessment.
    imgsz : Optional[int]
        Inference input size, normalized with
        :func:`~mvp_mission_bebop.perception.worker.normalize_imgsz`.
    conf_threshold : float
        Victim confidence threshold, also passed to the detector.
    min_box_area_frac : float
        Minimum victim box area as a fraction of the frame.
    niceness : int
        Added to the thread's niceness on start; 0 leaves it unchanged.
    clock : Callable[[], float]
        Monotonic clock, in seconds.
    utc_clock : Callable[[], str]
        ISO 8601 UTC clock for ``completed_at_utc``.

    Raises
    ------
    TypeError, ValueError
        On an invalid argument.
    """

    def __init__(
        self,
        detector_factory: DetectorFactory,
        sinks: Sequence[InspectionSink] = (),
        *,
        slot: Optional[InspectionAssessmentSlot] = None,
        model: str = "",
        imgsz: Optional[int] = DEFAULT_INSPECTION_IMGSZ,
        conf_threshold: float = DEFAULT_VICTIM_CONF,
        min_box_area_frac: float = DEFAULT_MIN_BOX_AREA_FRAC,
        niceness: int = DEFAULT_NICENESS,
        clock: Clock = time.monotonic,
        utc_clock: UtcClock = _utc_now,
    ) -> None:
        super().__init__(name="InspectionInference", daemon=True)
        if not callable(detector_factory):
            raise TypeError("detector_factory must be callable")
        for sink in sinks:
            if not callable(getattr(sink, "deliver", None)):
                raise TypeError(f"sink {sink!r} has no deliver()")
        if isinstance(niceness, bool) or not isinstance(niceness, int) or niceness < 0:
            raise ValueError(f"niceness must be a non-negative integer, got {niceness!r}")
        self._factory = detector_factory
        self._sinks: List[InspectionSink] = list(sinks)
        self.slot = slot if slot is not None else InspectionAssessmentSlot()
        self._model = model
        self._imgsz = normalize_imgsz(imgsz)
        self._conf = float(conf_threshold)
        self._area_frac = float(min_box_area_frac)
        self._detect_kwargs = detector_kwargs(self._conf, self._imgsz)
        self._niceness = niceness
        self._clock = clock
        self._utc = utc_clock

        self._queue: "queue.Queue[InspectionJob]" = queue.Queue(maxsize=1)
        self._halt = threading.Event()
        self._idle = threading.Condition()
        self._outstanding = 0
        self._current: Optional[InspectionJob] = None
        self._settled: Set[InspectionJob] = set()
        self._detector: Optional[Any] = None
        self._detector_error: Optional[BaseException] = None

    # ------------------------------------------------------------ public API

    def submit(self, job: InspectionJob) -> bool:
        """Queue ``job`` without blocking; a job not yet started is replaced.

        Returns
        -------
        bool
            ``True`` if the job was queued, ``False`` once the worker is stopped.

        Raises
        ------
        TypeError
            If ``job`` is not an :class:`InspectionJob`.
        """
        if not isinstance(job, InspectionJob):
            raise TypeError(f"expected an InspectionJob, got {type(job).__name__}")
        if self._halt.is_set():
            logger.warning("Inspection worker stopped; capture %s not assessed.", job.capture_stamp)
            return False
        with self._idle:
            try:
                replaced = self._queue.get_nowait()
            except queue.Empty:
                self._outstanding += 1
            else:
                logger.warning(
                    "Inspection job %s replaced by %s before it started.",
                    replaced.capture_stamp,
                    job.capture_stamp,
                )
            self._queue.put_nowait(job)
            self._idle.notify_all()
        return True

    def drain(self, timeout_sec: float) -> bool:
        """Wait up to ``timeout_sec`` for every submitted job to be settled.

        Returns
        -------
        bool
            ``True`` if nothing is pending.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        with self._idle:
            while self._outstanding > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._idle.wait(remaining)
        return True

    def stop(self, timeout_sec: float) -> bool:
        """Halt the thread, waiting at most ``timeout_sec`` for it to exit.

        Jobs still pending when the wait runs out are settled as
        ``unavailable`` with reason ``timeout``; an inference that completes
        afterwards is discarded rather than delivered a second time.

        Returns
        -------
        bool
            ``True`` if the thread exited within the bound.
        """
        self._halt.set()
        with self._idle:
            self._idle.notify_all()
        if self.is_alive():
            self.join(max(0.0, float(timeout_sec)))
        pending: List[InspectionJob] = []
        with self._idle:
            if self._current is not None:
                pending.append(self._current)
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                pass
        for job in pending:
            self._settle(job, self._unavailable(job, REASON_TIMEOUT))
        return not self.is_alive()

    # ------------------------------------------------------------ thread body

    def run(self) -> None:
        self._lower_priority()
        while not self._halt.is_set():
            # Taken and marked current under one lock, so ``stop`` never sees a
            # job that is neither queued nor in flight.
            with self._idle:
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    self._idle.wait(_POLL_SEC)
                    continue
                self._current = job
            try:
                assessment = self._assess(job)
            except Exception as exc:  # noqa: BLE001 - any failure is an unavailable assessment
                logger.error("Victim inference failed for %s: %s", job.capture_stamp, exc)
                assessment = self._unavailable(job, REASON_DETECTOR_ERROR)
            self._settle(job, assessment)

    def _lower_priority(self) -> None:
        if not self._niceness:
            return
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), self._niceness)
        except (AttributeError, OSError) as exc:
            logger.warning("Inspection worker priority unchanged: %s", exc)

    def _ensure_detector(self) -> Optional[Any]:
        if self._detector is None and self._detector_error is None:
            started = self._clock()
            try:
                detector = self._factory()
                detector.load()
            except Exception as exc:  # noqa: BLE001 - recorded, every job then settles unavailable
                self._detector_error = exc
                logger.error("Inspection detector unavailable: %s", exc)
            else:
                self._detector = detector
                logger.info("Inspection detector loaded in %.0f ms.", (self._clock() - started) * 1000.0)
        return self._detector

    def _assess(self, job: InspectionJob) -> InspectionAssessment:
        detector = self._ensure_detector()
        if detector is None:
            return self._unavailable(job, REASON_DETECTOR_UNAVAILABLE)
        started = self._clock()
        try:
            result = detector.detect(job.frame, **self._detect_kwargs)
        except Exception as exc:  # noqa: BLE001 - a detector fault never reads as "no victim"
            elapsed_ms = (self._clock() - started) * 1000.0
            logger.error("Inspection detector raised on %s: %s", job.capture_stamp, exc)
            return self._unavailable(job, REASON_DETECTOR_ERROR, inference_ms=elapsed_ms)
        elapsed_ms = (self._clock() - started) * 1000.0
        return assess_detections(
            result,
            job.frame.shape,
            capture_stamp=job.capture_stamp,
            completed_at_utc=self._utc(),
            inference_ms=elapsed_ms,
            model=self._model,
            imgsz=self._imgsz,
            conf_threshold=self._conf,
            min_box_area_frac=self._area_frac,
        )

    def _unavailable(self, job: InspectionJob, reason: str, *, inference_ms: float = 0.0) -> InspectionAssessment:
        return unavailable(
            job.capture_stamp,
            reason,
            job.frame.shape,
            completed_at_utc=self._utc(),
            inference_ms=inference_ms,
            model=self._model,
            imgsz=self._imgsz,
            conf=self._conf,
        )

    def _settle(self, job: InspectionJob, assessment: InspectionAssessment) -> None:
        """Deliver once per job, then release a ``drain`` waiter."""
        with self._idle:
            first = job not in self._settled
            self._settled.add(job)
        try:
            if first:
                self._deliver(assessment, job)
            else:
                logger.info("Assessment of %s already settled; late result discarded.", job.capture_stamp)
        finally:
            with self._idle:
                if self._current is job:
                    self._current = None
                if first and self._outstanding > 0:
                    self._outstanding -= 1
                self._idle.notify_all()

    def _deliver(self, assessment: InspectionAssessment, job: InspectionJob) -> None:
        self.slot.publish(assessment)
        logger.info(
            "Inspection assessment %s: %s (%s, %.0f ms after submission).",
            job.capture_stamp,
            assessment.victim_status.value,
            assessment.reason or f"{assessment.victim_count} victim box(es)",
            (self._clock() - job.submitted_at) * 1000.0,
        )
        for sink in self._sinks:
            try:
                sink.deliver(assessment, job)
            except Exception as exc:  # noqa: BLE001 - one sink failing never silences the others
                logger.error("Inspection sink %s failed: %s", type(sink).__name__, exc)
