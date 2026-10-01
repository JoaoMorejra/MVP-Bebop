#!/usr/bin/env python3
"""Mission start-up latency: process spawn to ``[STEP 1``, by phase.

Runs ``mission.py --no-fly --stages 1`` in a private ``ROS_DOMAIN_ID`` with a
scratch parameter store, a warm inference-device cache and ``BMG_GCS_SESSION``
set as the station sets it, ``--runs`` times,
and stops each run as soon as Stage 1 is entered. Reports, per run and as the
median, the time from spawn to ``[STEP 1`` and the ``[TIMING] phase=<name>
ms=<n>`` lines the mission logs (7.5). The first run warms the device cache and
is not counted.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_startup.py --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PHASE_LINE = re.compile(r"\[TIMING\] phase=(?P<name>[a-z_0-9]+) ms=(?P<ms>\d+)")
STEP1_MARKER = "[STEP 1"
DOMAIN = 89
#: Longest a single run may take to reach Stage 1, seconds.
RUN_TIMEOUT_SEC = 90.0

PARAMS = {
    "kinematics": {"countdown_sec": 0.0, "takeoff_stabilize_duration_sec": 1.0},
    "battery": {"land_pct": 20.0},
}


def parse_phases(lines: Iterable[str]) -> Dict[str, int]:
    """Sum of ``[TIMING] phase=`` durations by name, in milliseconds."""
    phases: Dict[str, int] = {}
    for line in lines:
        match = PHASE_LINE.search(line)
        if match:
            phases[match.group("name")] = phases.get(match.group("name"), 0) + int(match.group("ms"))
    return phases


def summarize(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Median spawn-to-Stage-1 time and median of each phase over ``runs``.

    Raises
    ------
    ValueError
        If no run reached Stage 1.
    """
    reached = [run for run in runs if run.get("step1_ms") is not None]
    if not reached:
        raise ValueError("no run reached Stage 1")
    names = sorted({name for run in reached for name in run["phases"]})
    return {
        "runs": len(reached),
        "step1_ms": int(statistics.median(run["step1_ms"] for run in reached)),
        "phases": {
            name: int(statistics.median(run["phases"][name] for run in reached if name in run["phases"]))
            for name in names
        },
    }


def run_once(scratch: str, domain: int = DOMAIN) -> Dict[str, Any]:
    """One mission start, stopped at Stage 1."""
    env = {
        **os.environ,
        "ROS_DOMAIN_ID": str(domain),
        "BMG_INFERENCE_CACHE": os.path.join(scratch, "inference_device.json"),
        "BMG_SPEECH_CACHE_DIR": os.path.join(scratch, "speech"),
        "PYTHONUNBUFFERED": "1",
        # As the ground station launches it (electron/main.cjs:startMissionProcess):
        # the station narrates, so the mission builds no synthesizer of its own.
        "BMG_GCS_SESSION": "1",
    }
    command = [
        sys.executable,
        "-m",
        "mvp_mission_bebop.mission",
        "--no-fly",
        "--stages",
        "1",
        "--config",
        os.path.join(scratch, "mission_config.json"),
        "--bench-frame",
        os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
        "--model-path",
        os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
        "--params-json",
        json.dumps(PARAMS),
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=scratch, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines: List[str] = []
    step1_ms: Optional[int] = None
    timer = threading.Timer(RUN_TIMEOUT_SEC, process.kill)
    timer.start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            if STEP1_MARKER in line and step1_ms is None:
                step1_ms = int((time.monotonic() - started) * 1000.0)
                process.send_signal(signal.SIGINT)
        process.wait(timeout=30.0)
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
    return {"step1_ms": step1_ms, "phases": parse_phases(lines)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print every run and the median as JSON. Returns 1 if no run reached Stage 1."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--domain",
        type=int,
        default=DOMAIN,
        help="ROS_DOMAIN_ID; a rehearsal domain fed by scripts/bench_relay.py measures with the live camera",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    scratch = tempfile.mkdtemp(prefix="bmg-startup-")
    run_once(scratch, args.domain)
    runs = [run_once(scratch, args.domain) for _ in range(args.runs)]
    try:
        summary = summarize(runs)
    except ValueError as exc:
        print(json.dumps({"runs": runs, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({"runs": runs, "median": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
