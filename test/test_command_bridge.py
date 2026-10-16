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
    assert topics == ["TOPIC_CMD_VEL", "TOPIC_GOTO_STAGE", "TOPIC_LAND", "TOPIC_MOVE_CAMERA"]


def test_no_compass_calibration_command_survives(bridge):
    with open(_BRIDGE, encoding="utf-8") as handle:
        source = handle.read().lower()
    assert "magneto" not in source
    assert not hasattr(bridge, "magneto_request")
