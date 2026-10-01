#!/usr/bin/env python3
"""Delivery and latency of the abort landing, measured in an isolated domain.

A probe node stands in for the driver: it subscribes to ``/bebop/land`` with the
driver's QoS (reliable, depth 1) in a private ``ROS_DOMAIN_ID`` and timestamps
every message. Each attempt then fires one landing path and records whether a
land arrived and how long after the command was issued. No aircraft and no
driver are involved; nothing leaves the private domain.

Paths:

* ``cli``: the ``LAND_PUB`` backstop alone, cold process per attempt;
* ``cli-sequence``: ``STOP_PUB`` then ``LAND_PUB``, as the station runs them;
  latency is measured to the first land;
* ``bridge``: the resident ``streamer/command_bridge.py``, one ``{"op": "land"}``
  on stdin per attempt after the bridge announced ``ready``.

The CLI strings are read from ``electron/missionLifecycle.cjs`` so the bench
always measures what ships.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_abort_latency.py --path cli --attempts 30
    python3 scripts/bench_abort_latency.py --path bridge --attempts 30
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from typing import Dict, Final, List, Optional, Sequence, Union

_ROOT: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LIFECYCLE_MODULE: Final[str] = os.path.join(_ROOT, "bebop_mission_control", "electron", "missionLifecycle.cjs")
COMMAND_BRIDGE: Final[str] = os.path.join(_ROOT, "bebop_mission_control", "streamer", "command_bridge.py")

#: Constants the bench needs from the lifecycle module.
CLI_COMMAND_NAMES: Final[tuple] = ("LAND_PUB", "STOP_PUB")

#: Time allowed after a path returns for a land still in flight to arrive, seconds.
SETTLE_SEC: Final[float] = 1.0

#: Kill timeout of one CLI command, matching ``PUB_TIMEOUT_MS`` in main.cjs, seconds.
CLI_TIMEOUT_SEC: Final[float] = 20.0

PATHS: Final[tuple] = ("cli", "cli-sequence", "bridge")


def extract_cli_commands(source: str) -> Dict[str, str]:
    """Pull the CLI command constants out of the lifecycle module source.

    Parameters
    ----------
    source : str
        Text of ``missionLifecycle.cjs``.

    Returns
    -------
    dict of str to str
        ``{"LAND_PUB": ..., "STOP_PUB": ...}``.

    Raises
    ------
    ValueError
        If either constant is absent or not a single-quoted string literal.
    """
    commands: Dict[str, str] = {}
    for name in CLI_COMMAND_NAMES:
        match = re.search(rf"const {name}\s*=\s*'([^']+)'", source)
        if match is None:
            raise ValueError(f"{name} not found as a string literal in the lifecycle module")
        commands[name] = match.group(1)
    return commands


def validate_attempts(value: Union[int, str]) -> int:
    """Validate the number of attempts.

    Raises
    ------
    TypeError
        If ``value`` is a ``bool`` or neither ``int`` nor ``str``.
    ValueError
        If ``value`` is not a positive integer.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"attempts must be int, got {type(value).__name__}")
    count = int(value) if isinstance(value, int) or value.strip().isdigit() else None
    if count is None or count <= 0:
        raise ValueError(f"attempts must be a positive integer, got {value!r}")
    return count


def summarize(latencies: Sequence[Optional[float]]) -> Dict[str, Optional[float]]:
    """Reduce per-attempt latencies (``None`` = lost) to counts and percentiles.

    Returns
    -------
    dict
        ``attempts``, ``delivered``, ``lost`` and ``p50_ms``/``p95_ms``/``max_ms``
        over the delivered attempts (``None`` when nothing was delivered).
    """
    delivered = sorted(value for value in latencies if value is not None)
    summary: Dict[str, Optional[float]] = {
        "attempts": len(latencies),
        "delivered": len(delivered),
        "lost": len(latencies) - len(delivered),
        "p50_ms": None,
        "p95_ms": None,
        "max_ms": None,
    }
    if delivered:
        ms = [value * 1000.0 for value in delivered]
        summary["p50_ms"] = statistics.median(ms)
        summary["p95_ms"] = ms[min(len(ms) - 1, int(round(0.95 * (len(ms) - 1))))]
        summary["max_ms"] = ms[-1]
    return summary


class LandProbe:
    """Driver stand-in counting ``/bebop/land`` in one private domain.

    Also subscribes to ``/bebop/cmd_vel``, as the driver does: the CLI stop
    waits for a matched subscription, and without one it would spend its whole
    ``--max-wait-time-secs`` before the land could start.
    """

    def __init__(self, domain: int) -> None:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Empty

        self._ctx = rclpy.Context()
        rclpy.init(context=self._ctx, domain_id=domain)
        self._node = rclpy.create_node("bench_land_probe", namespace="/bebop", context=self._ctx)
        self._lock = threading.Lock()
        self._stamps: List[float] = []
        self._node.create_subscription(Empty, "land", self._on_land, 1)
        self._node.create_subscription(Twist, "cmd_vel", lambda _msg: None, 1)
        self._executor = SingleThreadedExecutor(context=self._ctx)
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        try:
            while self._ctx.ok():
                self._executor.spin_once(timeout_sec=0.05)
        except Exception:  # noqa: BLE001 - shutdown races end the spin
            pass

    def _on_land(self, _msg: object) -> None:
        with self._lock:
            self._stamps.append(time.monotonic())

    def first_after(self, t0: float) -> Optional[float]:
        """Latency of the first land received after ``t0``, or ``None``."""
        with self._lock:
            later = [stamp for stamp in self._stamps if stamp >= t0]
        return (later[0] - t0) if later else None

    def close(self) -> None:
        import rclpy

        if self._ctx.ok():
            rclpy.shutdown(context=self._ctx)
        self._thread.join(timeout=2.0)


#: Longest wait for the bridge to report a land burst, seconds. Covers its
#: LAND_MATCH_WAIT_SEC with margin.
BRIDGE_EVENT_TIMEOUT_SEC: Final[float] = 8.0


def _read_bridge_event(bridge: subprocess.Popen, name: str, timeout_sec: float) -> Optional[Dict[str, object]]:
    """Read bridge stdout until a ``BMG_CMD:`` event called ``name``, or time out."""
    import select

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        ready, _, _ = select.select([bridge.stdout], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        line = bridge.stdout.readline()
        if not line:
            break
        if line.startswith("BMG_CMD:"):
            try:
                event = json.loads(line[len("BMG_CMD:"):])
            except json.JSONDecodeError:
                continue
            if event.get("event") == name:
                return event
    return None


def _run_cli(command: str, env: Dict[str, str]) -> None:
    subprocess.run(command, shell=True, env=env, timeout=CLI_TIMEOUT_SEC,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def run_bench(path: str, attempts: int, domain: int, bridge_settle_sec: float = 2.0) -> Dict[str, object]:
    """Execute ``attempts`` landings on ``path`` and return the summary.

    Raises
    ------
    ValueError
        If ``path`` is not one of :data:`PATHS`.
    RuntimeError
        If the command bridge does not announce ``ready`` within 15 s.
    """
    if path not in PATHS:
        raise ValueError(f"unknown path {path!r}; expected one of {PATHS}")
    with open(LIFECYCLE_MODULE, encoding="utf-8") as handle:
        commands = extract_cli_commands(handle.read())
    env = {**os.environ, "ROS_DOMAIN_ID": str(domain)}
    probe = LandProbe(domain)
    bridge: Optional[subprocess.Popen] = None
    latencies: List[Optional[float]] = []
    matched: List[Optional[int]] = []
    try:
        if path == "bridge":
            bridge = subprocess.Popen(
                [sys.executable, COMMAND_BRIDGE], env=env, text=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                line = bridge.stdout.readline()
                if '"ready"' in line:
                    break
            else:
                raise RuntimeError("command bridge did not announce ready")
            # Time between the bridge's `ready` and the first request. The station
            # starts the bridge at boot, so in service this is minutes; a bridge
            # just restarted by the supervisor can be asked within milliseconds.
            time.sleep(bridge_settle_sec)
        else:
            time.sleep(1.0)

        for _ in range(attempts):
            t0 = time.monotonic()
            if path == "cli":
                _run_cli(commands["LAND_PUB"], env)
            elif path == "cli-sequence":
                _run_cli(commands["STOP_PUB"], env)
                _run_cli(commands["LAND_PUB"], env)
            else:
                bridge.stdin.write(json.dumps({"op": "land"}) + "\n")
                bridge.stdin.flush()
                # The bridge may hold the burst until the publisher matches a
                # reader (LAND_MATCH_WAIT_SEC); the settle window starts once
                # it reports the burst sent, not when the request was written.
                event = _read_bridge_event(bridge, "land", BRIDGE_EVENT_TIMEOUT_SEC)
                matched.append(None if event is None else event.get("matched"))
            deadline = time.monotonic() + SETTLE_SEC
            latency = probe.first_after(t0)
            while latency is None and time.monotonic() < deadline:
                time.sleep(0.005)
                latency = probe.first_after(t0)
            latencies.append(latency)
            time.sleep(0.2)
    finally:
        if bridge is not None:
            try:
                bridge.stdin.write(json.dumps({"op": "quit"}) + "\n")
                bridge.stdin.flush()
                bridge.wait(timeout=3.0)
            except Exception:  # noqa: BLE001 - teardown only
                bridge.kill()
        probe.close()

    result: Dict[str, object] = {"path": path, "domain": domain, **summarize(latencies),
                                 "latencies_ms": [None if v is None else round(v * 1000.0, 1) for v in latencies]}
    if path == "bridge":
        result["matched"] = matched
    return result


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point. Returns 1 when any attempt was lost."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", choices=PATHS, default="cli")
    parser.add_argument("--attempts", default="30")
    parser.add_argument("--domain", type=int, default=93, help="private ROS_DOMAIN_ID (default 93)")
    parser.add_argument("--bridge-settle-sec", type=float, default=2.0,
                        help="wait between the bridge's ready and the first land (default 2.0)")
    args = parser.parse_args(argv)
    if args.domain == 14:
        raise ValueError("domain 14 is the driver domain; the bench must run isolated")
    if args.bridge_settle_sec < 0:
        raise ValueError("--bridge-settle-sec must be non-negative")
    result = run_bench(args.path, validate_attempts(args.attempts), args.domain, args.bridge_settle_sec)
    print(json.dumps(result, indent=2))
    return 1 if result["lost"] else 0


if __name__ == "__main__":
    sys.exit(main())
