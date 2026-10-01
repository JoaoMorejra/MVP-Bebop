#!/usr/bin/env python3
"""Cockpit video rate and station CPU by mission stage, on the live camera (4.8, 4.5f).

Expects the driver on its own domain and ``scripts/bench_relay.py`` forwarding
its sensor topics into ``--domain``. In that domain it starts the real MJPEG
bridge and ``mission.py --no-fly`` (station session, so no local speech), polls
the bridge's ``/status`` and ``/proc/stat`` every ``--period`` seconds, and
reports the median FPS and total CPU for each stage: before the mission, S1-S5
as the ``[STEP N`` markers say, and after it. The 4.5f rule reads the S2 CPU:
VAAPI decode only above 70 %.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_relay.py --source-domain 14 --target-domain 77 &
    python3 scripts/bench_stage_fps.py --domain 77
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STEP_LINE = re.compile(r"\[STEP ([1-5]):")
STAGE_ORDER = ("pre", "S1", "S2", "S3", "S4", "S5", "post")

PARAMS = {
    "kinematics": {"countdown_sec": 3.0, "takeoff_stabilize_duration_sec": 2.0, "hover_duration_sec": 4.0},
    "timeouts": {"search_timeout_sec": 15.0, "tracking_timeout_sec": 8.0},
    "rtl": {"timeout_sec": 10.0, "centering_timeout_sec": 5.0, "touchdown_timeout_sec": 6.0},
    "battery": {"land_pct": 20.0},
}


def stage_at(t: float, steps: Sequence[Tuple[float, int]], exit_t: Optional[float]) -> str:
    """Stage label at ``t``: ``pre`` before the first step, ``post`` after the exit."""
    if exit_t is not None and t >= exit_t:
        return "post"
    label = "pre"
    for started, number in steps:
        if t >= started:
            label = f"S{number}"
    return label


def per_stage(samples: Sequence[Dict[str, float]], steps: Sequence[Tuple[float, int]], exit_t: Optional[float]) -> Dict[str, Dict[str, Any]]:
    """Median FPS and CPU of the samples in each stage, in flight order."""
    grouped: Dict[str, List[Dict[str, float]]] = {}
    for sample in samples:
        grouped.setdefault(stage_at(sample["t"], steps, exit_t), []).append(sample)
    return {
        label: {
            "fps_median": round(statistics.median(s["fps"] for s in grouped[label]), 1),
            "cpu_median": round(statistics.median(s["cpu"] for s in grouped[label]), 1),
            "samples": len(grouped[label]),
        }
        for label in STAGE_ORDER
        if label in grouped
    }


def _cpu_counters() -> Tuple[int, int]:
    with open("/proc/stat", "r", encoding="ascii") as handle:
        values = [int(v) for v in handle.readline().split()[1:]]
    return sum(values), values[3] + values[4]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", default="77")
    parser.add_argument("--port", type=int, default=9393)
    parser.add_argument("--period", type=float, default=0.5)
    parser.add_argument("--pre-sec", type=float, default=8.0)
    parser.add_argument("--post-sec", type=float, default=8.0)
    args = parser.parse_args(argv)

    scratch = tempfile.mkdtemp(prefix="bmg-stage-fps-")
    env = {**os.environ, "ROS_DOMAIN_ID": str(args.domain), "BMG_GCS_SESSION": "1", "PYTHONUNBUFFERED": "1",
           "BMG_INFERENCE_CACHE": os.path.join(os.path.expanduser("~"), ".cache", "bmg", "inference_device.json")}
    bridge = subprocess.Popen(
        [sys.executable, os.path.join(_REPO, "bebop_mission_control", "streamer", "mjpeg_server.py"), str(args.port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    t0 = time.monotonic()
    samples: List[Dict[str, Any]] = []
    steps: List[Tuple[float, int]] = []
    exit_t: Optional[float] = None
    stop = threading.Event()

    def sample_loop() -> None:
        previous = _cpu_counters()
        while not stop.is_set():
            time.sleep(args.period)
            total, idle = _cpu_counters()
            dt, di = total - previous[0], idle - previous[1]
            previous = (total, idle)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/status", timeout=1.0) as response:
                    status = json.loads(response.read())
            except (OSError, ValueError):
                continue
            samples.append({"t": time.monotonic() - t0, "fps": float(status.get("fps") or 0.0),
                            "cpu": 100.0 * (dt - di) / dt if dt > 0 else 0.0, "source": status.get("source")})

    sampler = threading.Thread(target=sample_loop, daemon=True)
    sampler.start()
    time.sleep(args.pre_sec)

    mission = subprocess.Popen(
        [sys.executable, "-m", "mvp_mission_bebop.mission", "--no-fly", "--stages", "1,2,3,4,5",
         "--config", os.path.join(scratch, "mission_config.json"),
         "--model-path", os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
         "--params-json", json.dumps(PARAMS)],
        cwd=scratch, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    log: List[str] = []
    assert mission.stdout is not None
    for line in mission.stdout:
        log.append(line)
        match = STEP_LINE.search(line)
        if match:
            steps.append((time.monotonic() - t0, int(match.group(1))))
    code = mission.wait()
    exit_t = time.monotonic() - t0
    time.sleep(args.post_sec)
    stop.set()
    sampler.join(2.0)
    bridge.terminate()
    with open(os.path.join(scratch, "mission.log"), "w", encoding="utf-8") as handle:
        handle.writelines(log)
    sources = sorted({s.get("source") for s in samples if s.get("source")})
    print(json.dumps({"mission_exit": code, "sources": sources, "log": os.path.join(scratch, "mission.log"),
                      "stages": per_stage(samples, steps, exit_t)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
