"""``mission.py --standby``: prepared before the click, counting from it.

Runs the real mission, ``--no-fly``, in a private ``ROS_DOMAIN_ID``. Measured
before this mode: 40 s at the station from the click to the first countdown
tick.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from mvp_mission_bebop.engine.launch import EXIT_STANDBY_MISMATCH, STANDBY_READY_LINE

_REPO = os.path.join(os.path.dirname(__file__), "..")
_DOMAIN = 96


def _doc(**kinematics):
    return {
        "no_fly": True,
        "kinematics": {"countdown_sec": 3.0, "takeoff_stabilize_duration_sec": 1.0, **kinematics},
        "battery": {"land_pct": 20.0},
    }


class Mission:
    def __init__(self, tmp_path, doc):
        env = {
            **os.environ,
            "ROS_DOMAIN_ID": str(_DOMAIN),
            "BMG_GCS_SESSION": "1",
            "PYTHONUNBUFFERED": "1",
            "BMG_INFERENCE_CACHE": str(tmp_path / "inference_device.json"),
        }
        self.process = subprocess.Popen(
            [
                sys.executable, "-m", "mvp_mission_bebop.mission", "--standby", "--no-fly", "--stages", "1",
                "--config", str(tmp_path / "mission_config.json"),
                "--bench-frame", os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
                "--model-path", os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
                "--params-json", json.dumps(doc),
            ],
            cwd=tmp_path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.lines = []
        self.ready = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.append((time.monotonic(), line.rstrip("\n")))
            if STANDBY_READY_LINE in line:
                self.ready.set()

    def send(self, command):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()

    def first(self, needle, after=0.0):
        return next((t for t, line in self.lines if needle in line and t >= after), None)

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)


@pytest.fixture
def mission(tmp_path):
    pytest.importorskip("rclpy")
    started = []

    def start(doc):
        m = Mission(tmp_path, doc)
        started.append(m)
        assert m.ready.wait(120), "standby never became ready:\n" + "\n".join(l for _, l in m.lines[-40:])
        return m

    yield start
    for m in started:
        m.close()


def test_the_countdown_starts_at_the_click_and_the_takeoff_is_on_the_deadline(mission):
    m = mission(_doc())
    assert m.first("[STEP 1") is None, "a standby mission entered Stage 1 before the go"
    assert m.first("[MILESTONE mission.parameters]") is None
    clicked = time.monotonic()
    m.send({"op": "go", "params": _doc(target_altitude_m=1.4), "launch_at_ms": int(time.time() * 1000)})
    assert m.process.wait(timeout=90) == 0, "\n".join(l for _, l in m.lines[-40:])
    first_tick = m.first("[MILESTONE mission.countdown]", after=clicked)
    takeoff = m.first("[MILESTONE mission.takeoff]", after=clicked)
    assert first_tick is not None and first_tick - clicked < 1.5
    assert takeoff is not None and 2.5 <= takeoff - clicked < 4.5
    parameters = next(l for _, l in m.lines if "[MILESTONE mission.parameters]" in l)
    assert json.loads(parameters[parameters.index("{"):])["target_altitude_m"] == pytest.approx(1.4)


def test_a_go_that_changes_a_start_up_field_is_refused(mission):
    m = mission(_doc())
    doc = _doc()
    doc["no_fly"] = False
    m.send({"op": "go", "params": doc, "launch_at_ms": int(time.time() * 1000)})
    assert m.process.wait(timeout=30) == EXIT_STANDBY_MISMATCH
    assert any("no_fly" in l for _, l in m.lines if "[STANDBY] mismatch" in l)
    assert m.first("[STEP 1") is None


def test_quit_leaves_without_flying(mission):
    m = mission(_doc())
    m.send({"op": "quit"})
    assert m.process.wait(timeout=30) == 0
    assert m.first("[STEP 1") is None
