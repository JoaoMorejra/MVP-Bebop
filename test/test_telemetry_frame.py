"""The telemetry bridge's reading of ``/bebop/odom``, against the driver's frame.

``ros2_bebop_driver`` (``BebopDriverNode::publishOdometry``) integrates the
ARSDK speed, which is NED (speedX north, speedY east, speedZ down), after
negating Y and Z, and publishes the magnetic yaw (clockwise from north)
negated. The published frame is therefore x north, y west, z up, with yaw
counter-clockwise from north -- whatever its variable names say. These tests
drive the bridge with odometry built by that exact arithmetic for flights whose
true displacement and heading are known, and pin what reaches the tactical
map: ENU metres, a compass heading, and a latitude/longitude on the right side
of the base.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

_BRIDGE = os.path.join(
    os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "telemetry_bridge.py"
)

METRES_PER_DEGREE = 111139.0
BASE = (-22.4120, -45.4600)


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("telemetry_bridge_under_test", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def driver_odometry(yaw_deg: float, north_m: float, east_m: float):
    """Pose ``publishOdometry`` reports after a straight flight.

    Returns the published ``(x, y)`` and the yaw, in degrees, that a reader of
    the published quaternion recovers with ``atan2``.
    """
    x = north_m
    y = -east_m
    yaw = -math.radians(yaw_deg)
    return (x, y), math.degrees(math.atan2(math.sin(yaw), math.cos(yaw)))


FLIGHTS = [
    # (magnetic heading, metres north, metres east)
    (90.0, 0.0, 5.0),
    (45.0, 3.0, 3.0),
    (180.0, -4.0, 0.0),
    (270.0, 0.0, -2.5),
    (0.0, 6.0, 0.0),
    (135.0, -1.5, 2.0),
]


@pytest.mark.parametrize("heading, north, east", FLIGHTS)
def test_the_odometry_frame_converts_to_east_north(bridge, heading, north, east):
    (x, y), _ = driver_odometry(heading, north, east)
    got_east, got_north = bridge.odom_to_enu(x, y)
    assert got_east == pytest.approx(east)
    assert got_north == pytest.approx(north)


@pytest.mark.parametrize("heading, north, east", FLIGHTS)
def test_the_odometry_yaw_converts_to_a_compass_heading(bridge, heading, north, east):
    _, yaw = driver_odometry(heading, north, east)
    compass = bridge.odom_yaw_to_compass(yaw)
    assert 0.0 <= compass < 360.0
    assert (compass - heading + 180.0) % 360.0 - 180.0 == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("heading, north, east", FLIGHTS)
def test_the_payload_places_the_aircraft_where_it_flew(bridge, heading, north, east):
    (x, y), yaw = driver_odometry(heading, north, east)
    state = bridge.TelemetryState()
    state.connected = True
    state.note_sample(bridge.TOPIC_ODOM)
    state.set_odom(speed=0.0, altitude=1.0, heading=yaw, position_xy=(x, y))

    payload = state.to_payload(bridge.BaseReference(BASE[0], BASE[1], "operator-cache"))

    assert payload["east_m"] == pytest.approx(east, abs=1e-3)
    assert payload["north_m"] == pytest.approx(north, abs=1e-3)
    assert (payload["heading"] - heading + 180.0) % 360.0 - 180.0 == pytest.approx(0.0, abs=0.05)

    shown_north = (payload["latitude"] - BASE[0]) * METRES_PER_DEGREE
    shown_east = (payload["longitude"] - BASE[1]) * METRES_PER_DEGREE * math.cos(
        math.radians(BASE[0])
    )
    assert shown_north == pytest.approx(north, abs=0.02)
    assert shown_east == pytest.approx(east, abs=0.02)


def test_the_raw_odometry_fields_stay_raw(bridge):
    """``odom_x_m``/``odom_y_m`` are documented as the untouched driver frame."""
    (x, y), yaw = driver_odometry(90.0, 0.0, 5.0)
    state = bridge.TelemetryState()
    state.connected = True
    state.note_sample(bridge.TOPIC_ODOM)
    state.set_odom(speed=0.0, altitude=1.0, heading=yaw, position_xy=(x, y))

    payload = state.to_payload(None)

    assert (payload["odom_x_m"], payload["odom_y_m"]) == (pytest.approx(x), pytest.approx(y))
