"""Structural interlocks of the driver emulator (section 4.1.5 of the 100% plan).

1. ``scripts/fake_bebop_driver.py`` refuses ``ROS_DOMAIN_ID`` 14, an absent one,
   or one outside 80-99, before anything joins a DDS domain.
2. It requires ``ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST``.
3. It exits with status 2 when a ``bebop_driver`` node already exists.
4. No emulator source can reach ``192.168.42.x``: no address, no socket library.
5. The actuation watchdog of the driver domain (``watchdog.ActuationWatchdog``)
   reports a new publisher on takeoff, cmd_vel or land.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT = os.path.join(_REPO, "scripts", "fake_bebop_driver.py")
_SUPPORT = os.path.join(_REPO, "test", "support", "fake_bebop")


def load_cli():
    spec = importlib.util.spec_from_file_location("fake_bebop_driver", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env(**overrides):
    base = {k: v for k, v in os.environ.items() if k not in ("ROS_DOMAIN_ID", "ROS_AUTOMATIC_DISCOVERY_RANGE")}
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base


# ------------------------------------------------------------ 1 and 2: environment


@pytest.mark.parametrize("domain", ["14", None, "79", "100", "abc", ""])
def test_a_refused_domain_never_starts(domain):
    cli = load_cli()
    with pytest.raises(cli.InterlockError):
        cli.check_environment(env(ROS_DOMAIN_ID=domain, ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"))


@pytest.mark.parametrize("discovery", [None, "SUBNET", "OFF", "localhost "])
def test_discovery_must_be_localhost(discovery):
    cli = load_cli()
    with pytest.raises(cli.InterlockError, match="LOCALHOST"):
        cli.check_environment(env(ROS_DOMAIN_ID="88", ROS_AUTOMATIC_DISCOVERY_RANGE=discovery))


def test_a_domain_flag_that_contradicts_the_environment_is_refused():
    cli = load_cli()
    with pytest.raises(cli.InterlockError, match="--domain"):
        cli.check_environment(env(ROS_DOMAIN_ID="88", ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"), domain_flag=89)


def test_an_accepted_environment_yields_its_domain():
    cli = load_cli()
    assert cli.check_environment(env(ROS_DOMAIN_ID="88", ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"), 88) == 88


def test_the_driver_domain_is_refused_by_the_process_before_any_rclpy_import():
    completed = subprocess.run(
        [sys.executable, "-X", "importtime", _SCRIPT, "--seed", "1"],
        env=env(ROS_DOMAIN_ID="14", ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "14" in completed.stderr
    imported = {line.rsplit("|", 1)[-1].strip() for line in completed.stderr.splitlines() if "|" in line}
    assert not {name for name in imported if name.split(".")[0] in ("rclpy", "rmw", "rcl")}


# ------------------------------------------------------------ 3: one driver per graph


def _start_emulator(domain, tmp_path, *extra):
    log = tmp_path / "emulator.jsonl"
    process = subprocess.Popen(
        [sys.executable, _SCRIPT, "--seed", "1", "--scenario", "empty", "--log-json", str(log), "--camera-hz", "2",
         *extra],
        env=env(ROS_DOMAIN_ID=str(domain), ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30.0
    line = ""
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith("[FAKE_BEBOP] ready") or process.poll() is not None:
            break
    return process, line, log


def test_a_second_emulator_in_the_same_graph_exits_with_status_two(tmp_path):
    pytest.importorskip("rclpy")
    first, ready, _ = _start_emulator(94, tmp_path)
    try:
        assert ready.startswith("[FAKE_BEBOP] ready"), first.stderr.read() if first.poll() is not None else ready
        second = subprocess.run(
            [sys.executable, _SCRIPT, "--seed", "2", "--scenario", "empty"],
            env=env(ROS_DOMAIN_ID="94", ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert second.returncode == 2
        assert "bebop_driver" in second.stderr
    finally:
        first.send_signal(signal.SIGINT)
        first.wait(timeout=10)


def test_an_emulator_runs_logs_and_stops_cleanly_on_sigint(tmp_path):
    pytest.importorskip("rclpy")
    process, ready, log = _start_emulator(95, tmp_path, "--fault", "photo_no_ack")
    assert ready.startswith("[FAKE_BEBOP] ready"), process.stderr.read() if process.poll() is not None else ready
    time.sleep(1.0)
    process.send_signal(signal.SIGINT)
    out, _err = process.communicate(timeout=15)
    assert process.returncode == 0
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["commands"] == {}
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["ev"] == "start"
    assert records[0]["faults"] == [{"kind": "photo_no_ack"}]
    assert records[-1]["ev"] == "stop"
    assert any(r["ev"] == "pub" and r["topic"] == "states/flying_state" and r["value"] == 0 for r in records)


# ------------------------------------------------------------ 4: no path to the aircraft


def _emulator_sources():
    paths = [_SCRIPT]
    for name in sorted(os.listdir(_SUPPORT)):
        if name.endswith(".py"):
            paths.append(os.path.join(_SUPPORT, name))
    return paths


@pytest.mark.parametrize("path", _emulator_sources(), ids=os.path.basename)
def test_no_emulator_source_names_the_aircraft_network_or_opens_sockets(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "192.168" not in source
    forbidden = re.findall(r"^\s*(?:import|from)\s+(socket|ftplib|urllib|requests|http|asyncio)\b", source, re.M)
    assert forbidden == []


# ------------------------------------------------------------ 5: the driver-domain watchdog


def test_the_watchdog_reports_a_new_actuation_publisher_and_nothing_else():
    pytest.importorskip("rclpy")
    import rclpy
    from std_msgs.msg import Empty

    from support.fake_bebop.watchdog import ActuationWatchdog

    domain = 96
    watchdog = ActuationWatchdog(domain)
    watchdog.start()
    try:
        time.sleep(1.5)
        assert not watchdog.breached()
        ctx = rclpy.Context()
        rclpy.init(context=ctx, domain_id=domain)
        node = rclpy.create_node("intruder", context=ctx, start_parameter_services=False)
        node.create_publisher(Empty, "/bebop/land", 1)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not watchdog.breached():
            time.sleep(0.1)
        node.destroy_node()
        rclpy.shutdown(context=ctx)
        assert watchdog.breached()
        assert watchdog.report()["new_publishers"]["/bebop/land"] >= 1
    finally:
        watchdog.stop()


def test_the_watchdog_watches_the_topics_the_bench_relay_watches():
    spec = importlib.util.spec_from_file_location("bench_relay", os.path.join(_REPO, "scripts", "bench_relay.py"))
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    from support.fake_bebop.watchdog import WATCHED_TOPICS

    assert tuple(WATCHED_TOPICS) == tuple(relay.WATCHED_SOURCE_TOPICS)
