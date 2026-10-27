"""A launch document the mission cannot fly is refused, structured, with exit 78.

The cold start and the standby ``go`` both report the refusal on the alert
channel (``mission.launch_refused``) and on the ``[LAUNCH_REFUSED]`` line, and
exit with ``EXIT_LAUNCH_REFUSED`` before anything is commanded. Runs the real
mission, ``--no-fly``, in a private ``ROS_DOMAIN_ID`` outside the emulator's
80-99.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

from mvp_mission_bebop import mission as mission_module
from mvp_mission_bebop.engine.exit_codes import EXIT_LAUNCH_REFUSED
from mvp_mission_bebop.engine.launch import STANDBY_READY_LINE
from mvp_mission_bebop.launch_envelope import LaunchEnvelopeError
from mvp_mission_bebop.telemetry.milestones import ALERT_KEYS

_REPO = os.path.join(os.path.dirname(__file__), "..")
_DOMAIN = 77
_ALERT_LINE = re.compile(r"\[ALERT ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$")


def _doc(**overrides):
    doc = {
        "no_fly": True,
        "mission_mode": "simple",
        "kinematics": {"countdown_sec": 3.0, "takeoff_stabilize_duration_sec": 1.0, "target_altitude_m": 1.8},
        "platform": {"height_m": 1.0, "venue_ceiling_agl_m": 2.5},
        "battery": {"land_pct": 20.0},
    }
    for path, value in overrides.items():
        node = doc
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    return doc


def _command(tmp_path, doc, *extra):
    return [
        sys.executable, "-m", "mvp_mission_bebop.mission", "--no-fly", *extra,
        "--config", str(tmp_path / "mission_config.json"),
        "--bench-frame", os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
        "--model-path", os.path.join(_REPO, "mvp_mission_bebop", "yolov8n.pt"),
        "--params-json", json.dumps(doc),
    ]


def _env(tmp_path):
    return {
        **os.environ,
        "ROS_DOMAIN_ID": str(_DOMAIN),
        "BMG_GCS_SESSION": "1",
        "PYTHONUNBUFFERED": "1",
        "BMG_INFERENCE_CACHE": str(tmp_path / "inference_device.json"),
    }


def _refusals(lines):
    alerts = []
    for line in lines:
        match = _ALERT_LINE.search(line)
        if match and match.group(1) == "mission.launch_refused":
            alerts.append(json.loads(match.group(2)))
    printed = [line for line in lines if line.startswith("[LAUNCH_REFUSED] ")]
    return alerts, printed


# ------------------------------------------------------------------ contract


def test_the_alert_key_is_registered_and_the_exit_status_is_ex_config():
    assert "mission.launch_refused" in ALERT_KEYS
    assert EXIT_LAUNCH_REFUSED == 78


def test_an_envelope_error_names_its_field_and_reason():
    field, reason = mission_module.launch_refusal(LaunchEnvelopeError("kinematics.target_altitude_m", "abaixo"))
    assert (field, reason) == ("kinematics.target_altitude_m", "abaixo")


def test_a_mode_error_is_charged_to_the_mission_mode():
    field, reason = mission_module.launch_refusal(ValueError("mission mode 'expert' is not one of: simple"))
    assert field == "mission_mode"
    assert "expert" in reason


def _refusal_branch(source: str, marker: str) -> ast.ExceptHandler:
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        if not any(marker in ast.unparse(statement) for statement in node.body):
            continue
        for handler in node.handlers:
            if "refuse_launch" in ast.unparse(handler):
                return handler
    raise AssertionError(f"no refusal branch around {marker}")


def test_the_standby_refusal_releases_the_sdk_like_its_neighbours():
    source = inspect.getsource(mission_module.main)
    handler = _refusal_branch(source, "update_from_dict(command['params'])")
    calls = [ast.unparse(statement) for statement in handler.body]
    assert calls == [
        "refuse_launch(error)",
        "handler.cleanup()",
        "nectar.shutdown()",
        "sys.exit(EXIT_LAUNCH_REFUSED)",
    ]


def test_the_cold_start_refusal_exits_78_after_reporting():
    handler = _refusal_branch(inspect.getsource(mission_module.resolve_parameters), "validate_launch_envelope(params)")
    assert [ast.unparse(statement) for statement in handler.body] == [
        "refuse_launch(error)",
        "sys.exit(EXIT_LAUNCH_REFUSED)",
    ]


# ---------------------------------------------------------------- cold start


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"kinematics.target_altitude_m": 0.5}, "kinematics.target_altitude_m"),
        ({"kinematics.target_altitude_m": 2.45}, "kinematics.target_altitude_m"),
        ({"mission_mode": "expert"}, "mission_mode"),
        ({"platform.height_m": -1.0}, "platform.height_m"),
    ],
)
def test_a_cold_start_refuses_with_the_alert_and_exit_78(tmp_path, overrides, field):
    completed = subprocess.run(
        _command(tmp_path, _doc(**overrides), "--stages", "1"),
        cwd=tmp_path, env=_env(tmp_path), capture_output=True, text=True, timeout=120,
    )
    lines = completed.stdout.splitlines()
    assert completed.returncode == EXIT_LAUNCH_REFUSED, completed.stdout[-3000:] + completed.stderr[-3000:]
    alerts, printed = _refusals(lines)
    assert [alert["field"] for alert in alerts] == [field]
    assert alerts[0]["reason"]
    assert printed == [f"[LAUNCH_REFUSED] {field}: {alerts[0]['reason']}"]
    assert not any("[STEP " in line for line in lines)
    assert not (tmp_path / "mission_config.json").exists(), "a refused document was persisted"


def test_bug_1_1_the_station_simple_document_flies_stage_one(tmp_path):
    pytest.importorskip("rclpy")
    completed = subprocess.run(
        _command(tmp_path, _doc(), "--stages", "1"),
        cwd=tmp_path, env=_env(tmp_path), capture_output=True, text=True, timeout=180,
    )
    lines = completed.stdout.splitlines()
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
    assert _refusals(lines) == ([], [])
    assert any("[STEP 1" in line for line in lines)


# ------------------------------------------------------------------- standby


class Standby:
    def __init__(self, tmp_path, doc):
        self.process = subprocess.Popen(
            _command(tmp_path, doc, "--standby", "--stages", "1"),
            cwd=tmp_path, env=_env(tmp_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        self.lines = []
        self.ready = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.append(line.rstrip("\n"))
            if STANDBY_READY_LINE in line:
                self.ready.set()

    def go(self, doc):
        self.process.stdin.write(json.dumps({"op": "go", "params": doc, "launch_at_ms": int(time.time() * 1000)}) + "\n")
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)


@pytest.fixture
def standby(tmp_path):
    pytest.importorskip("rclpy")
    started = []

    def start(doc):
        process = Standby(tmp_path, doc)
        started.append(process)
        assert process.ready.wait(120), "standby never became ready:\n" + "\n".join(process.lines[-40:])
        return process

    yield start
    for process in started:
        process.close()


def test_a_go_outside_the_envelope_is_refused_with_the_alert_and_exit_78(standby):
    process = standby(_doc())
    process.go(_doc(**{"kinematics.target_altitude_m": 0.5}))
    assert process.process.wait(timeout=30) == EXIT_LAUNCH_REFUSED, "\n".join(process.lines[-40:])
    time.sleep(0.2)
    alerts, printed = _refusals(process.lines)
    assert [alert["field"] for alert in alerts] == ["kinematics.target_altitude_m"]
    assert len(printed) == 1
    assert not any("[STEP 1" in line for line in process.lines)
    assert not any("[MILESTONE mission.mode]" in line for line in process.lines)
