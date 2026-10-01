"""Pure parts of ``scripts/bench_startup.py`` (7.5)."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_startup.py")
_spec = importlib.util.spec_from_file_location("bench_startup", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def test_timing_phases_are_read_from_the_log():
    lines = [
        "2026-09-30 [Mission] INFO: [TIMING] phase=imports ms=2100",
        "noise",
        "[TIMING] phase=driver_connect ms=340",
        "[TIMING] inference_device=CUDA p50_ms=45 p95_ms=46 selection_ms=900 cached=True candidates={}",
        "[TIMING] phase=driver_connect ms=10",
    ]
    assert bench.parse_phases(lines) == {"imports": 2100, "driver_connect": 350}


def test_runs_are_summarized_by_median():
    runs = [
        {"step1_ms": 6000, "phases": {"imports": 2000}},
        {"step1_ms": 5000, "phases": {"imports": 1800, "warmup": 900}},
        {"step1_ms": 7000, "phases": {"imports": 2200}},
    ]
    summary = bench.summarize(runs)
    assert summary["step1_ms"] == 6000
    assert summary["phases"]["imports"] == 2000
    assert summary["phases"]["warmup"] == 900


def test_no_run_reaching_stage_one_is_an_error():
    with pytest.raises(ValueError):
        bench.summarize([{"step1_ms": None, "phases": {}}])
