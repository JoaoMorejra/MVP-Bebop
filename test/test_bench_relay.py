"""Isolated-domain sensor relay used for benchtop rehearsals with the aircraft on.

The relay is the only path between the driver's DDS domain and the domain a
``mission.py --no-fly`` rehearsal runs in. Its safety property is structural:
it subscribes to sensor topics in the driver domain and publishes them in the
rehearsal domain, and it never creates a publisher on any topic the driver
subscribes to. These tests pin that property against the driver source, so a
new driver subscription cannot silently become relayable.
"""

from __future__ import annotations

import importlib.util
import os
import re
import time

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_relay.py")
_DRIVER_NODE = os.path.join(
    os.path.dirname(__file__), "..", "..", "ros2_bebop_driver", "src", "bebop_driver_node.cpp"
)


def load_relay():
    spec = importlib.util.spec_from_file_location("bench_relay", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def driver_subscribed_topics():
    with open(_DRIVER_NODE, encoding="utf-8") as handle:
        source = handle.read()
    simple = re.findall(r'SIMPLECALLBACK\(\s*\w+\s*,\s*[\w:]+\s*,\s*"([^"]+)"', source)
    explicit = re.findall(r'create_subscription<[^>]+>\(\s*"([^"]+)"', source)
    return {"/bebop/" + name for name in simple + explicit}


# ------------------------------------------------------------ domain validation


@pytest.mark.parametrize("source, target", [(14, 77), ("14", "77"), (0, 232)])
def test_distinct_domains_in_range_are_accepted(source, target):
    relay = load_relay()
    assert relay.validate_domains(source, target) == (int(source), int(target))


def test_relaying_a_domain_onto_itself_is_refused():
    relay = load_relay()
    with pytest.raises(ValueError, match="distinct"):
        relay.validate_domains(14, 14)


@pytest.mark.parametrize("value", [-1, 233, "abc", "1.5", ""])
def test_malformed_or_out_of_range_domain_is_refused(value):
    relay = load_relay()
    with pytest.raises(ValueError):
        relay.validate_domains(value, 77)


@pytest.mark.parametrize("value", [True, None, 14.0, [14]])
def test_non_integral_domain_types_are_refused(value):
    relay = load_relay()
    with pytest.raises(TypeError):
        relay.validate_domains(value, 77)


# ------------------------------------------------------------ topic partition


def test_no_relayed_topic_is_an_actuation_topic():
    relay = load_relay()
    sensors = {topic.name for topic in relay.SENSOR_TOPICS}
    assert sensors.isdisjoint(relay.ACTUATION_TOPICS)


@pytest.mark.skipif(not os.path.exists(_DRIVER_NODE), reason="driver source not in workspace")
def test_every_driver_subscription_is_classified_as_actuation():
    relay = load_relay()
    subscribed = driver_subscribed_topics()
    assert subscribed, "driver source parse found no subscriptions"
    assert subscribed <= set(relay.ACTUATION_TOPICS)
    assert subscribed.isdisjoint({topic.name for topic in relay.SENSOR_TOPICS})


def test_relay_plan_with_an_actuation_topic_is_refused():
    relay = load_relay()
    rogue = relay.RelayTopic("/bebop/cmd_vel", "geometry_msgs/msg/Twist", "default")
    with pytest.raises(ValueError, match="/bebop/cmd_vel"):
        relay.validate_relay_plan(relay.SENSOR_TOPICS + (rogue,), relay.ACTUATION_TOPICS)


def test_every_sensor_message_type_resolves():
    relay = load_relay()
    for topic in relay.SENSOR_TOPICS:
        assert relay.resolve_message_type(topic.type_name) is not None


@pytest.mark.parametrize("name", ["sensor_msgs/Image", "not_a_pkg/msg/Nope", "std_msgs/msg/Nope"])
def test_unknown_message_type_is_refused(name):
    relay = load_relay()
    with pytest.raises(ValueError):
        relay.resolve_message_type(name)


# ------------------------------------------------------------ live isolation


def _spin_until(executor, predicate, timeout_sec):
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        executor.spin_once(timeout_sec=0.05)
        if predicate():
            return True
    return False


def test_relay_forwards_sensors_and_never_publishes_into_the_source_domain():
    rclpy = pytest.importorskip("rclpy")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile
    from std_msgs.msg import UInt8

    relay_module = load_relay()
    source_domain, target_domain = 191, 192
    relay = relay_module.SensorRelay(source_domain, target_domain)
    relay.start()

    source_ctx = rclpy.Context()
    target_ctx = rclpy.Context()
    rclpy.init(context=source_ctx, domain_id=source_domain)
    rclpy.init(context=target_ctx, domain_id=target_domain)
    try:
        driver = rclpy.create_node("fake_driver", namespace="/bebop", context=source_ctx)
        mission = rclpy.create_node("fake_mission", context=target_ctx)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        flying_pub = driver.create_publisher(UInt8, "states/flying_state", latched)
        received = []
        mission.create_subscription(
            UInt8, "/bebop/states/flying_state", lambda msg: received.append(msg.data), latched
        )
        from std_msgs.msg import Empty
        takeoff_pub = mission.create_publisher(Empty, "/bebop/takeoff", 1)

        executor = SingleThreadedExecutor(context=target_ctx)
        executor.add_node(mission)
        source_exec = SingleThreadedExecutor(context=source_ctx)
        source_exec.add_node(driver)

        def pump():
            flying_pub.publish(UInt8(data=0))
            takeoff_pub.publish(Empty())
            source_exec.spin_once(timeout_sec=0.0)
            return bool(received) and relay.discarded().get("/bebop/takeoff", 0) >= 1

        assert _spin_until(executor, pump, 10.0), "relay did not forward state and discard takeoff"
        assert received[-1] == 0

        for topic in relay_module.ACTUATION_TOPICS:
            assert driver.count_publishers(topic) == 0, topic
        assert not relay.isolation_breached()
    finally:
        relay.stop()
        rclpy.shutdown(context=source_ctx)
        rclpy.shutdown(context=target_ctx)
