#!/usr/bin/env python3
"""Emulated ``ros2_bebop_driver`` for ``mission.py --fly`` without the aircraft.

Stands where the driver would -- node ``/bebop/bebop_driver``, the driver's
topics, types and QoS (``test/support/fake_bebop/node.py``) -- in a private DDS
domain, with an independent plant behind it (``plant.py``), a camera scene
(``scene.py``) and declarative faults (``faults.py``).

Interlocks (``docs/PROMPT_IMPLEMENTACAO_VOO_REAL_100.md`` 4.1.5), all checked
before any DDS participant exists except the third:

1. ``ROS_DOMAIN_ID`` must be set and in 80-99; 14, the real driver's, is refused.
2. ``ROS_AUTOMATIC_DISCOVERY_RANGE`` must be ``LOCALHOST``.
3. A ``bebop_driver`` node already in the graph: exit status 2, nothing created.
4. Nothing here names or opens a socket to the aircraft's network.

Every refusal exits with status 2. A clean run prints ``[FAKE_BEBOP] ready``
once the node is up and, after SIGINT/SIGTERM, a JSON summary line.

Usage (inside ``nectar-activate``)::

    ROS_DOMAIN_ID=88 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \\
        python3 scripts/fake_bebop_driver.py --seed 1 --scenario accident --log-json /tmp/emu.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from collections import Counter
from typing import Dict, Final, List, Mapping, Optional

_REPO: Final[str] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _path in (_REPO, os.path.join(_REPO, "test")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Exit status of every interlock refusal.
EXIT_REFUSED: Final[int] = 2
#: Discovery wait of the start-up graph scan, seconds. Measured 1.7 s to discover a
#: running emulator under LOCALHOST discovery; 3 s leaves margin under load.
GRAPH_SCAN_SEC: Final[float] = 3.0
#: The only discovery range accepted.
REQUIRED_DISCOVERY: Final[str] = "LOCALHOST"
#: Real driver domain and emulator range, kept literal here so the check runs before any import.
DRIVER_DOMAIN: Final[int] = 14
EMULATOR_DOMAINS: Final[range] = range(80, 100)


class InterlockError(RuntimeError):
    """The environment is not one the emulator may run in."""


def check_environment(environ: Mapping[str, str], domain_flag: Optional[int] = None) -> int:
    """Interlocks 1 and 2: the domain and the discovery range.

    Parameters
    ----------
    environ : Mapping
        The process environment.
    domain_flag : int, optional
        ``--domain``; when given it must equal ``ROS_DOMAIN_ID``.

    Returns
    -------
    int
        The domain to join.

    Raises
    ------
    InterlockError
        On any refused configuration.
    """
    raw = environ.get("ROS_DOMAIN_ID")
    if raw is None or not raw.strip():
        raise InterlockError("ROS_DOMAIN_ID is not set; the emulator runs only in an explicit domain 80-99")
    text = raw.strip()
    if not text.isdigit():
        raise InterlockError(f"ROS_DOMAIN_ID {raw!r} is not an integer")
    domain = int(text)
    if domain == DRIVER_DOMAIN:
        raise InterlockError(f"ROS_DOMAIN_ID {DRIVER_DOMAIN} is the real driver's domain; refused")
    if domain not in EMULATOR_DOMAINS:
        raise InterlockError(
            f"ROS_DOMAIN_ID {domain} outside the emulator range {EMULATOR_DOMAINS.start}-{EMULATOR_DOMAINS.stop - 1}"
        )
    if domain_flag is not None and domain_flag != domain:
        raise InterlockError(f"--domain {domain_flag} contradicts ROS_DOMAIN_ID {domain}")
    if environ.get("ROS_AUTOMATIC_DISCOVERY_RANGE") != REQUIRED_DISCOVERY:
        raise InterlockError(
            f"ROS_AUTOMATIC_DISCOVERY_RANGE must be {REQUIRED_DISCOVERY}, "
            f"got {environ.get('ROS_AUTOMATIC_DISCOVERY_RANGE')!r}"
        )
    return domain


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", type=int, default=None, help="must equal ROS_DOMAIN_ID when given")
    parser.add_argument("--scenario", default="accident", help="camera scene (see support.fake_bebop.scene.SCENARIOS)")
    parser.add_argument("--fault", action="append", default=[], help="fault, JSON or kind:key=value (repeatable)")
    parser.add_argument("--faults-json", default=None, help="file with a JSON list of faults")
    parser.add_argument("--seed", type=int, default=1, help="plant seed (PlantConfig.from_seed)")
    parser.add_argument("--battery-start", type=float, default=None, help="initial charge, percent")
    parser.add_argument("--camera-hz", type=float, default=30.0, help="camera frame rate")
    parser.add_argument("--compressed", action="store_true", help="also publish camera/image_raw/compressed")
    parser.add_argument("--log-json", default=None, help="JSON-lines ground-truth log")
    parser.add_argument("--max-sec", type=float, default=None, help="stop by itself after this many seconds")
    return parser


def _driver_in_graph(node) -> bool:
    from mvp_mission_bebop.actuators.driver_discovery import DRIVER_NODE_NAMES

    deadline = time.monotonic() + GRAPH_SCAN_SEC
    while True:
        names = [name for name, _ns in node.get_node_names_and_namespaces()]
        if any(name in DRIVER_NODE_NAMES for name in names):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _summary(records: List[Dict]) -> Dict:
    commands = Counter(r["topic"] for r in records if r["ev"] == "cmd")
    return {
        "commands": dict(commands),
        "states": [[r["old"], r["new"]] for r in records if r["ev"] == "state"],
        "faults": [r["kind"] for r in records if r["ev"] == "fault"],
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        domain = check_environment(os.environ, args.domain)
    except InterlockError as exc:
        print(f"[FAKE_BEBOP] refused: {exc}", file=sys.stderr, flush=True)
        return EXIT_REFUSED

    from support.fake_bebop.faults import FaultSchedule, parse_fault
    from support.fake_bebop.plant import BebopPlant, PlantConfig
    from support.fake_bebop.scene import build_scene

    try:
        faults = [parse_fault(spec) for spec in args.fault]
        if args.faults_json:
            with open(args.faults_json, encoding="utf-8") as handle:
                faults += list(FaultSchedule.from_json(handle.read()).faults)
        schedule = FaultSchedule(faults)
        config = PlantConfig.from_seed(args.seed)
        if args.battery_start is not None:
            from dataclasses import replace

            config = replace(config, battery_start_pct=args.battery_start)
        scene = build_scene(args.scenario)
    except (TypeError, ValueError, OSError) as exc:
        print(f"[FAKE_BEBOP] refused: {exc}", file=sys.stderr, flush=True)
        return EXIT_REFUSED

    import rclpy

    from support.fake_bebop.node import CommandLog, FakeBebopDriver

    context = rclpy.Context()
    rclpy.init(context=context, domain_id=domain)
    probe = rclpy.create_node("fake_bebop_probe", context=context, enable_rosout=False, start_parameter_services=False)
    occupied = _driver_in_graph(probe)
    probe.destroy_node()
    if occupied:
        print("[FAKE_BEBOP] refused: a bebop_driver node is already in the graph", file=sys.stderr, flush=True)
        rclpy.shutdown(context=context)
        return EXIT_REFUSED

    log = CommandLog(args.log_json)
    plant = BebopPlant(config, faults=schedule, start_time=time.monotonic())
    driver = FakeBebopDriver(context, plant, scene, log, camera_hz=args.camera_hz,
                             publish_compressed=args.compressed)
    driver.start(scenario=args.scenario, faults=schedule.describe())

    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    print(f"[FAKE_BEBOP] ready domain={domain} seed={args.seed} scenario={args.scenario}", flush=True)
    stop.wait(timeout=args.max_sec)

    driver.stop()
    if context.ok():
        rclpy.shutdown(context=context)
    print(json.dumps(_summary(log.records)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
