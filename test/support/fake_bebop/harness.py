"""Integration harness: the real ``mission.py`` against the driver emulator, in a private domain.

One :class:`EmulatedFlight` is one run: an :class:`~watchdog.ActuationWatchdog`
on the real driver's domain (14), ``scripts/fake_bebop_driver.py`` in a domain
of 80-99 with ``ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST``, and the mission in
the same domain, standby then ``go`` exactly as ``electron/main.cjs`` launches
it, or cold. Every mission stdout line is kept with the monotonic instant it was
read; the emulator's JSON-lines log is the ground truth. ``CLOCK_MONOTONIC`` is
system-wide, so the three timelines (test, emulator, mission) are directly
comparable.

Nothing here may publish on domain 14; the watchdog only counts publishers.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Final, Iterable, List, Optional, Sequence, Tuple

from support.fake_bebop.watchdog import ActuationWatchdog

REPO: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EMULATOR_SCRIPT: Final[str] = os.path.join(REPO, "scripts", "fake_bebop_driver.py")
MODEL_PATH: Final[str] = os.path.join(REPO, "mvp_mission_bebop", "yolov8n.pt")
DRIVER_DOMAIN: Final[int] = 14
VENV_PYTHON: Final[str] = os.path.abspath(os.path.join(REPO, "..", "..", ".venv", "bin", "python3"))
PYTHON_BIN: Final[str] = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable

STEP_LINE: Final = re.compile(r"\[STEP ([1-5]):")
MILESTONE_LINE: Final = re.compile(r"\[MILESTONE ([a-z]+\.[a-z0-9_]+)\] (\{.*\})\s*$")
ALERT_LINE: Final = re.compile(r"\[ALERT ([a-z]+\.[a-z0-9_]+)\] (\{.*\})\s*$")
STANDBY_READY: Final[str] = "[STANDBY] ready"

#: Short emulated flight: every stage runs, nothing waits longer than it must.
SHORT_FLIGHT: Final[Dict[str, Any]] = {
    "kinematics": {
        "countdown_sec": 3.0,
        "target_altitude_m": 1.4,
        "takeoff_stabilize_duration_sec": 3.0,
        "hover_duration_sec": 1.0,
    },
    "timeouts": {"search_timeout_sec": 12.0, "tracking_timeout_sec": 6.0},
    "rtl": {"timeout_sec": 15.0, "centering_timeout_sec": 6.0, "touchdown_timeout_sec": 8.0},
    "battery": {"land_pct": 20.0},
}


@dataclass
class Line:
    """One mission stdout line and the monotonic instant the harness read it."""

    mono: float
    text: str


@dataclass
class FlightResult:
    """Everything an emulated run produced."""

    exit_code: Optional[int]
    lines: List[Line]
    emulator: List[Dict[str, Any]]
    watchdog: Dict[str, Any]
    click_mono: Optional[float] = None
    click_wall_ms: Optional[float] = None
    countdown_sec: float = 0.0
    stderr: str = ""

    # ----------------------------------------------------------- mission side

    def steps(self) -> List[int]:
        return [int(m.group(1)) for line in self.lines for m in [STEP_LINE.search(line.text)] if m]

    def milestones(self) -> List[Tuple[float, str, Dict[str, Any]]]:
        found = []
        for line in self.lines:
            match = MILESTONE_LINE.search(line.text)
            if match:
                found.append((line.mono, match.group(1), json.loads(match.group(2))))
        return found

    def alerts(self) -> List[Tuple[float, str, Dict[str, Any]]]:
        found = []
        for line in self.lines:
            match = ALERT_LINE.search(line.text)
            if match:
                found.append((line.mono, match.group(1), json.loads(match.group(2))))
        return found

    def first_line(self, needle: str) -> Optional[Line]:
        return next((line for line in self.lines if needle in line.text), None)

    def tail(self, count: int = 60) -> str:
        return "\n".join(line.text for line in self.lines[-count:]) + "\n" + self.stderr[-3000:]

    # ----------------------------------------------------------- emulator side

    def commands(self, topic: str) -> List[Dict[str, Any]]:
        return [r for r in self.emulator if r["ev"] == "cmd" and r["topic"] == topic]

    def states(self) -> List[Tuple[float, int, int]]:
        return [(r["mono"], r["old"], r["new"]) for r in self.emulator if r["ev"] == "state"]

    def state_entered(self, state: int, after: float = 0.0) -> Optional[float]:
        return next((mono for mono, _old, new in self.states() if new == state and mono >= after), None)

    def nonzero_twists(self, after: float = 0.0) -> List[Dict[str, Any]]:
        return [r for r in self.commands("cmd_vel") if r["nonzero"] and r["mono"] >= after]

    def faults(self) -> List[Dict[str, Any]]:
        return [r for r in self.emulator if r["ev"] == "fault"]


@dataclass
class EmulatedFlight:
    """One emulated run. Use as a context manager, or call :meth:`close`.

    Parameters
    ----------
    workdir : str
        Scratch directory: parameter store, inference cache, evidence, logs.
    domain : int
        Private domain, 80-99.
    seed : int
        Plant seed.
    faults : sequence of str
        ``--fault`` values for the emulator.
    scenario : str
        Emulator camera scene.
    """

    workdir: str
    domain: int
    seed: int = 1
    faults: Sequence[str] = ()
    scenario: str = "accident"
    battery_start: Optional[float] = None
    camera_hz: float = 15.0
    _emulator: Optional[subprocess.Popen] = field(default=None, init=False)
    _mission: Optional[subprocess.Popen] = field(default=None, init=False)
    _lines: List[Line] = field(default_factory=list, init=False)
    _reader: Optional[threading.Thread] = field(default=None, init=False)
    _watchdog: Optional[ActuationWatchdog] = field(default=None, init=False)
    _click: Tuple[Optional[float], Optional[float]] = field(default=(None, None), init=False)
    _countdown: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if not 80 <= self.domain <= 99:
            raise ValueError(f"emulated flights run in domains 80-99, got {self.domain}")
        self.log_path = os.path.join(self.workdir, f"emulator_{self.domain}.jsonl")

    def env(self) -> Dict[str, str]:
        environ = {k: v for k, v in os.environ.items() if k != "ROS_LOCALHOST_ONLY"}
        environ.update(
            ROS_DOMAIN_ID=str(self.domain),
            ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST",
            BMG_GCS_SESSION="1",
            BMG_INFERENCE_CACHE=os.path.join(self.workdir, "inference_device.json"),
            PYTHONUNBUFFERED="1",
        )
        return environ

    # ----------------------------------------------------------- lifecycle

    def __enter__(self) -> "EmulatedFlight":
        self.start_emulator()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def start_emulator(self) -> None:
        self._watchdog = ActuationWatchdog(DRIVER_DOMAIN)
        self._watchdog.start()
        command = [PYTHON_BIN, EMULATOR_SCRIPT, "--seed", str(self.seed), "--scenario", self.scenario,
                   "--log-json", self.log_path, "--camera-hz", str(self.camera_hz)]
        for fault in self.faults:
            command += ["--fault", fault]
        if self.battery_start is not None:
            command += ["--battery-start", str(self.battery_start)]
        self._emulator = subprocess.Popen(command, env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            line = self._emulator.stdout.readline()
            if line.startswith("[FAKE_BEBOP] ready"):
                return
            if self._emulator.poll() is not None:
                break
        raise RuntimeError(f"emulator did not start: {self._emulator.stderr.read() if self._emulator.poll() is not None else ''}")

    def spawn_mission(self, params: Dict[str, Any], *, fly: bool = True, standby: bool = True,
                      extra: Iterable[str] = ()) -> None:
        """Start ``mission.py`` in the emulator's domain."""
        command = [PYTHON_BIN, "-m", "mvp_mission_bebop.mission", "--fly" if fly else "--no-fly",
                   "--config", os.path.join(self.workdir, "mission_config.json"), "--model-path", MODEL_PATH,
                   "--params-json", json.dumps(params), *extra]
        if standby:
            command.append("--standby")
        self._mission = subprocess.Popen(command, cwd=self.workdir, env=self.env(), stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._stderr_chunks: List[str] = []
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read(self) -> None:
        assert self._mission is not None and self._mission.stdout is not None
        for text in self._mission.stdout:
            self._lines.append(Line(time.monotonic(), text.rstrip("\n")))

    def _read_stderr(self) -> None:
        assert self._mission is not None and self._mission.stderr is not None
        for text in self._mission.stderr:
            self._stderr_chunks.append(text)

    def wait_for_line(self, needle: str, timeout: float) -> Optional[Line]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in list(self._lines):
                if needle in line.text:
                    return line
            if self._mission is not None and self._mission.poll() is not None and not self._reader.is_alive():
                return None
            time.sleep(0.02)
        return None

    def go(self, params: Dict[str, Any]) -> None:
        """Send the station's ``go``: the document and the click instant, read together."""
        assert self._mission is not None and self._mission.stdin is not None
        click_mono = time.monotonic()
        click_wall_ms = time.time() * 1000.0
        self._click = (click_mono, click_wall_ms)
        self._countdown = float(params.get("kinematics", {}).get("countdown_sec", 0.0))
        self._mission.stdin.write(json.dumps({"op": "go", "params": params, "launch_at_ms": click_wall_ms}) + "\n")
        self._mission.stdin.flush()

    def interrupt(self) -> float:
        """SIGINT to the mission, as ``stopMissionProcess`` sends it; returns the instant."""
        assert self._mission is not None
        sent = time.monotonic()
        self._mission.send_signal(signal.SIGINT)
        return sent

    def wait_mission(self, timeout: float) -> Optional[int]:
        assert self._mission is not None
        try:
            return self._mission.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._mission.send_signal(signal.SIGINT)
            try:
                return self._mission.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._mission.kill()
                return None

    def fly(self, params: Dict[str, Any], *, fly: bool = True, timeout: float = 240.0) -> FlightResult:
        """Standby, ``go`` on ``[STANDBY] ready``, wait for the exit, collect everything."""
        self.spawn_mission(params, fly=fly, standby=True)
        ready = self.wait_for_line(STANDBY_READY, 120.0)
        if ready is None:
            self.wait_mission(10.0)
            return self.result()
        self.go(params)
        self.wait_mission(timeout)
        return self.result()

    def result(self) -> FlightResult:
        if self._reader is not None:
            self._reader.join(timeout=5.0)
        self.stop_emulator()
        records = []
        if os.path.exists(self.log_path):
            with open(self.log_path, encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle if line.strip()]
        return FlightResult(
            exit_code=None if self._mission is None else self._mission.returncode,
            lines=list(self._lines),
            emulator=records,
            watchdog=self._watchdog.report() if self._watchdog is not None else {},
            click_mono=self._click[0],
            click_wall_ms=self._click[1],
            countdown_sec=self._countdown,
            stderr="".join(getattr(self, "_stderr_chunks", [])),
        )

    def stop_emulator(self) -> None:
        if self._emulator is not None and self._emulator.poll() is None:
            self._emulator.send_signal(signal.SIGINT)
            try:
                self._emulator.communicate(timeout=10.0)
            except subprocess.TimeoutExpired:
                self._emulator.kill()
                self._emulator.communicate()

    def close(self) -> None:
        if self._mission is not None and self._mission.poll() is None:
            self._mission.kill()
            self._mission.wait()
        self.stop_emulator()
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog = None
        subprocess.run(["ros2", "daemon", "stop"], env=self.env(), capture_output=True, timeout=20, check=False)
