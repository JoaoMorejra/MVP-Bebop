#!/usr/bin/env python3
"""Full bench rehearsal of the mission, without the aircraft (8.3).

Three ``mission.py --no-fly`` runs on the bundled target frame, each in a
private ``ROS_DOMAIN_ID`` with a scratch parameter store:

* ``complete``: all five stages; expects exit 0, ``[STEP 1..5]`` in order, the
  flight milestones in order and no two spoken lines overlapping (from the
  ``[SPEECH]``/``[SPEECH_DONE]`` lines of the mission's own announcer, which
  speaks aloud through the real synthesizer);
* ``abort``: SIGINT once Stage 2 starts; expects exit 3;
* ``unconfirmed``: a touchdown window too short to confirm; expects exit 4.

During ``complete`` the real telemetry bridge runs in the same domain and its
frames are written, with the mission events, to ``--trace``
(``test/fixtures/bench_rehearsal_trace.json`` by default), which
``src/lib/finishLock.rehearsal.test.ts`` replays through the Finalizar lock.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_rehearsal.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Final, List, Optional, Sequence, Tuple

_REPO: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_TRACE: Final[str] = os.path.join(_REPO, "test", "fixtures", "bench_rehearsal_trace.json")
DOMAIN: Final[int] = 91
RUN_TIMEOUT_SEC: Final[float] = 300.0
#: Slack on the end of a line before the next one counts as overlapping, ms.
OVERLAP_TOLERANCE_MS: Final[float] = 150.0

STEP_LINE = re.compile(r"\[STEP ([1-5]):")
MILESTONE_LINE = re.compile(r"\[MILESTONE ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$")
ALERT_LINE = re.compile(r"\[ALERT ([a-z]+\.[a-z0-9_]+)\] (\{.*\})$")
SPEECH_LINE = re.compile(r"\[SPEECH\] '(?P<text>.*)' latency_ms=\d+ source=\w+ priority=(?P<priority>\w+)")
SPEECH_DONE_LINE = re.compile(r"\[SPEECH_DONE\] '(?P<text>.*)' audio_ms=(?P<ms>\d+) priority=(?P<priority>\w+)")

#: Flight milestones of a complete run, in the order they must appear.
COMPLETE_MILESTONES: Final[Tuple[str, ...]] = (
    "mission.parameters",
    "mission.countdown",
    "mission.takeoff",
    "mission.scan_start",
    "mission.target_found",
    "mission.approaching",
    "mission.capture_done",
    "mission.rtl_start",
    "mission.landing",
    "mission.touchdown",
)

SHORT_RUN: Final[Dict[str, Any]] = {
    "kinematics": {"countdown_sec": 3.0, "takeoff_stabilize_duration_sec": 1.0, "hover_duration_sec": 1.0},
    "timeouts": {"search_timeout_sec": 10.0, "tracking_timeout_sec": 4.0},
    "rtl": {"timeout_sec": 4.0, "centering_timeout_sec": 3.0, "touchdown_timeout_sec": 4.0},
    "battery": {"land_pct": 20.0},
}


def _payload(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_events(lines: Sequence[Tuple[float, str]]) -> List[Dict[str, Any]]:
    """Mission events from ``(arrival seconds, line)`` pairs, in arrival order."""
    events: List[Dict[str, Any]] = []
    for t, line in lines:
        step = STEP_LINE.search(line)
        if step:
            events.append({"t": t, "kind": "step", "step": int(step.group(1))})
            continue
        milestone = MILESTONE_LINE.search(line)
        if milestone:
            events.append({"t": t, "kind": "milestone", "key": milestone.group(1), "payload": _payload(milestone.group(2))})
            continue
        alert = ALERT_LINE.search(line)
        if alert:
            events.append({"t": t, "kind": "alert", "key": alert.group(1), "payload": _payload(alert.group(2))})
            continue
        done = SPEECH_DONE_LINE.search(line)
        if done:
            events.append(
                {"t": t, "kind": "speech_done", "text": done.group("text"), "audio_ms": int(done.group("ms")), "priority": done.group("priority")}
            )
            continue
        speech = SPEECH_LINE.search(line)
        if speech:
            events.append({"t": t, "kind": "speech", "text": speech.group("text"), "priority": speech.group("priority")})
    return events


def step_order(events: Sequence[Dict[str, Any]]) -> List[int]:
    """Stage numbers entered, once each, in order."""
    order: List[int] = []
    for event in events:
        if event["kind"] == "step" and (not order or order[-1] != event["step"]):
            order.append(event["step"])
    return order


def milestones_in_order(events: Sequence[Dict[str, Any]], expected: Sequence[str]) -> bool:
    """Whether ``expected`` appears, in order, among the milestone keys."""
    keys = iter(event["key"] for event in events if event["kind"] == "milestone")
    return all(any(key == wanted for key in keys) for wanted in expected)


def speech_overlaps(events: Sequence[Dict[str, Any]], tolerance_ms: float = OVERLAP_TOLERANCE_MS) -> List[Dict[str, Any]]:
    """Lines that started while the previous one was still sounding.

    A line ends at its ``[SPEECH]`` start plus the ``audio_ms`` of the matching
    ``[SPEECH_DONE]``. An URGENT line pre-empting is by design and not counted.
    """
    starts = [event for event in events if event["kind"] == "speech"]
    lengths: Dict[str, List[int]] = {}
    for event in events:
        if event["kind"] == "speech_done":
            lengths.setdefault(event["text"], []).append(event["audio_ms"])
    overlaps: List[Dict[str, Any]] = []
    previous_end: Optional[float] = None
    previous_text: Optional[str] = None
    for start in starts:
        if previous_end is not None and start["priority"] != "URGENT":
            if start["t"] * 1000.0 < previous_end - tolerance_ms:
                overlaps.append({"first": previous_text, "second": start["text"], "overlap_ms": round(previous_end - start["t"] * 1000.0)})
        queue = lengths.get(start["text"])
        audio_ms = queue.pop(0) if queue else 0
        previous_end = start["t"] * 1000.0 + audio_ms
        previous_text = start["text"]
    return overlaps


class LineReader(threading.Thread):
    """Collects a child's stdout lines with their arrival time since ``t0``."""

    def __init__(self, stream: Any, t0: float, on_line=None) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.t0 = t0
        self.lines: List[Tuple[float, str]] = []
        self.on_line = on_line

    def run(self) -> None:
        for line in self.stream:
            stamp = time.monotonic() - self.t0
            text = line.rstrip("\n")
            self.lines.append((stamp, text))
            if self.on_line is not None:
                self.on_line(text)


def _env(scratch: str, *, station: bool) -> Dict[str, str]:
    env = {
        **os.environ,
        "ROS_DOMAIN_ID": str(DOMAIN),
        "BMG_INFERENCE_CACHE": os.path.join(scratch, "inference_device.json"),
        "BMG_SPEECH_CACHE_DIR": os.path.join(scratch, "speech"),
        "PYTHONUNBUFFERED": "1",
    }
    if station:
        env["BMG_GCS_SESSION"] = "1"
    else:
        env.pop("BMG_GCS_SESSION", None)
    return env


def run_scenario(name: str, scratch: str, params: Dict[str, Any], *, station: bool, interrupt_at_step: Optional[int] = None, telemetry: bool = False) -> Dict[str, Any]:
    """One mission run. Returns exit code, events and, with ``telemetry``, bridge frames."""
    env = _env(scratch, station=station)
    bridge: Optional[subprocess.Popen] = None
    frames: List[Dict[str, Any]] = []
    t0 = time.monotonic()
    if telemetry:
        bridge = subprocess.Popen(
            [sys.executable, os.path.join(_REPO, "bebop_mission_control", "streamer", "telemetry_bridge.py")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        def on_frame(text: str) -> None:
            if text.startswith("BMG_TELEM:"):
                payload = _payload(text[len("BMG_TELEM:"):].strip())
                frames.append(
                    {
                        "t": round(time.monotonic() - t0, 3),
                        "flying_state": payload.get("flying_state"),
                        "nav_fresh": payload.get("nav_fresh"),
                        "nav_source": payload.get("nav_source"),
                    }
                )

        LineReader(bridge.stdout, t0, on_frame).start()
        time.sleep(2.0)

    command = [
        sys.executable,
        "-m",
        "mvp_mission_bebop.mission",
        "--no-fly",
        "--stages",
        "1,2,3,4,5",
        "--config",
        os.path.join(scratch, f"{name}_config.json"),
        "--bench-frame",
        os.path.join(_REPO, "test", "fixtures", "bench_target.jpg"),
        "--params-json",
        json.dumps(params),
    ]
    mission = subprocess.Popen(command, cwd=scratch, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    interrupted = threading.Event()

    def on_line(text: str) -> None:
        step = STEP_LINE.search(text)
        if interrupt_at_step is not None and step and int(step.group(1)) == interrupt_at_step and not interrupted.is_set():
            interrupted.set()
            mission.send_signal(signal.SIGINT)

    reader = LineReader(mission.stdout, t0, on_line)
    reader.start()
    try:
        code = mission.wait(timeout=RUN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        mission.kill()
        code = None
    exit_t = round(time.monotonic() - t0, 3)
    reader.join(5.0)
    if bridge is not None:
        time.sleep(4.0)
        bridge.send_signal(signal.SIGINT)
        try:
            bridge.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            bridge.kill()
    events = parse_events(reader.lines)
    return {"name": name, "exit_code": code, "exit_t": exit_t, "events": events, "frames": frames}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the three scenarios; print the verdicts as JSON. Returns 1 on any failed expectation."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trace", default=DEFAULT_TRACE)
    args = parser.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix="bmg-rehearsal-")

    complete = run_scenario("complete", scratch, SHORT_RUN, station=False, telemetry=True)
    abort = run_scenario("abort", scratch, SHORT_RUN, station=True, interrupt_at_step=2)
    unconfirmed_params = json.loads(json.dumps(SHORT_RUN))
    unconfirmed_params["rtl"]["touchdown_timeout_sec"] = 0.05
    unconfirmed = run_scenario("unconfirmed", scratch, unconfirmed_params, station=True)

    touchdown = [e for e in unconfirmed["events"] if e["kind"] == "milestone" and e["key"] == "mission.touchdown"]
    verdicts = {
        "complete": {
            "exit_code": complete["exit_code"],
            "exit_ok": complete["exit_code"] == 0,
            "steps": step_order(complete["events"]),
            "steps_ok": step_order(complete["events"]) == [1, 2, 3, 4, 5],
            "milestones_ok": milestones_in_order(complete["events"], COMPLETE_MILESTONES),
            "spoken_lines": sum(1 for e in complete["events"] if e["kind"] == "speech"),
            "speech_overlaps": speech_overlaps(complete["events"]),
            "telemetry_frames": len(complete["frames"]),
        },
        "abort": {
            "exit_code": abort["exit_code"],
            "exit_ok": abort["exit_code"] == 3,
            "steps": step_order(abort["events"]),
        },
        "unconfirmed": {
            "exit_code": unconfirmed["exit_code"],
            "exit_ok": unconfirmed["exit_code"] == 4,
            "touchdown": touchdown[-1]["payload"] if touchdown else None,
        },
    }
    ok = (
        verdicts["complete"]["exit_ok"]
        and verdicts["complete"]["steps_ok"]
        and verdicts["complete"]["milestones_ok"]
        and not verdicts["complete"]["speech_overlaps"]
        and verdicts["abort"]["exit_ok"]
        and verdicts["unconfirmed"]["exit_ok"]
    )
    trace = {
        "generated_by": "scripts/bench_rehearsal.py",
        "exit_code": complete["exit_code"],
        "exit_t": complete["exit_t"],
        "events": [
            {k: v for k, v in e.items() if k in ("t", "kind", "step", "key")}
            for e in complete["events"]
            if e["kind"] in ("step", "milestone")
        ],
        "frames": complete["frames"],
    }
    with open(args.trace, "w", encoding="utf-8") as handle:
        json.dump(trace, handle, indent=1)
    print(json.dumps({"ok": ok, "verdicts": verdicts}, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
