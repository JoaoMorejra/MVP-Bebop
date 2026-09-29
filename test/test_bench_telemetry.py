"""Benchtop runs surface the simulated airframe on the cockpit HUD.

Under ``--no-fly`` the kinematic simulator is the airframe, but its state used
to live only inside ``mission.py``: nothing reached ROS, the telemetry bridge
saw no fresh ``/bebop/odom``, and the cockpit showed dashes for altitude and
speed while the map marker sat at the origin for the whole rehearsal.

The mission now publishes the simulated state on dedicated bench topics --
never on ``/bebop/odom``, which belongs to the real driver -- and the bridge
reports it as simulated navigation without claiming a driver or a link.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

from mvp_mission_bebop.actuators.simulator import (
    FLYING_STATE_FLYING,
    FLYING_STATE_HOVERING,
    FLYING_STATE_LANDED,
    FLYING_STATE_LANDING,
    FLYING_STATE_TAKINGOFF,
    KinematicSimulator,
    SimulatedState,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import CalibrationConfig, FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry import bench
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

_BRIDGE = os.path.join(
    os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "telemetry_bridge.py"
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def simulator_with_listener():
    clock = FakeClock()
    supervisor = OdometrySupervisor(
        FlightKinematicsConfig(), TimeoutsConfig(), CalibrationConfig(min_ground_samples=4)
    )
    states = []
    simulator = KinematicSimulator(supervisor, SpeedCalibration(1.0), clock=clock)
    simulator.set_state_listener(states.append)
    return simulator, clock, states


def run(simulator, clock, seconds, step=0.05):
    for _ in range(int(round(seconds / step))):
        clock.now += step
        simulator.integrate()


# ------------------------------------------------------------------ simulator


def test_simulated_flying_state_follows_the_bebop_enumeration():
    simulator, clock, states = simulator_with_listener()

    simulator.publish_initial_state()
    assert states[-1].flying_state == FLYING_STATE_LANDED

    simulator.takeoff(1.0)
    run(simulator, clock, 0.5)
    assert states[-1].flying_state == FLYING_STATE_TAKINGOFF

    run(simulator, clock, 2.0)
    assert states[-1].flying_state == FLYING_STATE_HOVERING
    assert states[-1].z == pytest.approx(1.0)

    simulator.command(0.2, 0.0, 0.0, 0.0)
    run(simulator, clock, 1.0)
    assert states[-1].flying_state == FLYING_STATE_FLYING
    assert states[-1].vx == pytest.approx(0.2)
    assert states[-1].x > 0.15

    simulator.command(0.0, 0.0, 0.0, 0.0)
    simulator.land()
    run(simulator, clock, 0.5)
    assert states[-1].flying_state == FLYING_STATE_LANDING

    run(simulator, clock, 4.0)
    assert states[-1].flying_state == FLYING_STATE_LANDED
    assert states[-1].z == 0.0


def test_a_failing_listener_never_stops_the_simulation():
    simulator, clock, _ = simulator_with_listener()

    def broken(_state):
        raise RuntimeError("publisher gone")

    simulator.set_state_listener(broken)
    simulator.takeoff(1.0)
    run(simulator, clock, 1.0)
    assert simulator.position[2] > 0.0


# ------------------------------------------------------------ bench publisher


def test_bench_topics_are_off_the_driver_namespace_paths():
    assert bench.bench_odometry_topic("bebop") == "/bebop/mission/bench_odom"
    assert bench.bench_flying_state_topic("/bebop/") == "/bebop/mission/bench_flying_state"


def test_bench_odometry_message_carries_the_simulated_pose():
    state = SimulatedState(
        x=1.5, y=-0.5, z=1.2, yaw=math.radians(30.0), vx=0.2, vy=0.0, vz=0.0,
        flying_state=FLYING_STATE_FLYING,
    )
    msg = bench.odometry_message(state)

    assert msg.pose.pose.position.x == pytest.approx(1.5)
    assert msg.pose.pose.position.y == pytest.approx(-0.5)
    assert msg.pose.pose.position.z == pytest.approx(1.2)
    q = msg.pose.pose.orientation
    yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y**2 + q.z**2))
    assert yaw == pytest.approx(math.radians(30.0))
    assert msg.twist.twist.linear.x == pytest.approx(0.2)


class RecordingPublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class RecordingNode:
    def __init__(self):
        self.publishers = {}

    def create_publisher(self, msg_type, topic, qos):
        publisher = RecordingPublisher()
        self.publishers[topic] = publisher
        return publisher


def test_bench_publisher_writes_both_topics():
    node = RecordingNode()
    publisher = bench.BenchTelemetryPublisher(node, "bebop")
    publisher.publish(
        SimulatedState(x=0.0, y=0.0, z=1.0, yaw=0.0, vx=0.0, vy=0.0, vz=0.0,
                       flying_state=FLYING_STATE_HOVERING)
    )

    assert len(node.publishers["/bebop/mission/bench_odom"].messages) == 1
    assert node.publishers["/bebop/mission/bench_flying_state"].messages[0].data == FLYING_STATE_HOVERING


# --------------------------------------------------------------------- bridge


@pytest.fixture
def bridge(monkeypatch):
    spec = importlib.util.spec_from_file_location("telemetry_bridge_bench", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    clock = {"t": 1000.0}
    monkeypatch.setattr(module, "_now", lambda: clock["t"])
    module.clock = clock
    yield module
    sys.modules.pop(spec.name, None)


def test_bridge_and_mission_agree_on_the_bench_topics(bridge):
    assert bridge.TOPIC_BENCH_ODOM == bench.bench_odometry_topic("bebop")
    assert bridge.TOPIC_BENCH_FLYING_STATE == bench.bench_flying_state_topic("bebop")


def test_fresh_bench_state_is_reported_as_simulated_navigation(bridge):
    state = bridge.TelemetryState()
    state.set_bench_flying_state(1)
    bridge.clock["t"] += 3.0
    state.set_bench_flying_state(3)
    state.set_bench(speed=0.2, altitude=1.8, heading=0.0, position_xy=(2.0, -1.0))

    payload = state.to_payload(None)

    assert payload["simulated"] is True
    assert payload["nav_source"] == "simulator"
    assert payload["nav_fresh"] is True
    assert payload["altitude"] == 1.8 and payload["speed"] == 0.2
    assert payload["east_m"] == 1.0 and payload["north_m"] == 2.0
    assert payload["flying_state"] == 3 and payload["flying_state_label"] == "flying"
    assert payload["flight_time_sec"] == 3
    # A simulation is not an aircraft: nothing claims a link or a driver.
    assert payload["connected"] is False
    assert payload["driver_running"] is False
    assert payload["data_fresh"] is False
    assert payload["gps_fix"] is False


def test_stale_bench_state_falls_back_to_the_aircraft_contract(bridge):
    state = bridge.TelemetryState()
    state.set_bench(speed=0.2, altitude=1.8, heading=0.0, position_xy=(2.0, -1.0))
    bridge.clock["t"] += bridge.ODOM_STALE_SEC + 0.5

    payload = state.to_payload(None)

    assert payload["simulated"] is False
    assert payload["nav_source"] == "none"
    assert payload["altitude"] == 0.0
    assert payload["east_m"] is None
    assert payload["flying_state"] is None


def test_aircraft_navigation_is_unchanged_without_a_simulation(bridge):
    state = bridge.TelemetryState()
    state.set_link(True, "Bebop2-A035633", -48)
    state.set_odom(speed=0.4, altitude=1.1, heading=0.0, position_xy=(1.0, 0.0))

    payload = state.to_payload(None)

    assert payload["simulated"] is False
    assert payload["nav_source"] == "aircraft"
    assert payload["altitude"] == 1.1 and payload["data_fresh"] is True
