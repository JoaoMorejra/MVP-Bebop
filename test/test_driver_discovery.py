"""Driver discovery in-process and the stale ``ros2`` daemon (7.2).

Reproduced live: with the driver up, ``ros2 node list`` through a stale daemon
returned nothing until ``ros2 daemon stop/start``, and the Nectar SDK's
``connect()`` depends on that command. The mission checks the graph through its
own node first and recovers the daemon once when the two disagree.
"""

from __future__ import annotations

import types

import pytest

from mvp_mission_bebop.actuators import driver_discovery
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy


class Graph:
    def __init__(self, appears_after=0, names=(("bebop_driver", "/bebop"),)):
        self.calls = 0
        self.appears_after = appears_after
        self.names = list(names)

    def get_node_names_and_namespaces(self):
        self.calls += 1
        return self.names if self.calls > self.appears_after else []


def test_the_driver_is_found_in_the_graph():
    assert driver_discovery.driver_in_graph(Graph(appears_after=2), timeout_sec=1.0, poll_sec=0.01) is True


def test_an_absent_driver_is_reported_after_the_wait():
    graph = Graph(names=())
    assert driver_discovery.driver_in_graph(graph, timeout_sec=0.05, poll_sec=0.01) is False
    assert graph.calls >= 2


def test_another_namespace_is_not_the_driver():
    graph = Graph(names=(("bebop_driver", "/other"),))
    assert driver_discovery.driver_in_graph(graph, timeout_sec=0.02, poll_sec=0.01) is False


class Drone:
    def __init__(self, answers):
        self.answers = list(answers)
        self.connects = 0

    def connect(self):
        self.connects += 1
        return self.answers.pop(0) if self.answers else False


def proxy(drone, no_fly):
    return BenchtopDroneProxy(drone, no_fly=no_fly)


def test_a_driver_the_sdk_also_sees_connects_once():
    drone = Drone([True])
    restarts = []
    assert proxy(drone, False).connect(graph_probe=lambda: True, restart_daemon=lambda: restarts.append(1)) is True
    assert drone.connects == 1 and restarts == []


def test_a_stale_daemon_is_restarted_once_and_the_connect_retried():
    drone = Drone([False, True])
    restarts = []
    assert proxy(drone, False).connect(graph_probe=lambda: True, restart_daemon=lambda: restarts.append(1)) is True
    assert drone.connects == 2 and restarts == [1]


def test_a_driver_absent_from_both_is_not_retried():
    drone = Drone([False, True])
    restarts = []
    assert proxy(drone, False).connect(graph_probe=lambda: False, restart_daemon=lambda: restarts.append(1)) is False
    assert restarts == [] and drone.connects == 1


def test_a_still_failing_sdk_after_the_restart_fails():
    drone = Drone([False, False])
    assert proxy(drone, False).connect(graph_probe=lambda: True, restart_daemon=lambda: None) is False


def test_the_bench_without_a_driver_skips_the_sdk_probe():
    drone = Drone([True])
    bench = proxy(drone, True)
    assert bench.connect(graph_probe=lambda: False, restart_daemon=lambda: None) is True
    assert drone.connects == 0
    assert bench.driver_reachable is False


def test_the_bench_with_a_driver_reports_it_reachable():
    bench = proxy(Drone([True]), True)
    assert bench.connect(graph_probe=lambda: True, restart_daemon=lambda: None) is True
    assert bench.driver_reachable is True


def test_the_restart_runs_stop_then_start(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver_discovery.subprocess, "run", run)
    driver_discovery.restart_ros2_daemon()
    assert commands == [["ros2", "daemon", "stop"], ["ros2", "daemon", "start"]]
