"""Inference device selection for the mission detector: CUDA, integrated GPU, CPU.

Decision D3: the station's three compute devices are enabled -- the NVIDIA
GeForce MX110 through CUDA, the Intel HD 620 through OpenVINO's GPU plugin, and
the CPU through OpenVINO -- and the detector runs on whichever the benchmark
shows is best, with an automatic fallback down the chain at run time.

The Nectar SDK's ``Detector`` resolves its ``device`` through torch
(``nectar.ai.core.utils.device.get_device``), which knows ``cpu``, ``cuda`` and
``mps`` only. An OpenVINO device therefore cannot be named through it: handed a
torch device, Ultralytics' OpenVINO backend compiles for ``AUTO``, which on
this station may pick either GPU. The SDK is not edited; the OpenVINO paths
build the SDK detector as usual and pin the device on the Ultralytics predictor
underneath it (:class:`_PinnedPredict`), which also carries FP16 to the MX110.

Bench, one 856x480 frame, YOLOv8n, p50/p95 in ms, three interleaved rounds of
40 (``scripts/bench_inference_devices.py``, ``docs/BENCH_INFERENCIA.md``,
2026-09-30), with the station's total CPU during continuous inference:

=====================  ===========  ===========  ===========
device                 640 px       480 px       CPU @480
=====================  ===========  ===========  ===========
MX110 CUDA FP32        64 / 65      49 / 50      31%
MX110 CUDA FP16        54 / 56      45 / 46      31%
HD 620 OpenVINO FP16   37 / 41      26 / 30      53%
CPU OpenVINO (idle)    65 / 82      37 / 45      68%
=====================  ===========  ===========  ===========

FP16 on the MX110 is 7% faster and leaves the detections unchanged on the 32
evidence frames (same classes, confidence within 0.004, IoU >= 0.997), so CUDA
runs FP16 (:data:`CUDA_QUANTIZE`). An earlier reading of "no gain" reused one
Ultralytics predictor for both precisions, which keeps the first one's dtype.
Idle, the HD 620 wins; it shares the package's 15 W with the CPU, and under the
mission's own load it measured 45/72 ms against CUDA's 49/51. Loading it costs
5.4 s at start-up against ~2.5 s for CUDA (measured live). Operator decision
(2026-10-01): ``AUTO`` takes the MX110 whenever it works; the benchmark of
:func:`select_detector` decides between the others only without it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import statistics
import threading
import time
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

logger = logging.getLogger("InferenceDevice")

#: Values accepted for ``vision.inference_device``.
INFERENCE_DEVICES: Final[Tuple[str, ...]] = ("AUTO", "CUDA", "IGPU", "CPU")

#: Preference order, used for the fallback chain and to break technical ties.
#: CUDA first: it leaves the integrated GPU to the display and the compositor.
PREFERENCE: Final[Tuple[str, ...]] = ("CUDA", "IGPU", "CPU")

#: Relative p95 difference below which two devices are a technical tie.
TIE_RATIO: Final[float] = 0.10

#: Inferences discarded per device before timing (allocation, compilation).
WARMUP_SAMPLES: Final[int] = 3

#: Timed inferences per device in a full benchmark.
BENCH_SAMPLES: Final[int] = 30

#: Fewest timed inferences for a device's measurement to count.
MIN_BENCH_SAMPLES: Final[int] = 5

#: Timed inferences validating a cached choice.
VALIDATION_SAMPLES: Final[int] = 5

#: Ceiling of the timed benchmark, all devices, seconds. Model loading and the
#: warmup inferences are not counted: the first CUDA inference costs ~1.9 s
#: (cuDNN initialisation) and the first OpenVINO GPU one ~1 s (compilation), so
#: counting them left the later devices unmeasured and the first one won by
#: default. They are reported separately as ``load_ms``.
BENCH_BUDGET_SEC: Final[float] = 6.0

#: Warm-up inferences a CUDA detector receives in the preload (7.4).
#:
#: Measured on the MX110 at 480 px: the first call 1.7 s (torch imports its
#: dynamo machinery and selects kernels), the second 47 ms, every later one
#: 44-46 ms. Two leave it resident at full cadence; the selection's own
#: validation then runs eight more on the real frame. Was 10.
CUDA_WARMUP_SAMPLES: Final[int] = 2

#: Share of the MX110's 2 GB this process may allocate.
CUDA_MEMORY_FRACTION: Final[float] = 0.8
#: Ultralytics precision on the MX110. FP16 measured 7% faster at 480 px
#: (``docs/BENCH_INFERENCIA.md``) with the detections unchanged on the evidence set.
CUDA_QUANTIZE: Final[int] = 16

#: A cached choice whose validation p95 exceeds its recorded p95 by more than
#: this factor is re-benchmarked.
CACHE_REVALIDATION_FACTOR: Final[float] = 1.5

Clock = Callable[[], float]
Builder = Callable[[str], Any]


def normalize_inference_device(value: Any) -> str:
    """Validate ``vision.inference_device``.

    Parameters
    ----------
    value : str
        One of :data:`INFERENCE_DEVICES`, in any case, surrounding space allowed.

    Returns
    -------
    str
        The canonical upper-case name.

    Raises
    ------
    TypeError
        If ``value`` is not a string.
    ValueError
        If it names no known device.
    """
    if not isinstance(value, str):
        raise TypeError(f"vision.inference_device must be a string, got {type(value).__name__}")
    name = value.strip().upper()
    if name not in INFERENCE_DEVICES:
        raise ValueError(f"vision.inference_device {value!r} is not one of {INFERENCE_DEVICES}")
    return name


def openvino_model_path(model_path: str) -> str:
    """The OpenVINO IR directory Ultralytics exports beside ``model_path``."""
    stripped = model_path.rstrip("/")
    if stripped.endswith("_openvino_model"):
        return stripped
    root, _extension = os.path.splitext(stripped)
    return f"{root}_openvino_model"


def choose_device(stats: Dict[str, Tuple[float, float]]) -> str:
    """Pick a device from ``{device: (p50_ms, p95_ms)}``.

    The lowest p95 wins; every device within :data:`TIE_RATIO` of it is a
    technical tie, settled by :data:`PREFERENCE`.

    Raises
    ------
    ValueError
        If ``stats`` is empty.
    """
    if not stats:
        raise ValueError("no device was measured")
    best = min(p95 for _p50, p95 in stats.values())
    tied = [device for device, (_p50, p95) in stats.items() if p95 <= best * (1.0 + TIE_RATIO)]
    return min(tied, key=lambda device: PREFERENCE.index(device) if device in PREFERENCE else len(PREFERENCE))


def _chain_from(start: str, available: Sequence[str]) -> List[str]:
    """``start`` and every available device after it in :data:`PREFERENCE`."""
    index = PREFERENCE.index(start)
    return [device for device in PREFERENCE[index:] if device in available]


class FallbackDetector:
    """The mission detector, falling back down a device chain when one fails.

    A CUDA error, an out-of-memory or a GPU that disappears raises inside
    ``detect``; the frame is then retried on the next device of the chain,
    which is built on the spot, and the switch is logged as a WARNING. Anything
    else the mission reads from the detector is delegated to the current one.

    Parameters
    ----------
    chain : sequence of str
        Devices in fallback order.
    build : callable
        Builds an unloaded detector for a device name.
    primary : object, optional
        An already loaded detector for ``chain[0]``, reused rather than rebuilt.
    """

    def __init__(self, chain: Sequence[str], build: Builder, primary: Optional[Any] = None) -> None:
        if not chain:
            raise ValueError("the fallback chain is empty")
        self.chain: List[str] = list(chain)
        self._build = build
        self._index = 0
        self._current: Optional[Any] = primary
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        """Device the detector currently runs on."""
        return self.chain[self._index]

    def load(self) -> bool:
        """Build and load the first device of the chain that loads."""
        if self._current is None:
            self._activate(0, None)
        return True

    def _activate(self, start: int, cause: Optional[BaseException]) -> None:
        last: Optional[BaseException] = cause
        for index in range(start, len(self.chain)):
            device = self.chain[index]
            try:
                detector = self._build(device)
                detector.load()
            except Exception as exc:  # noqa: BLE001 - a device that will not load is skipped
                logger.warning("Inference device %s unavailable (%s).", device, exc)
                last = exc
                continue
            if index != self._index or self._current is not None:
                logger.warning(
                    "Inference moved from %s to %s: %s",
                    self.chain[self._index] if self._current is not None else "none",
                    device,
                    cause if cause is not None else "initial load",
                )
            self._index = index
            self._current = detector
            return
        raise RuntimeError(f"no inference device could be loaded ({last})") from last

    def detect(self, frame: Any, **kwargs: Any) -> Any:
        """Detect on the current device, falling back once per failure."""
        with self._lock:
            detector = self._current
            index = self._index
        try:
            return detector.detect(frame, **kwargs)
        except Exception as exc:  # noqa: BLE001 - classified by the fallback, re-raised when exhausted
            with self._lock:
                if self._index == index:
                    if index + 1 >= len(self.chain):
                        raise
                    self._activate(index + 1, exc)
                detector = self._current
            return detector.detect(frame, **kwargs)

    def __getattr__(self, name: str) -> Any:
        current = self.__dict__.get("_current")
        if current is None:
            raise AttributeError(name)
        return getattr(current, name)


def _warm(detector: Any, frame: Any, detect_kwargs: Dict[str, Any]) -> None:
    for _ in range(WARMUP_SAMPLES):
        detector.detect(frame, **detect_kwargs)


def _measure(
    detector: Any,
    frame: Any,
    samples: int,
    deadline: float,
    clock: Clock,
    detect_kwargs: Dict[str, Any],
) -> Optional[Tuple[float, float]]:
    """Time up to ``samples`` inferences of a warmed ``detector`` before ``deadline``."""
    timings: List[float] = []
    for _ in range(samples):
        if clock() >= deadline:
            break
        started = clock()
        detector.detect(frame, **detect_kwargs)
        timings.append((clock() - started) * 1000.0)
    if len(timings) < min(MIN_BENCH_SAMPLES, samples):
        return None
    timings.sort()
    p95 = timings[min(len(timings) - 1, int(round(0.95 * (len(timings) - 1))))]
    return statistics.median(timings), p95


def _load_cache(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_cache(path: Optional[str], key: str, entry: Dict[str, Any]) -> None:
    if not path:
        return
    data = _load_cache(path)
    data[key] = entry
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temporary, path)
    except OSError as exc:
        logger.debug("Inference device cache not written (%s): %s", path, exc)


def _release(detector: Any) -> None:
    del detector
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - best effort
        pass


def select_detector(
    requested: str,
    available: Sequence[str],
    build: Builder,
    *,
    frame: Any,
    cache_path: Optional[str],
    cache_key: str,
    clock: Clock = time.monotonic,
    detect_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[FallbackDetector, Dict[str, Any]]:
    """Build the mission detector on the requested or the benchmarked device.

    ``AUTO`` takes the MX110 whenever it loads and passes a
    :data:`VALIDATION_SAMPLES` check (operator decision, 2026-10-01: idle, the
    HD 620 measured a lower p95, but loading it cost 5.4 s at start-up against
    ~2.5 s for CUDA). Without CUDA, or with a CUDA that fails, it reuses a
    cached choice for ``cache_key`` after the same check, or benchmarks the
    remaining devices on ``frame`` within :data:`BENCH_BUDGET_SEC` and applies
    :func:`choose_device`.
    An explicit device is used as asked, or the next available one if absent.
    Either way the result falls back down :data:`PREFERENCE` at run time.

    Returns
    -------
    tuple
        The loaded :class:`FallbackDetector` and a report with ``device``,
        ``candidates`` (``{device: {"p50_ms", "p95_ms"}}``) and ``cached``.
    """
    name = normalize_inference_device(requested)
    kwargs = detect_kwargs or {}
    present = [device for device in PREFERENCE if device in available]
    if not present:
        raise RuntimeError("no inference device is available")
    report: Dict[str, Any] = {"requested": name, "device": None, "candidates": {}, "cached": False}

    if name != "AUTO":
        start = name if name in present else next(
            (device for device in PREFERENCE[PREFERENCE.index(name):] if device in present), present[0]
        )
        chain = FallbackDetector(_chain_from(start, present), build)
        chain.load()
        report["device"] = chain.device
        return chain, report

    if "CUDA" in present:
        try:
            detector = build("CUDA")
            detector.load()
            _warm(detector, frame, kwargs)
            measured = _measure(detector, frame, VALIDATION_SAMPLES, clock() + BENCH_BUDGET_SEC, clock, kwargs)
        except Exception as exc:  # noqa: BLE001 - the remaining devices are benchmarked instead
            logger.warning("Preferred inference device CUDA unusable (%s); benchmarking the others.", exc)
            measured = None
        if measured is not None:
            report.update(
                device="CUDA",
                preferred=True,
                candidates={"CUDA": {"p50_ms": round(measured[0], 1), "p95_ms": round(measured[1], 1)}},
            )
            return FallbackDetector(_chain_from("CUDA", present), build, primary=detector), report
        present = [device for device in present if device != "CUDA"]
        if not present:
            raise RuntimeError("the preferred CUDA device failed and no other device is available")

    cached = _load_cache(cache_path).get(cache_key)
    if isinstance(cached, dict) and cached.get("device") in present:
        device = cached["device"]
        try:
            detector = build(device)
            detector.load()
            _warm(detector, frame, kwargs)
            measured = _measure(detector, frame, VALIDATION_SAMPLES, clock() + BENCH_BUDGET_SEC, clock, kwargs)
        except Exception as exc:  # noqa: BLE001 - a stale cache entry is re-benchmarked
            logger.warning("Cached inference device %s failed validation (%s); re-benchmarking.", device, exc)
            measured = None
        recorded = float(cached.get("p95_ms") or 0.0)
        if measured is not None and (recorded <= 0.0 or measured[1] <= recorded * CACHE_REVALIDATION_FACTOR):
            report.update(device=device, cached=True, candidates={device: {"p50_ms": measured[0], "p95_ms": measured[1]}})
            return FallbackDetector(_chain_from(device, present), build, primary=detector), report

    spent = 0.0
    stats: Dict[str, Tuple[float, float]] = {}
    built: Dict[str, Any] = {}
    for position, device in enumerate(present):
        remaining = BENCH_BUDGET_SEC - spent
        if remaining <= 0.0:
            break
        try:
            load_started = clock()
            detector = build(device)
            detector.load()
            _warm(detector, frame, kwargs)
            report.setdefault("load_ms", {})[device] = round((clock() - load_started) * 1000.0, 1)
            timed_started = clock()
            measured = _measure(
                detector, frame, BENCH_SAMPLES, timed_started + remaining / (len(present) - position), clock, kwargs
            )
            spent += clock() - timed_started
        except Exception as exc:  # noqa: BLE001 - an unusable device is left out of the choice
            logger.warning("Inference device %s left out of the benchmark: %s", device, exc)
            continue
        built[device] = detector
        if measured is not None:
            stats[device] = measured
            report["candidates"][device] = {"p50_ms": round(measured[0], 1), "p95_ms": round(measured[1], 1)}

    if not stats:
        chosen = next(iter(built), present[0])
    else:
        chosen = choose_device(stats)
    primary = built.pop(chosen, None)
    for other in built.values():
        _release(other)
    if chosen in stats:
        _store_cache(
            cache_path,
            cache_key,
            {"device": chosen, "p50_ms": round(stats[chosen][0], 1), "p95_ms": round(stats[chosen][1], 1)},
        )
    report["device"] = chosen
    chain = FallbackDetector(_chain_from(chosen, present), build, primary=primary)
    chain.load()
    return chain, report


# -------------------------------------------------------------- preloading


def preload_plan(requested: str, available: Sequence[str], cache_path: Optional[str], cache_key: str) -> List[str]:
    """Devices :func:`select_detector` will build first, in order.

    An explicit device (or the next present one) alone; under ``AUTO`` the
    MX110 when present, else the cached choice alone, or every present device
    when the selection will benchmark.
    """
    name = normalize_inference_device(requested)
    present = [device for device in PREFERENCE if device in available]
    if not present:
        return []
    if name != "AUTO":
        start = name if name in present else next(
            (device for device in PREFERENCE[PREFERENCE.index(name):] if device in present), present[0]
        )
        return [start]
    if "CUDA" in present:
        return ["CUDA"]
    cached = _load_cache(cache_path).get(cache_key)
    if isinstance(cached, dict) and cached.get("device") in present:
        return [cached["device"]]
    return present


class _Loaded:
    """A detector already loaded by the preloader; ``load`` does not reload it."""

    def __init__(self, detector: Any) -> None:
        self._detector = detector

    def load(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_detector"], name)


class DetectorPreloader:
    """Loads and warms detectors on a thread while the mission waits for a frame (7.4).

    The first inference is the expensive one (cuDNN initialisation on the
    MX110, graph compilation on the HD 620), and it does not depend on the
    image, so it runs on ``warm_frame`` concurrently with the camera start-up.
    :meth:`build` is the builder handed to :func:`select_detector`: it waits for
    the preload and returns each preloaded detector once, then builds normally.

    Parameters
    ----------
    devices : sequence of str
        From :func:`preload_plan`.
    build : callable
        Builds an unloaded detector for a device name.
    warm_frame : object
        Image for the warm-up inference; the stream geometry is enough.
    detect_kwargs : dict, optional
        Passed to ``detect`` (``imgsz`` decides the compiled shape).
    join_timeout_sec : float
        Longest :meth:`build` waits for the preload before building directly.
    warm_counts : dict, optional
        Warm-up inferences per device (default 1). The MX110 takes
        :data:`CUDA_WARMUP_SAMPLES`, so it is resident with its kernels selected
        before the first search cycle.
    """

    def __init__(
        self,
        devices: Sequence[str],
        build: Builder,
        *,
        warm_frame: Any,
        detect_kwargs: Optional[Dict[str, Any]] = None,
        join_timeout_sec: float = 60.0,
        warm_counts: Optional[Dict[str, int]] = None,
    ) -> None:
        self._devices = list(devices)
        self._build = build
        self._frame = warm_frame
        self._kwargs = dict(detect_kwargs or {})
        self._join_timeout = join_timeout_sec
        self._warm_counts = dict(warm_counts or {})
        self._ready: Dict[str, Any] = {}
        self._failed: Dict[str, BaseException] = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="DetectorPreload", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        for device in self._devices:
            started = time.monotonic()
            try:
                detector = self._build(device)
                detector.load()
                for _ in range(max(1, int(self._warm_counts.get(device, 1)))):
                    detector.detect(self._frame, **self._kwargs)
            except Exception as exc:  # noqa: BLE001 - handed to the selection, which skips the device
                with self._lock:
                    self._failed[device] = exc
                continue
            with self._lock:
                self._ready[device] = detector
            logger.info("Preloaded inference device %s in %.0f ms.", device, (time.monotonic() - started) * 1000.0)

    def build(self, device: str) -> Any:
        """The preloaded detector for ``device`` (once), else a newly built one."""
        if device in self._devices:
            self.join(self._join_timeout)
        with self._lock:
            ready = self._ready.pop(device, None)
            failed = self._failed.pop(device, None)
        if ready is not None:
            return _Loaded(ready)
        if failed is not None:
            raise failed
        return self._build(device)


# -------------------------------------------------------------- real devices


def available_devices() -> Dict[str, str]:
    """Probe the station. Returns ``{device: backend id}``.

    ``CUDA`` maps to ``cuda:0``; ``IGPU`` to the OpenVINO id of the Intel
    integrated GPU, found by name because with the NVIDIA driver loaded the
    MX110 is enumerated by OpenVINO as a second ``GPU.N`` and the bare alias
    ``GPU`` is ambiguous; ``CPU`` is always present.
    """
    found: Dict[str, str] = {}
    try:
        import torch

        if torch.cuda.is_available():
            found["CUDA"] = "cuda:0"
    except Exception as exc:  # noqa: BLE001 - absence is a probe result, not an error
        logger.debug("CUDA probe: %s", exc)
    try:
        import openvino as ov

        core = ov.Core()
        for name in core.available_devices:
            if not name.startswith("GPU"):
                continue
            full = str(core.get_property(name, "FULL_DEVICE_NAME"))
            if "Intel" in full and "iGPU" in full:
                found["IGPU"] = name
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("OpenVINO probe: %s", exc)
    found["CPU"] = "CPU"
    return found


class _PinnedPredict:
    """An SDK detector whose Ultralytics ``predict`` always receives ``overrides``.

    ``Detector.detect`` forwards only ``conf``, ``iou`` and ``imgsz`` and hands
    Ultralytics a ``torch.device``. Two settings the station needs cannot pass
    through it, so after loading they are fixed on the predictor underneath:

    * the OpenVINO device: a ``torch.device`` reaches Ultralytics' OpenVINO
      backend as ``AUTO``; only an ``intel:<device>`` string selects one;
    * FP16 on the MX110 (``quantize=16``).
    """

    def __init__(self, detector: Any, overrides: Dict[str, Any]) -> None:
        self._detector = detector
        self._overrides = dict(overrides)

    def load(self) -> bool:
        loaded = self._detector.load()
        yolo = getattr(getattr(self._detector, "_model", None), "model", None)
        if yolo is None or not hasattr(yolo, "predict"):
            raise RuntimeError("cannot pin predictor arguments: SDK model layout changed")
        predict = yolo.predict
        overrides = self._overrides

        def pinned(*args: Any, **kwargs: Any) -> Any:
            kwargs.update(overrides)
            return predict(*args, **kwargs)

        yolo.predict = pinned
        return loaded

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_detector"], name)


#: Environment override of the OpenVINO compilation cache directory.
OPENVINO_CACHE_ENV: Final[str] = "BMG_OV_CACHE_DIR"


def enable_openvino_compile_cache() -> str:
    """Make every ``openvino.Core`` created from now on cache its compilations.

    Ultralytics builds its own ``Core`` when it loads an IR, so a ``CACHE_DIR``
    set on another instance never reaches it. Installing a subclass on the
    module attribute it calls does. Measured on this station: the CPU IR loads
    in 241 ms from the cache against 1148 ms cold, the HD 620 in 2084 against
    2613 ms. Idempotent. Returns the cache directory.
    """
    import openvino as ov

    directory = os.environ.get(OPENVINO_CACHE_ENV) or os.path.join(
        os.path.expanduser("~"), ".cache", "bmg", "ov"
    )
    os.makedirs(directory, exist_ok=True)
    if getattr(ov.Core, "_bmg_compile_cache", False):
        return directory
    base = ov.Core

    class CachingCore(base):  # type: ignore[misc, valid-type]
        _bmg_compile_cache = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            try:
                self.set_property({"CACHE_DIR": directory})
            except Exception as exc:  # noqa: BLE001 - the cache is an optimisation
                logger.debug("OpenVINO CACHE_DIR not set: %s", exc)

    ov.Core = CachingCore
    return directory


def build_device_detector(
    device: str,
    model_path: str,
    confidence: float,
    backends: Dict[str, str],
    *,
    cuda_quantize: int = CUDA_QUANTIZE,
) -> Any:
    """Construct an unloaded SDK detector for ``device``.

    ``cuda_quantize`` is the Ultralytics precision on the MX110 (16 or 32);
    the benchmark sets it, the mission keeps :data:`CUDA_QUANTIZE`.

    Raises
    ------
    RuntimeError
        If the device is not on this station.
    FileNotFoundError
        If the OpenVINO IR for an OpenVINO device has not been exported.
    """
    from nectar.ai.detection.detector import Detector

    if device not in backends:
        raise RuntimeError(f"{device} is not available on this station")
    if device == "CUDA":
        import torch

        torch.cuda.set_per_process_memory_fraction(CUDA_MEMORY_FRACTION, 0)
        detector = Detector(model_path, device=backends["CUDA"], confidence_threshold=confidence)
        return _PinnedPredict(detector, {"quantize": cuda_quantize})
    source = openvino_model_path(model_path)
    enable_openvino_compile_cache()
    if not os.path.isdir(source):
        raise FileNotFoundError(f"OpenVINO IR not found at {source}; export it with scripts/export_openvino_model.py")
    detector = Detector(source, framework="ultralytics", device="cpu", confidence_threshold=confidence)
    return _PinnedPredict(detector, {"device": f"intel:{backends[device].lower()}"})


def model_cache_key(model_path: str, imgsz: Optional[int]) -> str:
    """Key of a cached choice: the weights, their OpenVINO export, the input size and the CUDA precision."""
    digest = hashlib.sha1()
    for path in (model_path, os.path.join(openvino_model_path(model_path), "yolov8n.xml")):
        try:
            stat = os.stat(path)
            digest.update(f"{os.path.abspath(path)}:{stat.st_size}:{int(stat.st_mtime)}".encode())
        except OSError:
            digest.update(f"{path}:absent".encode())
    digest.update(f"imgsz={imgsz}:cuda_quantize={CUDA_QUANTIZE}".encode())
    return digest.hexdigest()
