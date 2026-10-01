#!/usr/bin/env python3
"""Inference latency per device and input size, with CPU load and stream rate.

Measures, on one real frame, every device the station offers:

* ``CUDA FP32`` and ``CUDA FP16``: the SDK ``Detector`` on the MX110 with the
  precision fixed on its predictor (``quantize``), FP16 being what the
  mission runs;
* ``IGPU FP16`` and ``CPU``: the SDK ``Detector`` on the OpenVINO IR, pinned
  as the mission pins it (``perception/inference_device.py``);

at each ``imgsz`` (320, 480, 640 by default). For each pair it reports p50/p95
latency, the sustained inference rate, the total CPU use of the station over
the timed run (``/proc/stat``) and, with ``--mjpeg-url``, the rate a client
received from the MJPEG bridge in parallel. The table is written to
``docs/BENCH_INFERENCIA.md``; the ``vision.inference_imgsz`` and
``vision.inference_device`` defaults in ``parameters.py`` are read from it.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_inference_devices.py
    python3 scripts/bench_inference_devices.py --mjpeg-url http://127.0.0.1:8080/stream
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import threading
import time
import urllib.request
from datetime import date
from typing import Any, Callable, Dict, Final, List, Optional, Sequence, Tuple

_REPO: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_MODEL: Final[str] = os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt")
DEFAULT_FRAME: Final[str] = os.path.join(_REPO, "test", "fixtures", "bench_target.jpg")
DEFAULT_OUTPUT: Final[str] = os.path.join(_REPO, "docs", "BENCH_INFERENCIA.md")
DEFAULT_SIZES: Final[Tuple[int, ...]] = (320, 480, 640)
#: Untimed inferences per pair before the timed run; CUDA's first calls
#: include kernel selection, OpenVINO's the first-request allocation.
WARMUP_SAMPLES: Final[int] = 10
#: Timed inferences per pair and round; three rounds by default.
TIMED_SAMPLES: Final[int] = 40
ROUNDS: Final[int] = 3
#: Confidence used for every pair; only latency is under test.
CONFIDENCE: Final[float] = 0.30
#: Zero-based ``/proc/stat`` value columns counted as idle: ``idle`` and ``iowait``.
_IDLE_COLUMNS: Final[Tuple[int, ...]] = (3, 4)

Row = Dict[str, Any]


def latency_stats(timings_ms: Sequence[float]) -> Tuple[float, float]:
    """Median and p95 of ``timings_ms``, as ``inference_device._measure`` takes them.

    Raises
    ------
    ValueError
        If ``timings_ms`` is empty.
    """
    if not timings_ms:
        raise ValueError("no timings to summarize")
    ordered = sorted(float(value) for value in timings_ms)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return statistics.median(ordered), p95


def _cpu_counters(text: str) -> Tuple[int, int]:
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == "cpu" and len(fields) >= 5:
            values = [int(value) for value in fields[1:]]
            idle = sum(values[index] for index in _IDLE_COLUMNS if index < len(values))
            return sum(values), idle
    raise ValueError("no aggregate 'cpu' line in the /proc/stat sample")


def cpu_busy_percent(before: str, after: str) -> float:
    """Station-wide CPU use between two ``/proc/stat`` samples, percent.

    Raises
    ------
    ValueError
        If either sample has no aggregate ``cpu`` line.
    """
    total_0, idle_0 = _cpu_counters(before)
    total_1, idle_1 = _cpu_counters(after)
    elapsed = total_1 - total_0
    if elapsed <= 0:
        return 0.0
    return 100.0 * (elapsed - (idle_1 - idle_0)) / elapsed


def _read_proc_stat() -> str:
    with open("/proc/stat", "r", encoding="ascii") as handle:
        return handle.read()


class JpegFrameCounter:
    """Counts complete JPEGs (SOI ... EOI) in a multipart MJPEG byte stream."""

    def __init__(self) -> None:
        self.frames = 0
        self._inside = False
        self._tail = b""

    def feed(self, chunk: bytes) -> None:
        data = self._tail + chunk
        position = 0
        while True:
            marker = b"\xff\xd9" if self._inside else b"\xff\xd8"
            found = data.find(marker, position)
            if found < 0:
                break
            position = found + 2
            if self._inside:
                self.frames += 1
            self._inside = not self._inside
        self._tail = data[-1:] if data else b""


class MjpegSampler:
    """Reads ``url`` on a thread and counts frames between :meth:`mark` calls."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._counter = JpegFrameCounter()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.error: Optional[str] = None

    def _run(self) -> None:
        try:
            with urllib.request.urlopen(self._url, timeout=5.0) as response:
                while not self._stop.is_set():
                    chunk = response.read(16384)
                    if not chunk:
                        break
                    self._counter.feed(chunk)
        except OSError as exc:
            self.error = str(exc)

    def start(self) -> None:
        self._thread.start()

    def mark(self) -> Tuple[float, int]:
        return time.monotonic(), self._counter.frames

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


def _time_run(infer: Callable[[], Any], samples: int, sampler: Optional[MjpegSampler]) -> Dict[str, Any]:
    """One timed round: raw latencies, wall time, station CPU and stream rate."""
    stream_0 = sampler.mark() if sampler is not None else None
    cpu_0 = _read_proc_stat()
    started = time.monotonic()
    timings: List[float] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        infer()
        timings.append((time.perf_counter() - t0) * 1000.0)
    elapsed = time.monotonic() - started
    cpu_1 = _read_proc_stat()
    mjpeg_fps: Optional[float] = None
    if sampler is not None and stream_0 is not None and sampler.error is None:
        t1, frames_1 = sampler.mark()
        mjpeg_fps = (frames_1 - stream_0[1]) / max(1e-6, t1 - stream_0[0])
    return {"timings": timings, "elapsed": elapsed, "cpu_pct": cpu_busy_percent(cpu_0, cpu_1), "mjpeg_fps": mjpeg_fps}


def combine_runs(runs: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Pool timed rounds: percentiles over every latency, rates and CPU weighted by wall time.

    Raises
    ------
    ValueError
        If ``runs`` holds no latency at all.
    """
    timings = [value for run in runs for value in run["timings"]]
    p50, p95 = latency_stats(timings)
    elapsed = sum(float(run["elapsed"]) for run in runs)
    streamed = [run for run in runs if run["mjpeg_fps"] is not None]
    streamed_elapsed = sum(float(run["elapsed"]) for run in streamed)
    return {
        "p50_ms": p50,
        "p95_ms": p95,
        "rate_hz": len(timings) / elapsed if elapsed > 0 else None,
        "cpu_pct": sum(run["cpu_pct"] * run["elapsed"] for run in runs) / elapsed if elapsed > 0 else None,
        "mjpeg_fps": (
            sum(run["mjpeg_fps"] * run["elapsed"] for run in streamed) / streamed_elapsed
            if streamed_elapsed > 0
            else None
        ),
    }


def _sdk_runner(
    device: str,
    model_path: str,
    backends: Dict[str, str],
    frame: Any,
    quantize: Optional[int] = None,
) -> Callable[[int], Callable[[], Any]]:
    from mvp_mission_bebop.perception.inference_device import CUDA_QUANTIZE, build_device_detector

    detector = build_device_detector(
        device, model_path, CONFIDENCE, backends, cuda_quantize=quantize if quantize is not None else CUDA_QUANTIZE
    )
    if not detector.load():
        raise RuntimeError(f"{device} detector failed to load")
    return lambda imgsz: (lambda: detector.detect(frame, conf=CONFIDENCE, imgsz=imgsz))


def measure(
    model_path: str,
    frame: Any,
    sizes: Sequence[int],
    samples: int,
    sampler: Optional[MjpegSampler],
    log: Callable[[str], None],
    rounds: int = 3,
) -> List[Row]:
    """Run every available device at every size, ``rounds`` interleaved times.

    Devices that fail to build are logged and skipped.
    """
    from mvp_mission_bebop.perception.inference_device import available_devices

    backends = available_devices()
    plan: List[Tuple[str, Callable[[], Callable[[int], Callable[[], Any]]]]] = []
    if "CUDA" in backends:
        plan.append(("CUDA FP32", lambda: _sdk_runner("CUDA", model_path, backends, frame, quantize=32)))
        plan.append(("CUDA FP16", lambda: _sdk_runner("CUDA", model_path, backends, frame, quantize=16)))
    if "IGPU" in backends:
        plan.append(("IGPU FP16", lambda: _sdk_runner("IGPU", model_path, backends, frame)))
    plan.append(("CPU", lambda: _sdk_runner("CPU", model_path, backends, frame)))

    runners: List[Tuple[str, Callable[[int], Callable[[], Any]]]] = []
    for label, build in plan:
        try:
            runners.append((label, build()))
        except Exception as exc:  # noqa: BLE001 - one device failing must not end the bench
            log(f"{label}: unavailable ({exc})")

    # Interleaved: every device at every size in each round, rounds pooled.
    # A single device-by-device pass swung by up to 35% with the i5-7200U's
    # turbo budget and with whichever device had run before.
    for label, runner in runners:
        for imgsz in sizes:
            infer = runner(imgsz)
            for _ in range(WARMUP_SAMPLES):
                infer()
    runs: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for round_index in range(rounds):
        for imgsz in sizes:
            for label, runner in runners:
                runs.setdefault((label, imgsz), []).append(_time_run(runner(imgsz), samples, sampler))
        log(f"round {round_index + 1}/{rounds} done")

    rows: List[Row] = []
    for (label, imgsz), pair_runs in runs.items():
        row: Row = {"device": label, "imgsz": imgsz, **combine_runs(pair_runs)}
        rows.append(row)
        log(
            f"{label} @{imgsz}: p50 {row['p50_ms']:.1f} ms, p95 {row['p95_ms']:.1f} ms, "
            f"{row['rate_hz']:.1f} Hz, CPU {row['cpu_pct']:.1f}%"
            + (f", mjpeg {row['mjpeg_fps']:.1f} FPS" if row["mjpeg_fps"] is not None else "")
        )
    order = [label for label, _ in runners]
    rows.sort(key=lambda row: (order.index(row["device"]), row["imgsz"]))
    return rows


def _cell(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.1f}"


def render_markdown(rows: Sequence[Row], meta: Dict[str, str]) -> str:
    """The ``docs/`` table for ``rows``, with the host and the lowest p95 per size."""
    lines = [
        "# Benchmark de inferencia por device",
        "",
        f"- Data: {meta.get('date', '-')}",
        f"- Estacao: {meta.get('host', '-')}",
        f"- Quadro: {meta.get('frame', '-')}",
        f"- Gerado por `scripts/bench_inference_devices.py` ({meta.get('rounds', '-')} rodadas intercaladas);"
        " CPU = uso total da estacao durante a inferencia continua; mjpeg = FPS recebido do bridge em"
        " paralelo (`-` sem `--mjpeg-url`).",
        "",
        "| device | imgsz | p50 ms | p95 ms | inferencias/s | CPU % | mjpeg FPS |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['device']} | {row['imgsz']} | {_cell(row['p50_ms'])} | {_cell(row['p95_ms'])} | "
            f"{_cell(row['rate_hz'])} | {_cell(row['cpu_pct'])} | {_cell(row['mjpeg_fps'])} |"
        )
    lines.append("")
    for imgsz in sorted({int(row["imgsz"]) for row in rows}):
        best = min((row for row in rows if row["imgsz"] == imgsz), key=lambda row: row["p95_ms"])
        lines.append(f"- Menor p95 a {imgsz} px: {best['device']} ({best['p95_ms']:.1f} ms).")
    lines.append("")
    return "\n".join(lines)


def _host_description() -> str:
    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    parts = [cpu]
    try:
        import torch

        if torch.cuda.is_available():
            parts.append(f"{torch.cuda.get_device_name(0)} (torch {torch.__version__})")
    except Exception:  # noqa: BLE001
        pass
    try:
        import openvino

        parts.append(f"OpenVINO {openvino.__version__}")
    except Exception:  # noqa: BLE001
        pass
    return "; ".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Measure, print progress on stderr and write the table. Returns 1 if nothing ran."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-path", default=DEFAULT_MODEL)
    parser.add_argument("--frame", default=DEFAULT_FRAME)
    parser.add_argument("--sizes", default=",".join(str(size) for size in DEFAULT_SIZES))
    parser.add_argument("--samples", type=int, default=TIMED_SAMPLES)
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument("--mjpeg-url", default=None)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        sizes = [int(size) for size in str(args.sizes).split(",") if size.strip()]
    except ValueError:
        parser.error(f"--sizes must be comma-separated integers, got {args.sizes!r}")
    if args.samples < 5:
        parser.error("--samples must be at least 5")
    if args.rounds < 1:
        parser.error("--rounds must be at least 1")

    import cv2

    frame = cv2.imread(args.frame)
    if frame is None:
        parser.error(f"cannot read frame {args.frame}")

    def log(text: str) -> None:
        print(text, file=sys.stderr, flush=True)

    sampler = MjpegSampler(args.mjpeg_url) if args.mjpeg_url else None
    if sampler is not None:
        sampler.start()
    try:
        rows = measure(args.model_path, frame, sizes, args.samples, sampler, log, rounds=args.rounds)
    finally:
        if sampler is not None:
            sampler.stop()
            if sampler.error:
                log(f"mjpeg: {sampler.error}")
    if not rows:
        return 1

    text = render_markdown(
        rows,
        {
            "date": date.today().isoformat(),
            "host": _host_description(),
            "frame": f"{os.path.basename(args.frame)} ({frame.shape[1]}x{frame.shape[0]})",
            "rounds": f"{args.rounds} x {args.samples}",
        },
    )
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
