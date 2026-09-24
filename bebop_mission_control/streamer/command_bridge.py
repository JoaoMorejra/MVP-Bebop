#!/usr/bin/env python3
"""
Resident command bridge for the Parrot Bebop 2 ground control station.

One long-lived rclpy node holding publishers on the aircraft's command topics,
driven by line-delimited JSON on stdin from `electron/main.cjs`.

It exists because the obvious alternative does not work at the latency these
commands need. A cold ``ros2 topic pub`` spends several seconds starting Python
and completing DDS discovery before it publishes anything at all. For the gimbal
that means an operator dragging the tilt control watches the camera replay the
drag whole seconds late. For the abort it means the emergency landing an
operator just demanded sits in a process that has not finished booting -- which
is the one command in the station that cannot afford to wait. A resident
participant publishes in microseconds.

Protocol, one JSON object per line on stdin::

    {"tilt": -40.0}              # degrees, negative is down
    {"tilt": -80.0, "pan": 0.0}
    {"op": "land"}               # emergency landing, published immediately
    {"op": "stop"}               # zero the velocity setpoint
    {"op": "stage", "stage": 4}  # ask the running mission to continue at stage 4
    {"op": "quit"}

Replies, one per line on stdout, prefixed so they survive anything rclpy
decides to print::

    BMG_CMD:{"event": "ready"}
    BMG_CMD:{"event": "tilt", "tilt": -40.0, "pan": 0.0}
    BMG_CMD:{"event": "land"}
"""

from __future__ import annotations

import json
import os
import sys

import rclpy
from geometry_msgs.msg import Twist, Vector3
from rclpy.node import Node
from std_msgs.msg import Empty, Int32

NAMESPACE = os.environ.get("BMG_BEBOP_NS", "/bebop")
TOPIC_MOVE_CAMERA = f"{NAMESPACE}/move_camera"
TOPIC_LAND = f"{NAMESPACE}/land"
TOPIC_CMD_VEL = f"{NAMESPACE}/cmd_vel"
TOPIC_GOTO_STAGE = f"{NAMESPACE}/mission/goto_stage"

EVENT_PREFIX = "BMG_CMD:"

# The airframe's own limits. Anything outside them is clamped rather than
# refused: a slider that silently stops at its end is correct behaviour, and a
# rejected command would leave the camera somewhere the interface is not
# showing.
TILT_MIN_DEG = -83.0
TILT_MAX_DEG = 17.0
PAN_LIMIT_DEG = 35.0

# The landing burst. ARSDK drops commands under load and a single Empty on a
# best-effort transport is not a guarantee, so the abort sends several -- it
# costs nothing and it is the difference between a landing and a hover.
LAND_REPEATS = 5


def emit(payload: dict) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps(payload) + "\n")
    sys.stdout.flush()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def main() -> int:
    rclpy.init(args=None)
    node = Node("bmg_command_bridge", start_parameter_services=False)

    # Depth 1 on the gimbal: only the newest tilt matters, and a queue of
    # superseded angles would make the camera replay a drag. The land publisher
    # keeps a deeper queue because every one of those messages matters.
    gimbal = node.create_publisher(Vector3, TOPIC_MOVE_CAMERA, 1)
    land = node.create_publisher(Empty, TOPIC_LAND, 10)
    cmd_vel = node.create_publisher(Twist, TOPIC_CMD_VEL, 10)
    goto_stage = node.create_publisher(Int32, TOPIC_GOTO_STAGE, 10)

    emit(
        {
            "event": "ready",
            "topics": {
                "gimbal": TOPIC_MOVE_CAMERA,
                "land": TOPIC_LAND,
                "cmd_vel": TOPIC_CMD_VEL,
                "stage": TOPIC_GOTO_STAGE,
            },
        }
    )

    def spin() -> None:
        # Keeps the participant serviced -- discovery and liveliness still need
        # a turn of the executor -- without ever blocking the read loop.
        rclpy.spin_once(node, timeout_sec=0.0)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(request, dict):
                continue

            op = request.get("op")

            if op == "quit":
                break

            if op == "stop":
                cmd_vel.publish(Twist())
                spin()
                emit({"event": "stop"})
                continue

            if op == "land":
                # Velocity first: an airframe still carrying a setpoint keeps
                # translating through the descent.
                cmd_vel.publish(Twist())
                for _ in range(LAND_REPEATS):
                    land.publish(Empty())
                spin()
                emit({"event": "land", "repeats": LAND_REPEATS})
                continue

            if op == "stage":
                try:
                    stage = int(request.get("stage"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= stage <= 5:
                    continue
                goto_stage.publish(Int32(data=stage))
                spin()
                emit({"event": "stage", "stage": stage})
                continue

            if "tilt" not in request:
                continue

            try:
                tilt = clamp(float(request.get("tilt", 0.0)), TILT_MIN_DEG, TILT_MAX_DEG)
                pan = clamp(float(request.get("pan", 0.0)), -PAN_LIMIT_DEG, PAN_LIMIT_DEG)
            except (TypeError, ValueError):
                continue

            message = Vector3()
            message.x = tilt
            message.y = pan
            message.z = 0.0
            gimbal.publish(message)
            spin()
            emit({"event": "tilt", "tilt": tilt, "pan": pan})
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
