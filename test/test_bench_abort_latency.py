"""Abort-delivery bench: command extraction and the loss/latency summary.

The bench measures the exact CLI backstop the station runs, so it reads the
command strings from ``electron/missionLifecycle.cjs`` instead of keeping a copy
that could drift from what is actually shipped.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_abort_latency.py")
_LIFECYCLE = os.path.join(
    os.path.dirname(__file__), "..", "bebop_mission_control", "electron", "missionLifecycle.cjs"
)


def load_bench():
    spec = importlib.util.spec_from_file_location("bench_abort_latency", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_commands_are_read_from_the_shipped_lifecycle_module():
    bench = load_bench()
    with open(_LIFECYCLE, encoding="utf-8") as handle:
        commands = bench.extract_cli_commands(handle.read())
    assert set(commands) == {"LAND_PUB", "STOP_PUB"}
    assert "/bebop/land std_msgs/msg/Empty" in commands["LAND_PUB"]
    assert "/bebop/cmd_vel geometry_msgs/msg/Twist" in commands["STOP_PUB"]
    assert "-t 10" in commands["LAND_PUB"]


def test_missing_command_in_source_is_refused():
    bench = load_bench()
    with pytest.raises(ValueError, match="STOP_PUB"):
        bench.extract_cli_commands("const LAND_PUB =\n  'ros2 topic pub /bebop/land';\n")


def test_summary_counts_losses_and_percentiles_over_delivered_attempts():
    bench = load_bench()
    summary = bench.summarize([0.010, None, 0.030, 0.020, None])
    assert summary["attempts"] == 5
    assert summary["delivered"] == 3
    assert summary["lost"] == 2
    assert summary["p50_ms"] == pytest.approx(20.0)
    assert summary["max_ms"] == pytest.approx(30.0)


def test_summary_of_all_lost_has_no_percentiles():
    bench = load_bench()
    summary = bench.summarize([None, None])
    assert summary["lost"] == 2
    assert summary["p50_ms"] is None
    assert summary["p95_ms"] is None


@pytest.mark.parametrize("value", [0, -3, "x", True])
def test_attempt_count_must_be_a_positive_integer(value):
    bench = load_bench()
    with pytest.raises((ValueError, TypeError)):
        bench.validate_attempts(value)


def test_a_land_sent_the_moment_the_bridge_is_ready_is_delivered():
    """Measured: with no wait after ``ready``, the bridge lost most first lands.

    ``ready`` is emitted once the node exists, before DDS has matched the land
    publisher with the driver's subscription, and an unmatched publish goes to
    no one. The station starts the bridge at boot, but the supervisor restarts
    it on a crash, and an abort can arrive right after. Three consecutive runs,
    first command immediately after ``ready``.
    """
    import json
    import subprocess
    import sys

    pytest.importorskip("rclpy")
    # One process per run, as in service: several rclpy contexts created and
    # shut down in one process are slower to be discovered than a fresh one.
    for run in range(3):
        completed = subprocess.run(
            [sys.executable, _SCRIPT, "--path", "bridge", "--attempts", "2",
             "--domain", str(95 + run), "--bridge-settle-sec", "0"],
            capture_output=True, text=True, timeout=60,
        )
        result = json.loads(completed.stdout)
        assert result["latencies_ms"][0] is not None, f"run {run}: first land lost: {result}"
        assert result["lost"] == 0, result
