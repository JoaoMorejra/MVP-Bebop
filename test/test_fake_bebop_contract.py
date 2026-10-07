"""The driver emulator honours the contract of ``ros2_bebop_driver`` (section 4.1.1).

The contract is not restated by hand: it is parsed from
``ros2_bebop_driver/src/bebop_driver_node.cpp`` -- every subscription with its
type and depth, every publisher with its type and QoS chain, and the camera
publisher -- and compared with ``support.fake_bebop.node.DRIVER_CONTRACT``. A
topic, type or QoS added to the driver fails this test until the emulator
follows. A second test reads the emulator's endpoints back from the live graph,
so the declared table and the node that runs cannot drift apart either.
"""

from __future__ import annotations

import os
import re
import threading
import time

import pytest

_DRIVER_NODE = os.path.join(
    os.path.dirname(__file__), "..", "..", "ros2_bebop_driver", "src", "bebop_driver_node.cpp"
)
_LAUNCH = os.path.join(
    os.path.dirname(__file__), "..", "..", "ros2_bebop_driver", "launch", "bebop_node_launch.xml"
)
_DOMAIN = 93

needs_driver_source = pytest.mark.skipif(not os.path.exists(_DRIVER_NODE), reason="driver source not in workspace")


def _ros_type(cpp: str) -> str:
    return cpp.replace("::", "/")


def _qos(expression: str):
    """(reliability, durability, depth) of an rclcpp QoS argument."""
    expression = expression.strip()
    if re.fullmatch(r"\d+", expression):
        return ("reliable", "volatile", int(expression))
    match = re.fullmatch(r"rclcpp::QoS\((\d+)\)((?:\.\w+\(\))*)", expression.replace(" ", ""))
    assert match, f"unparsed QoS expression {expression!r}"
    reliability, durability = "reliable", "volatile"
    for call in re.findall(r"\.(\w+)\(\)", match.group(2)):
        if call == "transient_local":
            durability = "transient_local"
        elif call == "best_effort":
            reliability = "best_effort"
        elif call == "reliable":
            reliability = "reliable"
        else:
            raise AssertionError(f"unknown QoS call {call}")
    return (reliability, durability, int(match.group(1)))


def driver_contract():
    with open(_DRIVER_NODE, encoding="utf-8") as handle:
        source = handle.read()
    contract = set()
    for cpp_type, name in re.findall(r'SIMPLECALLBACK\(\s*\w+\s*,\s*([\w:]+)\s*,\s*"([^"]+)"', source):
        # The macro subscribes with depth 1 and the default (reliable, volatile) profile.
        contract.add(("sub", name, _ros_type(cpp_type), "reliable", "volatile", 1))
    for cpp_type, name, depth in re.findall(r'create_subscription<([\w:]+)>\(\s*"([^"]+)"\s*,\s*(\d+)', source):
        contract.add(("sub", name, _ros_type(cpp_type), "reliable", "volatile", int(depth)))
    for cpp_type, name, qos in re.findall(r'create_publisher<([\w:]+)>\(\s*"([^"]+)"\s*,\s*([^;]+?)\);', source):
        contract.add(("pub", name, _ros_type(cpp_type)) + _qos(qos))
    camera = re.search(r'create_camera_publisher\(\s*this\s*,\s*"([^"]+)"\s*,\s*camera_qos\)', source)
    assert camera, "camera publisher not found in the driver"
    assert "rmw_qos_profile_t camera_qos = rmw_qos_profile_sensor_data;" in source
    assert "camera_qos.depth = 1;" in source
    base = camera.group(1)
    contract.add(("pub", base, "sensor_msgs/msg/Image", "best_effort", "volatile", 1))
    contract.add(("pub", base.rsplit("/", 1)[0] + "/camera_info", "sensor_msgs/msg/CameraInfo", "best_effort", "volatile", 1))
    return contract


@needs_driver_source
def test_the_emulator_declares_exactly_the_driver_contract():
    from support.fake_bebop.node import DRIVER_CONTRACT

    declared = {tuple(entry) for entry in DRIVER_CONTRACT}
    expected = driver_contract()
    assert declared - expected == set(), "emulator declares endpoints the driver does not have"
    assert expected - declared == set(), "driver endpoints missing from the emulator"


@needs_driver_source
def test_the_emulator_registers_under_the_driver_launch_name_and_namespace():
    from mvp_mission_bebop.actuators.driver_discovery import DRIVER_NODE_NAMES
    from support.fake_bebop.node import NAMESPACE, NODE_NAME

    with open(_LAUNCH, encoding="utf-8") as handle:
        launch = handle.read()
    assert f'name="{NODE_NAME}"' in launch
    assert 'arg name="namespace" default="bebop"' in launch
    assert NAMESPACE == "/bebop"
    assert NODE_NAME in DRIVER_NODE_NAMES


def test_every_topic_the_sdk_commands_is_one_the_emulator_receives():
    from support.fake_bebop.node import DRIVER_CONTRACT

    received = {entry[1] for entry in DRIVER_CONTRACT if entry[0] == "sub"}
    sdk_commands = {"takeoff", "land", "cmd_vel", "flip", "move_camera", "reset", "flattrim", "photo",
                    "autoflight/navigate_home"}
    assert sdk_commands <= received


class _Graph:
    """A second context in the emulator's domain that only reads the graph."""

    def __init__(self, domain):
        import rclpy

        self.ctx = rclpy.Context()
        rclpy.init(context=self.ctx, domain_id=domain)
        self.node = rclpy.create_node("contract_reader", context=self.ctx, start_parameter_services=False)

    def close(self):
        import rclpy

        self.node.destroy_node()
        rclpy.shutdown(context=self.ctx)


def _endpoint_qos(info):
    from rclpy.qos import DurabilityPolicy, ReliabilityPolicy

    profile = info.qos_profile
    reliability = "reliable" if profile.reliability == ReliabilityPolicy.RELIABLE else "best_effort"
    durability = "transient_local" if profile.durability == DurabilityPolicy.TRANSIENT_LOCAL else "volatile"
    return reliability, durability


def test_the_running_emulator_exposes_the_declared_endpoints_with_their_qos(monkeypatch):
    pytest.importorskip("rclpy")
    monkeypatch.setenv("ROS_AUTOMATIC_DISCOVERY_RANGE", "LOCALHOST")
    import rclpy

    from support.fake_bebop.node import DRIVER_CONTRACT, FakeBebopDriver
    from support.fake_bebop.plant import BebopPlant, PlantConfig

    ctx = rclpy.Context()
    rclpy.init(context=ctx, domain_id=_DOMAIN)
    driver = FakeBebopDriver(ctx, BebopPlant(PlantConfig()), camera_hz=2.0)
    driver.start()
    graph = _Graph(_DOMAIN)
    try:
        deadline = time.monotonic() + 5.0
        missing = None
        while time.monotonic() < deadline:
            missing = []
            for direction, name, type_name, reliability, durability, _depth in DRIVER_CONTRACT:
                topic = f"/bebop/{name}"
                infos = (
                    graph.node.get_publishers_info_by_topic(topic)
                    if direction == "pub"
                    else graph.node.get_subscriptions_info_by_topic(topic)
                )
                mine = [i for i in infos if i.node_name == "bebop_driver" and i.node_namespace == "/bebop"]
                if not mine or mine[0].topic_type != type_name or _endpoint_qos(mine[0]) != (reliability, durability):
                    missing.append((direction, topic))
            if not missing:
                break
            time.sleep(0.2)
        assert missing == []
    finally:
        graph.close()
        driver.stop()
        rclpy.shutdown(context=ctx)
