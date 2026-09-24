#!/usr/bin/env python3
"""Benchtop check of the tactical-map coordinate chain against a known flight.

The kinematic simulator only feeds the mission's own odometry supervisor, so
on the bench nothing reaches ``/bebop/odom`` and the map never moves. This
publishes ``/bebop/odom`` exactly the way ``ros2_bebop_driver`` does
(``BebopDriverNode::publishOdometry``: ARSDK NED speed and magnetic yaw, then
``vy = -vy``, ``vz = -vz``, ``yaw = -yaw`` and dead-reckoned integration at
15 Hz), for a scripted flight whose true displacement and heading are known,
runs the real ``streamer/telemetry_bridge.py`` against it on a private
``ROS_DOMAIN_ID`` and a fixed base, and compares what the bridge reports with
the truth.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_map_frame.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Tuple

#: A fixed base far from the equator, so axis and scale errors both show.
BASE: Tuple[float, float] = (-22.4120, -45.4600)

#: Metres per degree used by the bridge and by ``lib/geo.ts``.
METRES_PER_DEGREE = 111139.0

#: (name, magnetic yaw deg clockwise from north, speed north m/s, speed east m/s, seconds)
SCENARIOS: List[Tuple[str, float, float, float, float]] = [
    ("nose east, 5 m east", 90.0, 0.0, 0.5, 10.0),
    ("nose north-east, 3 m north + 3 m east", 45.0, 0.3, 0.3, 10.0),
    ("nose south, 4 m south", 180.0, -0.4, 0.0, 10.0),
]

PUBLISHER = r'''
import math, sys, time
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry

yaw_deg, speed_n, speed_e, seconds = (float(v) for v in sys.argv[1:5])
rclpy.init()
node = Node("bench_bebop_odom")
pub = node.create_publisher(Odometry, "/bebop/odom", 10)
x = y = 0.0
period = 0.066
deadline = time.monotonic() + seconds
hold_until = deadline + 4.0
while time.monotonic() < hold_until:
    moving = time.monotonic() < deadline
    # ARSDK: speedX north, speedY east, speedZ down; yaw clockwise from north.
    beb_vx, beb_vy, beb_vz = (speed_n, speed_e, 0.0) if moving else (0.0, 0.0, 0.0)
    beb_yaw = math.radians(yaw_deg)
    # ros2_bebop_driver/src/bebop_driver_node.cpp, publishOdometry.
    beb_vy = -beb_vy
    beb_vz = -beb_vz
    beb_yaw = -beb_yaw
    x += beb_vx * period
    y += beb_vy * period
    msg = Odometry()
    msg.header.frame_id = "odom"
    msg.child_frame_id = "base_link"
    msg.header.stamp = node.get_clock().now().to_msg()
    msg.pose.pose.position.x = x
    msg.pose.pose.position.y = y
    msg.pose.pose.position.z = 1.0
    msg.pose.pose.orientation.z = math.sin(beb_yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(beb_yaw / 2.0)
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.0)
    time.sleep(period)
'''


def truth(yaw_deg: float, speed_n: float, speed_e: float, seconds: float) -> Dict[str, float]:
    return {"north": speed_n * seconds, "east": speed_e * seconds, "compass": yaw_deg % 360.0}


def displayed(payload: Dict[str, object]) -> Dict[str, float]:
    """What the station draws, reproducing useTelemetry and TacticalMap."""
    lat, lng = float(payload["latitude"]), float(payload["longitude"])
    north_ll = (lat - BASE[0]) * METRES_PER_DEGREE
    east_ll = (lng - BASE[1]) * METRES_PER_DEGREE * math.cos(math.radians(BASE[0]))
    if isinstance(payload.get("east_m"), (int, float)):
        trail_east, trail_north = float(payload["east_m"]), float(payload["north_m"])
    else:
        trail_east, trail_north = float(payload["odom_x_m"]), float(payload["odom_y_m"])
    return {
        "trail_east": trail_east,
        "trail_north": trail_north,
        "latlon_east": east_ll,
        "latlon_north": north_ll,
        "compass": float(payload["heading"]) % 360.0,
    }


def run(tree: str, scenario: Tuple[str, float, float, float, float]) -> Dict[str, object]:
    name, yaw, sn, se, seconds = scenario
    env = dict(
        os.environ,
        ROS_DOMAIN_ID="79",
        BMG_BASE_LAT=str(BASE[0]),
        BMG_BASE_LNG=str(BASE[1]),
        PYTHONUNBUFFERED="1",
    )
    work = tempfile.mkdtemp(prefix="bench-map-")
    publisher = os.path.join(work, "odom.py")
    with open(publisher, "w", encoding="utf-8") as handle:
        handle.write(PUBLISHER)
    bridge = subprocess.Popen(
        [sys.executable, os.path.join(tree, "bebop_mission_control", "streamer", "telemetry_bridge.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    odom = subprocess.Popen([sys.executable, publisher, str(yaw), str(sn), str(se), str(seconds)], env=env)
    last: Dict[str, object] = {}
    deadline = time.monotonic() + seconds + 12.0
    try:
        for line in bridge.stdout:
            if line.startswith("BMG_TELEM:"):
                payload = json.loads(line[len("BMG_TELEM:"):])
                if payload.get("odom_x_m") is not None:
                    last = payload
            if odom.poll() is not None or time.monotonic() > deadline:
                break
    finally:
        for process in (odom, bridge):
            if process.poll() is None:
                process.kill()
            process.wait()
    expected = truth(yaw, sn, se, seconds)
    if not last:
        return {"scenario": name, "error": "bridge reported no odometry"}
    shown = displayed(last)
    compass_error = (shown["compass"] - expected["compass"] + 180.0) % 360.0 - 180.0
    return {
        "scenario": name,
        "truth": {k: round(v, 2) for k, v in expected.items()},
        "shown": {k: round(v, 2) for k, v in shown.items()},
        "trail_error_m": round(math.hypot(shown["trail_east"] - expected["east"], shown["trail_north"] - expected["north"]), 3),
        "latlon_error_m": round(math.hypot(shown["latlon_east"] - expected["east"], shown["latlon_north"] - expected["north"]), 3),
        "compass_error_deg": round(compass_error, 1),
    }


def main() -> int:
    tree = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    results = [run(tree, scenario) for scenario in SCENARIOS]
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
