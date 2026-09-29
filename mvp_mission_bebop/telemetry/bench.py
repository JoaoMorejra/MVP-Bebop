"""Benchtop telemetry: the simulated airframe, published for the ground station.

Under ``--no-fly`` the :class:`KinematicSimulator` is the airframe, but its state
existed only inside the mission process. The station's telemetry bridge reads
ROS topics, so it saw no odometry at all and the cockpit showed dashes for
altitude and speed while the map marker stayed on the origin for the whole
rehearsal.

The simulated state is published on topics of its own under the mission's
namespace, never on ``/bebop/odom``. That topic belongs to the driver, and with
the aircraft powered on the bench both would otherwise write to it, so every
other subscriber -- the driver's own tooling, a recorder, this mission's next
run -- would receive two interleaved airframes.

Topic contract, mirrored by ``TOPIC_BENCH_ODOM`` and
``TOPIC_BENCH_FLYING_STATE`` in ``bebop_mission_control/streamer/telemetry_bridge.py``
and pinned by ``test_bench_telemetry``:

* ``/<ns>/mission/bench_odom`` -- ``nav_msgs/Odometry`` in the driver's odometry
  frame (x forward of the launch heading, y left, z up), world-frame twist.
* ``/<ns>/mission/bench_flying_state`` -- ``std_msgs/UInt8`` carrying the ARSDK
  flying state the airframe would report.
"""

from __future__ import annotations

import math
from typing import Any, Final

from nav_msgs.msg import Odometry
from std_msgs.msg import UInt8

from mvp_mission_bebop.actuators.simulator import SimulatedState

#: Publishing depth. The bridge only ever wants the newest sample.
BENCH_QOS_DEPTH: Final[int] = 1

#: ``frame_id`` values of the driver's own odometry, so a consumer can treat a
#: bench sample exactly as it treats a real one.
ODOM_FRAME_ID: Final[str] = "odom"
BASE_FRAME_ID: Final[str] = "base_link"


def _mission_topic(namespace: str, leaf: str) -> str:
    return f"/{namespace.strip('/')}/mission/{leaf}"


def bench_odometry_topic(namespace: str) -> str:
    """Topic carrying the simulated odometry for ``namespace``."""
    return _mission_topic(namespace, "bench_odom")


def bench_flying_state_topic(namespace: str) -> str:
    """Topic carrying the simulated flying state for ``namespace``."""
    return _mission_topic(namespace, "bench_flying_state")


def odometry_message(state: SimulatedState) -> Odometry:
    """Render a simulated sample as ``nav_msgs/Odometry``.

    Parameters
    ----------
    state : SimulatedState
        Sample from :meth:`KinematicSimulator.set_state_listener`.

    Returns
    -------
    Odometry
        Pose and world-frame twist; the header stamp is left for the caller.
    """
    msg = Odometry()
    msg.header.frame_id = ODOM_FRAME_ID
    msg.child_frame_id = BASE_FRAME_ID
    msg.pose.pose.position.x = float(state.x)
    msg.pose.pose.position.y = float(state.y)
    msg.pose.pose.position.z = float(state.z)
    # Yaw-only attitude: the simulator does not model roll or pitch.
    msg.pose.pose.orientation.z = math.sin(state.yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(state.yaw / 2.0)
    msg.twist.twist.linear.x = float(state.vx)
    msg.twist.twist.linear.y = float(state.vy)
    msg.twist.twist.linear.z = float(state.vz)
    return msg


class BenchTelemetryPublisher:
    """Publishes every simulated sample on the bench topics.

    Parameters
    ----------
    node : Any
        ROS 2 node owning the publishers (anything with ``create_publisher``).
    namespace : str
        Aircraft namespace, ``params.network.namespace``.
    clock : Any, optional
        Node clock used to stamp the odometry header; ``None`` leaves it zero.
    """

    def __init__(self, node: Any, namespace: str, clock: Any = None) -> None:
        self.odometry_topic = bench_odometry_topic(namespace)
        self.flying_state_topic = bench_flying_state_topic(namespace)
        self._odometry = node.create_publisher(Odometry, self.odometry_topic, BENCH_QOS_DEPTH)
        self._flying_state = node.create_publisher(UInt8, self.flying_state_topic, BENCH_QOS_DEPTH)
        self._clock = clock

    def publish(self, state: SimulatedState) -> None:
        """Publish one sample on both topics."""
        msg = odometry_message(state)
        if self._clock is not None:
            msg.header.stamp = self._clock.now().to_msg()
        self._odometry.publish(msg)
        self._flying_state.publish(UInt8(data=int(state.flying_state)))
