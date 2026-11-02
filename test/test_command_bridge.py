"""The command bridge publishes only the commands the station drives."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_BRIDGE = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "command_bridge.py")


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("command_bridge_surface", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_the_bridge_declares_only_the_station_topics(bridge):
    topics = sorted(name for name in vars(bridge) if name.startswith("TOPIC_"))
    assert topics == [
        "TOPIC_BATTERY_FAILSAFE",
        "TOPIC_CMD_VEL",
        "TOPIC_GOTO_STAGE",
        "TOPIC_LAND",
        "TOPIC_MOVE_CAMERA",
        "TOPIC_NUDGE",
    ]
    assert bridge.TOPIC_NUDGE.endswith("/mission/nudge")
    assert bridge.TOPIC_BATTERY_FAILSAFE.endswith("/mission/battery_failsafe")


def test_no_compass_calibration_command_survives(bridge):
    with open(_BRIDGE, encoding="utf-8") as handle:
        source = handle.read().lower()
    assert "magneto" not in source
    assert not hasattr(bridge, "magneto_request")


def test_the_battery_op_publishes_a_valid_json_string(bridge):
    import json

    payload = bridge.battery_payload({"op": "battery", "enabled": False, "land_pct": 35})
    assert json.loads(payload) == {"enabled": False, "land_pct": 35.0}


@pytest.mark.parametrize(
    "request_",
    [
        {"enabled": "false", "land_pct": 20},
        {"enabled": 1, "land_pct": 20},
        {"enabled": True, "land_pct": 101},
        {"enabled": True, "land_pct": float("nan")},
        {"enabled": True},
    ],
)
def test_an_invalid_battery_op_is_ignored(bridge, request_):
    assert bridge.battery_payload(request_) is None


def test_the_nudge_op_maps_to_a_twist(bridge):
    message = bridge.nudge_message({"op": "nudge", "vx": 1, "vy": 0, "vyaw": -1})
    assert (message.linear.x, message.linear.y, message.angular.z) == (1.0, 0.0, -1.0)
    assert (message.linear.z, message.angular.x, message.angular.y) == (0.0, 0.0, 0.0)


def test_a_malformed_nudge_is_saturated_or_released_without_raising(bridge):
    assert bridge.nudge_intent({"vx": 5, "vy": "a", "vyaw": float("nan")}) == (1.0, 0.0, 0.0)
    assert bridge.nudge_intent({"vx": -3, "vy": None, "vyaw": True}) == (-1.0, 0.0, 0.0)


def test_the_nudge_never_reaches_cmd_vel(bridge):
    with open(_BRIDGE, encoding="utf-8") as handle:
        source = handle.read()
    branch = source[source.index('if op == "nudge":') : source.index('if "tilt" not in request:')]
    assert "cmd_vel" not in branch
    assert "nudge.publish" in branch
