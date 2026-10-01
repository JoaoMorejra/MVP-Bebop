"""Pure parts of ``scripts/bench_inference_devices.py`` (4.5g).

The measurement itself needs the station's GPUs and runs by hand; the
statistics, the CPU accounting from ``/proc/stat``, the MJPEG frame counter
and the table it writes to ``docs/`` are checked here.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_inference_devices.py")
_spec = importlib.util.spec_from_file_location("bench_inference_devices", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def test_latency_statistics_use_the_selection_rule_percentiles():
    timings = [float(value) for value in range(1, 101)]
    p50, p95 = bench.latency_stats(timings)
    assert p50 == pytest.approx(50.5)
    assert p95 == pytest.approx(95.0)


def test_latency_statistics_refuse_an_empty_run():
    with pytest.raises(ValueError):
        bench.latency_stats([])


def test_cpu_busy_fraction_from_two_proc_stat_samples():
    before = "cpu  100 0 100 800 0 0 0 0 0 0\n"
    after = "cpu  200 0 150 850 0 0 0 0 0 0\n"
    assert bench.cpu_busy_percent(before, after) == pytest.approx(75.0)


def test_cpu_busy_counts_iowait_as_idle():
    before = "cpu  0 0 0 0 0 0 0 0 0 0\n"
    after = "cpu  50 0 0 25 25 0 0 0 0 0\n"
    assert bench.cpu_busy_percent(before, after) == pytest.approx(50.0)


def test_cpu_busy_rejects_a_malformed_sample():
    with pytest.raises(ValueError):
        bench.cpu_busy_percent("intr 1 2 3\n", "cpu  1 1 1 1\n")


def test_the_mjpeg_counter_finds_frames_split_across_chunks():
    counter = bench.JpegFrameCounter()
    frame = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8" + b"\x00" * 10 + b"\xff\xd9\r\n"
    stream = frame * 3
    for start in range(0, len(stream), 7):
        counter.feed(stream[start : start + 7])
    assert counter.frames == 3


def test_the_table_lists_every_row_and_the_host():
    rows = [
        {"device": "CUDA FP32", "imgsz": 480, "p50_ms": 47.2, "p95_ms": 48.9, "rate_hz": 21.0, "cpu_pct": 31.4, "mjpeg_fps": None},
        {"device": "IGPU FP16", "imgsz": 480, "p50_ms": 40.0, "p95_ms": 42.1, "rate_hz": 24.5, "cpu_pct": 22.0, "mjpeg_fps": 28.7},
    ]
    text = bench.render_markdown(rows, {"date": "2026-09-30", "host": "i7-8550U", "frame": "bench_target.jpg"})
    assert "| CUDA FP32 | 480 | 47.2 | 48.9 | 21.0 | 31.4 | - |" in text
    assert "| IGPU FP16 | 480 | 40.0 | 42.1 | 24.5 | 22.0 | 28.7 |" in text
    assert "i7-8550U" in text and "2026-09-30" in text
    assert "Menor p95 a 480 px: IGPU FP16" in text


def test_rounds_are_pooled_into_one_row():
    runs = [
        {"timings": [10.0] * 10, "elapsed": 1.0, "cpu_pct": 20.0, "mjpeg_fps": None},
        {"timings": [30.0] * 10, "elapsed": 3.0, "cpu_pct": 60.0, "mjpeg_fps": None},
    ]
    row = bench.combine_runs(runs)
    assert row["p50_ms"] == pytest.approx(20.0)
    assert row["p95_ms"] == pytest.approx(30.0)
    assert row["rate_hz"] == pytest.approx(5.0)
    assert row["cpu_pct"] == pytest.approx(50.0)
    assert row["mjpeg_fps"] is None


def test_pooled_stream_rate_ignores_rounds_without_a_stream():
    runs = [
        {"timings": [1.0] * 5, "elapsed": 1.0, "cpu_pct": 0.0, "mjpeg_fps": 30.0},
        {"timings": [1.0] * 5, "elapsed": 1.0, "cpu_pct": 0.0, "mjpeg_fps": None},
        {"timings": [1.0] * 5, "elapsed": 2.0, "cpu_pct": 0.0, "mjpeg_fps": 24.0},
    ]
    assert bench.combine_runs(runs)["mjpeg_fps"] == pytest.approx(26.0)
