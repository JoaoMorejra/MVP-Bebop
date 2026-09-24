"""Latest-result perception: detector inference off the flight control thread.

Every closed-loop stage used to acquire a frame, run YOLOv8n and publish the
annotated overlay inline, between two ``LoopRate.tick`` calls. On the ground
station's i5-7200U that inference costs 150-250 ms, so a loop configured for
15 Hz ran at 4-8 Hz: every guidance law integrated against the degraded period,
the Bebop latched each velocity command for up to a quarter of a second, and the
GCS stream -- published from the same loop -- froze at the same cadence even
though the driver delivers frames every 30 ms.

:class:`PerceptionWorker` runs acquisition, inference and overlay publication on
its own thread and leaves the newest result in a :class:`LatestResultSlot`. A
control loop reads the slot, which costs a lock acquisition, and decides from the
sample's age and generation whether it holds fresh evidence, repeated evidence,
or none at all.

Nectar SDK review, performed before this module was written: ``Detector.detect``
(``nectar/ai/detection/detector.py``) is a synchronous call with no worker or
result cache. ``ROSCam`` (``nectar/vision/camera/drivers/ros_cam.py``) caches the
last frame behind a lock and an event, but its ``wait_for_new`` bookkeeping is a
single shared frame counter, so it supports exactly one consumer. The only
background inference pattern in the SDK is ``DetectionWorker`` in
``nectar/interface/tabs/vision_tab.py`` (with ``ModelLoadWorker`` in
``interface/widgets/detection_panel.py`` for model loading): both are PySide6
``QObject`` workers driven by queued Qt signals, with no staleness notion and a
hard dependency on a Qt event loop the mission process does not run.
``nectar/utils`` and ``nectar/vision/utils`` hold geometry and logging helpers
only. There is therefore no SDK abstraction to reuse, and this module is the
mission-side equivalent of the GUI worker.

Exclusive access contract
-------------------------
``ROSCam.get_frame(wait_for_new=True)`` tracks one ``_last_frame_count`` for all
callers, so two threads waiting for new frames steal them from one another, and
the Ultralytics predictor is not re-entrant. The worker therefore only runs while
*engaged*. :meth:`PerceptionPipeline.disengage` returns once the in-flight cycle
has finished, after which the caller owns both the camera and the detector --
which is what Stage 4's evidence capture and the Stage 5 marker search rely on.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Final, Iterator, Optional, Tuple, Union

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    from mvp_mission_bebop.context import MissionContext

logger = logging.getLogger("Perception")

Clock = Callable[[], float]

#: Frame acquisition timeout inside the worker loop, in seconds.
#:
#: Short on purpose. The loop re-checks the engage and halt flags between
#: acquisitions, so this bounds how long ``disengage`` and ``stop`` can be kept
#: waiting by a camera that has gone quiet. Six driver periods at 30 ms.
DEFAULT_FRAME_TIMEOUT_SEC: Final[float] = 0.2

#: Upper bound on how long ``disengage`` waits for the in-flight cycle.
#:
#: One worst-case CPU inference (250 ms) plus one frame acquisition timeout,
#: with a factor of four for a descheduled process. Exceeding it means the
#: detector itself is hung, and the caller is told rather than blocked forever.
DEFAULT_DISENGAGE_TIMEOUT_SEC: Final[float] = 2.0

#: How long ``stop`` waits for the thread to exit before giving up on the join.
DEFAULT_JOIN_TIMEOUT_SEC: Final[float] = 3.0

#: Back-off after an exception inside a worker cycle, in seconds. Keeps a
#: persistently failing detector from spinning a CPU core the control loops need.
_FAULT_BACKOFF_SEC: Final[float] = 0.1

#: Minimum interval between repeated fault log lines, in seconds.
_FAULT_LOG_INTERVAL_SEC: Final[float] = 2.0


@dataclass(frozen=True)
class PerceptionSample:
    """One frame and the detector's verdict on it.

    Attributes
    ----------
    frame : np.ndarray
        BGR frame the detector ran on.
    result : Any
        ``DetectionResult`` returned by the detector for ``frame``.
    stamp : float
        Monotonic time at which ``frame`` was acquired, in seconds. Age is
        measured from acquisition, not from the end of inference, because what
        matters to a guidance law is how old its view of the world is.
    generation : int
        Strictly increasing sequence number. Two reads returning the same
        generation are the same observation, and must not be counted twice by a
        frame-counting filter such as ``HysteresisConfirmer``.
    inference_sec : float
        Wall time the detector call took, in seconds.
    """

    frame: np.ndarray
    result: Any
    stamp: float
    generation: int
    inference_sec: float = 0.0

    def age_sec(self, now: float) -> float:
        """Seconds between acquisition and ``now``, floored at zero."""
        return max(0.0, now - self.stamp)


#: YOLOv8 feature-pyramid stride. Ultralytics silently rounds any other input
#: size up to a multiple of it, so the size that actually runs would differ from
#: the one configured.
IMGSZ_STRIDE: Final[int] = 32


def normalize_imgsz(value: Union[int, float, str, None]) -> Optional[int]:
    """Coerce a configured inference size to ``Optional[int]``.

    ``mission_config.json`` is written by the GCS, and a JavaScript number or a
    form field can arrive as ``480.0`` or ``"480"``. Both are accepted when they
    denote an integer; anything else is refused here, at configuration time,
    rather than inside a flight stage.

    Parameters
    ----------
    value : Union[int, float, str, None]
        Configured size. ``None`` or an empty string means the native size.

    Returns
    -------
    Optional[int]
        The size in pixels, or ``None``.

    Raises
    ------
    TypeError
        If ``value`` is not a number, a numeric string or ``None``.
    ValueError
        If ``value`` is not a positive integral multiple of ``IMGSZ_STRIDE``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("imgsz must be an integer, got bool")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"imgsz must be numeric, got {value!r}") from exc
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"imgsz must be an integer, got {value!r}")
        value = int(value)
    if not isinstance(value, int):
        raise TypeError(f"imgsz must be an integer, got {type(value).__name__}")
    if value <= 0 or value % IMGSZ_STRIDE != 0:
        raise ValueError(f"imgsz must be a positive multiple of {IMGSZ_STRIDE}, got {value!r}")
    return value


def detector_kwargs(conf: Optional[float], imgsz: Union[int, float, str, None]) -> Dict[str, Any]:
    """Keyword arguments for ``Detector.detect``, omitting every unset one.

    Omitting a keyword rather than passing ``None`` keeps the detector's own
    default in force and keeps the call valid against a Nectar build that
    predates the ``imgsz`` parameter on ``Detector.detect``.

    Parameters
    ----------
    conf : Optional[float]
        Confidence threshold, or ``None`` for the detector default.
    imgsz : Union[int, float, str, None]
        Inference input size, or ``None`` for the model's native size.
        Normalized with :func:`normalize_imgsz`.

    Returns
    -------
    Dict[str, Any]
        Mapping suitable for ``detector.detect(frame, **kwargs)``.

    Raises
    ------
    TypeError, ValueError
        On an invalid ``imgsz``.
    """
    kwargs: Dict[str, Any] = {}
    if conf is not None:
        kwargs["conf"] = conf
    size = normalize_imgsz(imgsz)
    if size is not None:
        kwargs["imgsz"] = size
    return kwargs


def _validate_age(max_age_sec: float) -> float:
    """Coerce and check a staleness bound.

    Raises
    ------
    TypeError
        If ``max_age_sec`` is not a real number.
    ValueError
        If ``max_age_sec`` is not finite and strictly positive.
    """
    if isinstance(max_age_sec, bool) or not isinstance(max_age_sec, (int, float)):
        raise TypeError(f"max_age_sec must be a real number, got {type(max_age_sec).__name__}")
    value = float(max_age_sec)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"max_age_sec must be finite and positive, got {max_age_sec!r}")
    return value


class LatestResultSlot:
    """Thread-safe single-entry store for the newest :class:`PerceptionSample`.

    A simplified, in-process counterpart of ``FrameBuffer`` in
    ``bebop_mission_control/streamer/mjpeg_server.py``: one writer replaces the
    entry, any number of readers observe the newest one, and a generation
    counter lets a reader block until something newer than what it already has
    arrives. Older entries are discarded, never queued -- a control loop has no
    use for a backlog of observations it is already too late to act on.

    Parameters
    ----------
    clock : Callable[[], float]
        Monotonic time source used for staleness. Injectable for tests.
    """

    __slots__ = ("_clock", "_condition", "_sample", "_generation")

    def __init__(self, *, clock: Clock = time.monotonic) -> None:
        self._clock: Clock = clock
        self._condition = threading.Condition()
        self._sample: Optional[PerceptionSample] = None
        self._generation: int = 0

    @property
    def generation(self) -> int:
        """Generation of the newest sample written, zero before the first."""
        with self._condition:
            return self._generation

    def publish(
        self,
        frame: np.ndarray,
        result: Any,
        stamp: float,
        inference_sec: float = 0.0,
    ) -> PerceptionSample:
        """Replace the entry and wake every waiting reader.

        Parameters
        ----------
        frame : np.ndarray
            Frame the detector ran on.
        result : Any
            Detector output for ``frame``.
        stamp : float
            Monotonic acquisition time of ``frame``.
        inference_sec : float
            Duration of the detector call.

        Returns
        -------
        PerceptionSample
            The sample as stored, carrying its assigned generation.
        """
        with self._condition:
            self._generation += 1
            sample = PerceptionSample(
                frame=frame,
                result=result,
                stamp=float(stamp),
                generation=self._generation,
                inference_sec=float(inference_sec),
            )
            self._sample = sample
            self._condition.notify_all()
        return sample

    def latest(self) -> Optional[PerceptionSample]:
        """Newest sample regardless of age, or ``None`` if empty."""
        with self._condition:
            return self._sample

    def get_latest(self, max_age_sec: float) -> Optional[PerceptionSample]:
        """Newest sample if it is younger than ``max_age_sec``, else ``None``.

        ``None`` means "no usable observation in this window". Callers treat it
        exactly as they treated a failed frame grab before inference moved off
        their thread, which is what the hysteresis and loss-tolerance filters
        downstream were written against.

        Raises
        ------
        TypeError, ValueError
            If ``max_age_sec`` is not a finite positive number.
        """
        bound = _validate_age(max_age_sec)
        with self._condition:
            sample = self._sample
        if sample is None or sample.age_sec(self._clock()) > bound:
            return None
        return sample

    def wait_newer(self, generation: int, timeout_sec: float) -> Optional[PerceptionSample]:
        """Block until a sample newer than ``generation`` exists, or time out.

        Returns
        -------
        Optional[PerceptionSample]
            The newer sample, or ``None`` if none arrived within ``timeout_sec``.
        """
        with self._condition:
            arrived = self._condition.wait_for(
                lambda: self._generation > generation, timeout=max(0.0, float(timeout_sec))
            )
            return self._sample if arrived else None

    def clear(self) -> None:
        """Drop the stored sample. The generation counter is never rewound."""
        with self._condition:
            self._sample = None


class PerceptionPipeline:
    """Interface shared by the threaded worker and its synchronous stand-in.

    A stage engages the pipeline with the confidence threshold it needs, reads
    samples with :meth:`get_latest`, keeps the overlay caption current with
    :meth:`set_status`, and disengages when it hands the camera back.

    Parameters
    ----------
    ctx : MissionContext
        Provides ``grab_frame`` and ``detector.detect``, plus
        ``publish_detection_summary`` (threaded worker) or
        ``publish_annotated_stream`` (inline pipeline). Any object with that
        surface works.
    clock : Callable[[], float]
        Monotonic time source. Injectable for tests.
    frame_timeout_sec : float
        Timeout handed to ``ctx.grab_frame`` on each acquisition.

    Raises
    ------
    ValueError
        If ``frame_timeout_sec`` is not finite and positive.
    """

    def __init__(
        self,
        ctx: "MissionContext",
        *,
        clock: Clock = time.monotonic,
        frame_timeout_sec: float = DEFAULT_FRAME_TIMEOUT_SEC,
    ) -> None:
        self._ctx = ctx
        self._clock: Clock = clock
        self._frame_timeout: float = _validate_age(frame_timeout_sec)
        self._slot = LatestResultSlot(clock=clock)
        self._settings_lock = threading.Lock()
        self._conf: Optional[float] = None
        self._imgsz: Optional[int] = None
        self._status: str = ""

    # --------------------------------------------------------------- settings

    @staticmethod
    def _validate_settings(
        conf: Optional[float], imgsz: Union[int, float, str, None]
    ) -> Tuple[Optional[float], Optional[int]]:
        """Check and normalize the per-stage detector settings.

        Raises
        ------
        TypeError, ValueError
            On a non-numeric or out-of-range ``conf`` or an invalid ``imgsz``.
        """
        if conf is not None:
            if isinstance(conf, bool) or not isinstance(conf, (int, float)):
                raise TypeError(f"conf must be a real number, got {type(conf).__name__}")
            if not 0.0 <= float(conf) <= 1.0:
                raise ValueError(f"conf must lie in [0, 1], got {conf!r}")
            conf = float(conf)
        return conf, normalize_imgsz(imgsz)

    @property
    def slot(self) -> LatestResultSlot:
        """The underlying result slot."""
        return self._slot

    @property
    def engaged(self) -> bool:
        """True while a stage owns the pipeline."""
        raise NotImplementedError

    def set_status(self, text: str) -> None:
        """Set the caption drawn on subsequently published overlays."""
        with self._settings_lock:
            self._status = str(text)

    def _snapshot_settings(self) -> Tuple[Optional[float], Optional[int], str]:
        with self._settings_lock:
            return self._conf, self._imgsz, self._status

    def _infer(self, frame: np.ndarray, conf: Optional[float], imgsz: Optional[int]) -> Any:
        """Run the detector with only the configured keywords."""
        return self._ctx.detector.detect(frame, **detector_kwargs(conf, imgsz))

    # -------------------------------------------------------------- lifecycle

    def engage(
        self, conf: Optional[float] = None, imgsz: Union[int, float, str, None] = None
    ) -> None:
        """Hand the camera and detector to the pipeline.

        The slot is cleared first: a result produced under the previous stage's
        gimbal attitude and confidence threshold is not evidence for this one.

        Parameters
        ----------
        conf : Optional[float]
            Detector confidence threshold, in ``[0, 1]``. ``None`` uses the
            detector's own default.
        imgsz : Union[int, float, str, None]
            Inference input size in pixels, a multiple of 32, normalized with
            :func:`normalize_imgsz`. ``None`` uses the model's native size.

        Raises
        ------
        TypeError, ValueError
            On an invalid ``conf`` or ``imgsz``.
        """
        raise NotImplementedError

    def disengage(self, timeout_sec: float = DEFAULT_DISENGAGE_TIMEOUT_SEC) -> bool:
        """Return the camera and detector to the caller.

        Returns
        -------
        bool
            True once no cycle is in flight, False if the in-flight cycle did
            not finish within ``timeout_sec``.
        """
        raise NotImplementedError

    @contextmanager
    def session(
        self, conf: Optional[float] = None, imgsz: Union[int, float, str, None] = None
    ) -> Iterator["PerceptionPipeline"]:
        """Engage for the duration of a ``with`` block, disengaging on any exit."""
        self.engage(conf=conf, imgsz=imgsz)
        try:
            yield self
        finally:
            self.disengage()

    def get_latest(self, max_age_sec: float) -> Optional[PerceptionSample]:
        """Newest sample younger than ``max_age_sec``, else ``None``.

        Raises
        ------
        TypeError, ValueError
            If ``max_age_sec`` is not a finite positive number.
        """
        raise NotImplementedError

    def start(self) -> None:
        """Begin operation. A no-op for pipelines without a thread."""

    def stop(self, timeout_sec: float = DEFAULT_JOIN_TIMEOUT_SEC) -> None:
        """End operation permanently. A no-op for pipelines without a thread."""


class PerceptionWorker(PerceptionPipeline, threading.Thread):
    """Background thread running acquisition, inference and overlay publication.

    Idle until :meth:`engage`; each engaged cycle then grabs a genuinely new
    frame through ``ctx.grab_frame``, runs the detector on it, stores the result
    in the slot and publishes it as a ``bmg.detections.v1`` overlay message with
    the caption last set by the owning stage. The frame itself is not
    republished: the GCS bridge draws the boxes over the raw camera stream,
    which keeps the cockpit video at the camera rate rather than the inference
    rate. The thread never commands the airframe and never touches
    mission state: its only outputs are the slot and the ROS image publisher,
    and ``rclpy`` publishers are safe to call from any thread under the shared
    ``MultiThreadedExecutor``.

    Parameters
    ----------
    ctx : MissionContext
        Source of frames, detector and stream publisher.
    clock : Callable[[], float]
        Monotonic time source. Injectable for tests.
    frame_timeout_sec : float
        Acquisition timeout per cycle.
    name : str
        Thread name, visible in stack dumps.
    """

    def __init__(
        self,
        ctx: "MissionContext",
        *,
        clock: Clock = time.monotonic,
        frame_timeout_sec: float = DEFAULT_FRAME_TIMEOUT_SEC,
        name: str = "mission_perception",
    ) -> None:
        PerceptionPipeline.__init__(self, ctx, clock=clock, frame_timeout_sec=frame_timeout_sec)
        threading.Thread.__init__(self, name=name, daemon=True)
        self._halt_event = threading.Event()
        self._engaged_event = threading.Event()
        # Held for the whole of one acquisition-inference-publication cycle.
        # ``disengage`` acquires it after clearing the engage flag, which is
        # what guarantees the camera and detector are free when it returns.
        self._cycle_lock = threading.Lock()
        self._cycles: int = 0
        self._last_fault_log: float = -math.inf

    @property
    def engaged(self) -> bool:
        return self._engaged_event.is_set()

    @property
    def cycles(self) -> int:
        """Completed inference cycles since start."""
        return self._cycles

    def engage(
        self, conf: Optional[float] = None, imgsz: Union[int, float, str, None] = None
    ) -> None:
        conf, size = self._validate_settings(conf, imgsz)
        with self._settings_lock:
            self._conf = conf
            self._imgsz = size
        self._slot.clear()
        self._engaged_event.set()

    def disengage(self, timeout_sec: float = DEFAULT_DISENGAGE_TIMEOUT_SEC) -> bool:
        self._engaged_event.clear()
        if threading.current_thread() is self:
            return True
        acquired = self._cycle_lock.acquire(timeout=max(0.0, float(timeout_sec)))
        if acquired:
            self._cycle_lock.release()
        else:
            logger.error(
                "Perception cycle still in flight %.1f s after disengage; the detector may be hung.",
                timeout_sec,
            )
        return acquired

    def get_latest(self, max_age_sec: float) -> Optional[PerceptionSample]:
        return self._slot.get_latest(max_age_sec)

    def start(self) -> None:
        """Start the thread.

        Defined explicitly because :class:`PerceptionPipeline` precedes
        ``threading.Thread`` in the MRO, and its no-op ``start`` would
        otherwise shadow the thread's.
        """
        threading.Thread.start(self)

    def stop(self, timeout_sec: float = DEFAULT_JOIN_TIMEOUT_SEC) -> None:
        """Halt the thread and join it, bounded by ``timeout_sec``."""
        self._halt_event.set()
        self._engaged_event.clear()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=max(0.0, float(timeout_sec)))
            if self.is_alive():
                logger.warning("Perception worker did not exit within %.1f s.", timeout_sec)

    def run(self) -> None:
        logger.info("Perception worker started.")
        while not self._halt_event.is_set():
            if not self._engaged_event.wait(timeout=self._frame_timeout):
                continue
            with self._cycle_lock:
                # Re-checked under the lock: a disengage that cleared the flag
                # between the wait above and the acquisition must win, or the
                # cycle would run against a camera the caller now owns.
                if not self._engaged_event.is_set() or self._halt_event.is_set():
                    continue
                try:
                    self._cycle()
                except Exception as exc:  # noqa: BLE001 - perception must not kill the mission
                    self._log_fault(exc)
                    self._halt_event.wait(_FAULT_BACKOFF_SEC)
        logger.info("Perception worker stopped after %d cycles.", self._cycles)

    def _cycle(self) -> None:
        frame = self._ctx.grab_frame(timeout_sec=self._frame_timeout)
        if frame is None:
            return
        stamp = self._clock()
        conf, imgsz, _ = self._snapshot_settings()

        started = time.perf_counter()
        result = self._infer(frame, conf, imgsz)
        inference_sec = time.perf_counter() - started

        if not self._engaged_event.is_set():
            # Disengaged mid-inference: the stage that asked for this result has
            # ended, and the next one must not inherit it.
            return

        sample = self._slot.publish(frame, result, stamp, inference_sec)
        self._cycles += 1
        _, _, status = self._snapshot_settings()
        self._ctx.publish_detection_summary(sample, status)

    def _log_fault(self, exc: Exception) -> None:
        now = self._clock()
        if now - self._last_fault_log >= _FAULT_LOG_INTERVAL_SEC:
            self._last_fault_log = now
            logger.error("Perception cycle failed: %s", exc, exc_info=True)


class SynchronousPerception(PerceptionPipeline):
    """Inline pipeline with the worker's interface and the pre-worker timing.

    Each :meth:`get_latest` performs one acquisition and one inference on the
    calling thread and returns a fresh sample, and :meth:`set_status` publishes
    that sample's overlay immediately with the given caption -- exactly one
    frame, one detection and one annotated image per control cycle, which is
    what the stages did before inference moved off their thread, including the
    full annotated frame on ``detection_stream_topic``. Used by the test suite
    to drive stages deterministically, and available as a fallback where a
    background thread is undesirable.
    """

    def __init__(
        self,
        ctx: "MissionContext",
        *,
        clock: Clock = time.monotonic,
        frame_timeout_sec: float = 1.0,
    ) -> None:
        super().__init__(ctx, clock=clock, frame_timeout_sec=frame_timeout_sec)
        self._engaged: bool = False
        self._published_generation: int = 0

    @property
    def engaged(self) -> bool:
        return self._engaged

    def engage(
        self, conf: Optional[float] = None, imgsz: Union[int, float, str, None] = None
    ) -> None:
        conf, size = self._validate_settings(conf, imgsz)
        with self._settings_lock:
            self._conf = conf
            self._imgsz = size
        self._slot.clear()
        self._published_generation = self._slot.generation
        self._engaged = True

    def disengage(self, timeout_sec: float = DEFAULT_DISENGAGE_TIMEOUT_SEC) -> bool:
        self._engaged = False
        return True

    def get_latest(self, max_age_sec: float) -> Optional[PerceptionSample]:
        _validate_age(max_age_sec)
        if not self._engaged:
            return None
        frame = self._ctx.grab_frame(timeout_sec=self._frame_timeout)
        if frame is None:
            return None
        stamp = self._clock()
        conf, imgsz, _ = self._snapshot_settings()
        started = time.perf_counter()
        result = self._infer(frame, conf, imgsz)
        return self._slot.publish(frame, result, stamp, time.perf_counter() - started)

    def set_status(self, text: str) -> None:
        super().set_status(text)
        sample = self._slot.latest()
        if sample is None or sample.generation <= self._published_generation:
            return
        self._published_generation = sample.generation
        self._ctx.publish_annotated_stream(sample.frame, sample.result, str(text))
