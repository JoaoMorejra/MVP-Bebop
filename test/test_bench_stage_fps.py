"""Pure parts of ``scripts/bench_stage_fps.py`` (4.8)."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_stage_fps.py")
_spec = importlib.util.spec_from_file_location("bench_stage_fps", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def test_samples_are_attributed_to_the_stage_running_at_their_time():
    steps = [(10.0, 1), (20.0, 2), (30.0, 5)]
    assert bench.stage_at(5.0, steps, exit_t=40.0) == "pre"
    assert bench.stage_at(10.0, steps, exit_t=40.0) == "S1"
    assert bench.stage_at(25.0, steps, exit_t=40.0) == "S2"
    assert bench.stage_at(35.0, steps, exit_t=40.0) == "S5"
    assert bench.stage_at(41.0, steps, exit_t=40.0) == "post"


def test_each_stage_reports_the_median_fps_and_cpu():
    samples = [
        {"t": 1.0, "fps": 29.0, "cpu": 20.0},
        {"t": 2.0, "fps": 30.0, "cpu": 30.0},
        {"t": 11.0, "fps": 28.0, "cpu": 60.0},
        {"t": 12.0, "fps": 26.0, "cpu": 70.0},
        {"t": 13.0, "fps": 30.0, "cpu": 50.0},
    ]
    table = bench.per_stage(samples, [(10.0, 1)], exit_t=20.0)
    assert table["pre"] == {"fps_median": 29.5, "cpu_median": 25.0, "samples": 2}
    assert table["S1"] == {"fps_median": 28.0, "cpu_median": 60.0, "samples": 3}
    assert "S2" not in table


def test_the_order_of_the_table_follows_the_flight():
    samples = [{"t": t, "fps": 30.0, "cpu": 10.0} for t in (1, 11, 21, 31, 41)]
    table = bench.per_stage(samples, [(10.0, 1), (20.0, 2), (30.0, 5)], exit_t=40.0)
    assert list(table) == ["pre", "S1", "S2", "S5", "post"]
