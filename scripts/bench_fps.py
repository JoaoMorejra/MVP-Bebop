#!/usr/bin/env python3
"""Benchtop measurement of the cockpit video rate across the detection reveal.

Stands in for the Bebop's camera with a synthetic 30 Hz publisher on the raw
topic, runs the real MJPEG bridge and a real ``mission.py --no-fly`` against it,
and samples what the cockpit FPS chip reads (the bridge's ``/status``) together
with the rate a client actually receives on ``/stream``.

The synthetic camera serves a blurred frame the detector does not fire on for
``--hidden-sec`` seconds after Stage 2 begins, then the target frame. That
yields three windows per run, reported separately:

* ``stage1``: takeoff, no inference running;
* ``search_no_target``: inference running, nothing to draw;
* ``target_visible``: inference running on a detected target -- boxes hidden
  or drawn depending on the build under test.

Everything runs on a private ``ROS_DOMAIN_ID`` and a scratch parameter store,
so neither a live GCS nor the operator's ``mission_config.json`` is touched.
A tree predating ``--config`` writes its store beside its own module instead,
which is why the baseline is meant to run from a separate worktree.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_fps.py --tree . --label after
    python3 scripts/bench_fps.py --tree /path/to/worktree --label before
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
import urllib.request
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

#: Nominal rate of the Bebop 2 front camera stream, frames per second.
CAMERA_HZ: float = 30.0

CAMERA_SOURCE = r'''
import os, sys, time
import cv2, rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

target = cv2.imread(sys.argv[1])
hidden = cv2.GaussianBlur(target, (51, 51), 0)
flag, hz = sys.argv[2], float(sys.argv[3])
rclpy.init()
node = Node("bench_camera")
qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1)
pub = node.create_publisher(Image, "/bebop/camera/image_raw", qos)
bridge = CvBridge()
period = 1.0 / hz
deadline = time.monotonic()
while rclpy.ok():
    frame = target if os.path.exists(flag) else hidden
    message = bridge.cv2_to_imgmsg(frame, encoding="bgr8")
    message.header.stamp = node.get_clock().now().to_msg()
    message.header.frame_id = "bebop_camera"
    pub.publish(message)
    deadline += period
    time.sleep(max(0.0, deadline - time.monotonic()))
'''


def _stream_counter(url: str, stamps: List[float], stop: threading.Event) -> None:
    """Consume ``/stream`` like the cockpit ``<img>`` does, stamping each JPEG."""
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                buffer = b""
                while not stop.is_set():
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while True:
                        start = buffer.find(b"\xff\xd8")
                        end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                        if start < 0 or end < 0:
                            break
                        stamps.append(time.monotonic())
                        buffer = buffer[end + 2 :]
        except Exception:  # noqa: BLE001 - the bridge may not be up yet
            time.sleep(0.2)


def _status_sampler(
    url: str, samples: List[Tuple[float, float, str]], stop: threading.Event
) -> None:
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                status = json.loads(response.read())
            samples.append((time.monotonic(), float(status.get("fps", 0.0)), status.get("source", "")))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)


def _window_rate(stamps: List[float], start: float, end: float) -> Optional[float]:
    inside = [t for t in stamps if start <= t < end]
    if len(inside) < 2 or end - start <= 0.0:
        return None
    return (len(inside) - 1) / (inside[-1] - inside[0])


def _describe(
    name: str,
    start: Optional[float],
    end: Optional[float],
    samples: List[Tuple[float, float, str]],
    stamps: List[float],
    settle_sec: float,
) -> Dict[str, object]:
    if start is None or end is None or end - start <= 2.0 * settle_sec:
        return {"window": name, "error": "window not observed"}
    lo, hi = start + settle_sec, end - settle_sec
    chip = [fps for t, fps, _ in samples if lo <= t < hi]
    sources = sorted({source for t, _, source in samples if lo <= t < hi})
    return {
        "window": name,
        "duration_sec": round(hi - lo, 1),
        "chip_fps_mean": round(statistics.fmean(chip), 2) if chip else None,
        "chip_fps_min": round(min(chip), 1) if chip else None,
        "chip_fps_p5": round(sorted(chip)[max(0, int(0.05 * len(chip)) - 1)], 1) if chip else None,
        "client_fps": round(_window_rate(stamps, lo, hi) or 0.0, 2),
        "sources": sources,
        "samples": len(chip),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", default=".", help="Repository root whose code is measured.")
    parser.add_argument("--label", default="run")
    parser.add_argument("--port", type=int, default=9191)
    parser.add_argument("--domain", default="77", help="Private ROS_DOMAIN_ID.")
    parser.add_argument("--hidden-sec", type=float, default=10.0)
    parser.add_argument("--tracking-sec", type=float, default=15.0)
    parser.add_argument("--settle-sec", type=float, default=0.5)
    parser.add_argument(
        "--exit-grace-sec",
        type=float,
        default=30.0,
        help="Time mission.py is given to exit after its cleanup line before it is reported hung.",
    )
    parser.add_argument("--output", default=None, help="JSON report path.")
    args = parser.parse_args()

    tree = os.path.abspath(args.tree)
    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(os.path.dirname(here), "test", "fixtures", "bench_target.jpg")
    workdir = tempfile.mkdtemp(prefix=f"bench-fps-{args.label}-")
    flag = os.path.join(workdir, "target_visible")

    env = dict(os.environ, ROS_DOMAIN_ID=args.domain, PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = tree + os.pathsep + env.get("PYTHONPATH", "")

    camera_script = os.path.join(workdir, "camera.py")
    with open(camera_script, "w", encoding="utf-8") as handle:
        handle.write(CAMERA_SOURCE)

    processes: List[subprocess.Popen] = []

    def spawn(argv: List[str], **kwargs) -> subprocess.Popen:
        process = subprocess.Popen(argv, env=env, cwd=workdir, **kwargs)
        processes.append(process)
        return process

    spawn([sys.executable, camera_script, fixture, flag, str(CAMERA_HZ)],
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    spawn([sys.executable, os.path.join(tree, "bebop_mission_control", "streamer", "mjpeg_server.py"),
           str(args.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    stop = threading.Event()
    samples: List[Tuple[float, float, str]] = []
    stamps: List[float] = []
    base = f"http://127.0.0.1:{args.port}"
    threads = [
        threading.Thread(target=_status_sampler, args=(f"{base}/status", samples, stop), daemon=True),
        threading.Thread(target=_stream_counter, args=(f"{base}/stream", stamps, stop), daemon=True),
    ]
    for thread in threads:
        thread.start()
    time.sleep(4.0)

    overrides = {
        "kinematics": {"countdown_sec": 0.0},
        "timeouts": {"search_timeout_sec": args.hidden_sec + 20.0,
                     "tracking_timeout_sec": args.tracking_sec},
    }
    mission_argv = [
        sys.executable, "-X", "faulthandler", "-m", "mvp_mission_bebop.mission",
        "--no-fly", "--stages", "1,2,3",
        "--model-path", os.path.join(tree, "mvp_mission_bebop", "yolov8n.pt"),
        "--params-json", json.dumps(overrides),
    ]
    probe = subprocess.run([sys.executable, os.path.join(tree, "mvp_mission_bebop", "mission.py"), "--help"],
                           env=env, capture_output=True, text=True)
    if "--config" in probe.stdout:
        mission_argv += ["--config", os.path.join(workdir, "mission_config.json")]

    marks: Dict[str, float] = {}
    log_path = os.path.join(workdir, "mission.log")
    mission = spawn(mission_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        for line in mission.stdout:
            now = time.monotonic()
            log.write(f"{now:.3f} {line}")
            step = re.search(r"\[STEP ([1-5]):", line)
            if step and f"step{step.group(1)}" not in marks:
                marks[f"step{step.group(1)}"] = now
                if step.group(1) == "2":
                    threading.Timer(args.hidden_sec, lambda: open(flag, "w").close()).start()
            if "confirmed at" in line and "confirmed" not in marks:
                marks["confirmed"] = now
            if "Mission resource cleanup finalized" in line:
                marks["end"] = now
                break
    exit_hang = False
    try:
        mission.wait(timeout=args.exit_grace_sec)
    except subprocess.TimeoutExpired:
        exit_hang = True
        mission.send_signal(signal.SIGABRT)
        try:
            mission.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mission.kill()
    if mission.stdout is not None:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(mission.stdout.read() or "")
    marks.setdefault("end", time.monotonic())
    stop.set()

    for process in processes:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    report = {
        "label": args.label,
        "tree": tree,
        "camera_hz": CAMERA_HZ,
        "mission_exit": mission.returncode,
        "exit_hang": exit_hang,
        "log": log_path,
        "windows": [
            _describe("stage1", marks.get("step1"), marks.get("step2"), samples, stamps, args.settle_sec),
            _describe("search_no_target", marks.get("step2"), marks.get("confirmed"), samples, stamps,
                      args.settle_sec),
            _describe("target_visible", marks.get("confirmed"), marks.get("end"), samples, stamps,
                      args.settle_sec),
        ],
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    return 0 if mission.returncode == 0 and not exit_hang else 1


if __name__ == "__main__":
    sys.exit(main())
