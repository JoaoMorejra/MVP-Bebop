"""The command bridge's magnetometer request (6.4): start or abort, nothing else."""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

_BRIDGE = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "command_bridge.py")


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("command_bridge_magneto", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.mark.parametrize("request_, expected", [({"op": "magneto", "start": True}, True), ({"op": "magneto", "start": False}, False)])
def test_a_boolean_start_is_accepted(bridge, request_, expected):
    assert bridge.magneto_request(request_) is expected


@pytest.mark.parametrize("request_", [{"op": "magneto"}, {"op": "magneto", "start": 1}, {"op": "magneto", "start": "true"}, {"op": "land"}])
def test_anything_else_is_refused(bridge, request_):
    assert bridge.magneto_request(request_) is None


def test_the_topic_is_the_driver_subscription(bridge):
    assert bridge.TOPIC_CALIBRATE_MAGNETO.endswith("/calibrate_magneto")
