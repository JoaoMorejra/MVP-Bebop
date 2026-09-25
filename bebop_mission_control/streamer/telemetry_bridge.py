#!/usr/bin/env python3
"""
Telemetry bridge for the Parrot Bebop 2 ground control station.

Aggregates three independent sources into one line-delimited JSON stream on
stdout, consumed by `electron/main.cjs`:

1. The ROS 2 driver (`ros2_bebop_driver`), which now publishes the aircraft's
   own ARSDK state: battery percentage, Wi-Fi RSSI as measured *by the drone*,
   flying state, sonar altitude and GPS fix, alongside `/bebop/odom`.
2. The host network stack, for the SSID and the laptop-side link quality.
3. The drone's busybox console on port 23, kept only as a fallback for the
   pre-driver window where the aircraft answers but no ROS node is up yet.

It also performs the ROS 2 graph validation the GCS gates flight readiness on:
which of the essential topics exist, how many publishers each has, and how long
ago a sample last arrived. Nothing here infers a value it has not measured — a
field that was never read is reported as unknown rather than as zero.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Vector3
from sensor_msgs.msg import BatteryState, NavSatFix
from std_msgs.msg import Float32, Int16, UInt8

# -----------------------------------------------------------------------------
# Contract
# -----------------------------------------------------------------------------

NAMESPACE = os.environ.get("BMG_BEBOP_NS", "/bebop")

TOPIC_ODOM = f"{NAMESPACE}/odom"
TOPIC_IMAGE = f"{NAMESPACE}/camera/image_raw"
TOPIC_CAMERA_INFO = f"{NAMESPACE}/camera/camera_info"
TOPIC_CMD_VEL = f"{NAMESPACE}/cmd_vel"
TOPIC_TAKEOFF = f"{NAMESPACE}/takeoff"
TOPIC_LAND = f"{NAMESPACE}/land"
TOPIC_MOVE_CAMERA = f"{NAMESPACE}/move_camera"
TOPIC_BATTERY = f"{NAMESPACE}/states/battery"
TOPIC_WIFI_RSSI = f"{NAMESPACE}/states/wifi_rssi"
TOPIC_FLYING_STATE = f"{NAMESPACE}/states/flying_state"
TOPIC_ALTITUDE = f"{NAMESPACE}/states/altitude"
TOPIC_GPS = f"{NAMESPACE}/states/gps"

#: Topics the aircraft must be exchanging before the GCS clears the flight.
#: `direction` is from the driver's point of view: `out` means the driver
#: publishes it and we must see samples; `in` means the driver subscribes and we
#: only need to know the subscription exists.
ESSENTIAL_TOPICS: Tuple[Tuple[str, str], ...] = (
    (TOPIC_ODOM, "out"),
    (TOPIC_IMAGE, "out"),
    (TOPIC_CAMERA_INFO, "out"),
    (TOPIC_BATTERY, "out"),
    (TOPIC_CMD_VEL, "in"),
    (TOPIC_TAKEOFF, "in"),
    (TOPIC_LAND, "in"),
    (TOPIC_MOVE_CAMERA, "in"),
)

DRIVER_NODE_NAMES = ("bebop_driver", "bebop_driver_node")

#: The subset of `ESSENTIAL_TOPICS` this process subscribes to, and can
#: therefore prove is delivering samples rather than merely advertised.
SUBSCRIBED_TOPICS = (TOPIC_ODOM, TOPIC_BATTERY)

#: ARSDK ArdroneWithin3.PilotingState.FlyingStateChanged
FLYING_STATE_LABELS = {
    0: "landed",
    1: "takingoff",
    2: "hovering",
    3: "flying",
    4: "landing",
    5: "emergency",
    6: "usertakeoff",
    7: "motor_ramping",
    8: "emergency_landing",
}
#: States in which the airframe is off the ground.
AIRBORNE_STATES = {1, 2, 3, 4, 6, 8}

BEBOP_IP = os.environ.get("BMG_BEBOP_IP", "192.168.42.1")

#: A sample older than this is no longer evidence that the link is alive.
ODOM_STALE_SEC = 3.0
TOPIC_STALE_SEC = 4.0
#: Battery is reported on change, not on a schedule; the Bebop can go a minute
#: between percentage steps, so it needs a far more generous window.
BATTERY_STALE_SEC = 90.0

EMIT_PERIOD_SEC = 0.5
MONITOR_PERIOD_SEC = 2.0


def _now() -> float:
    return time.monotonic()


# -----------------------------------------------------------------------------
# The driver's odometry frame
# -----------------------------------------------------------------------------
#
# ``ros2_bebop_driver`` (``BebopDriverNode::publishOdometry``) takes the ARSDK
# speed, which is NED -- speedX towards north, speedY towards east, speedZ
# down -- negates Y and Z, and integrates it; it takes the ARSDK yaw, which is
# the magnetic heading clockwise from north, and negates it. Its variables are
# named ``*_enu``, but what it publishes on ``/bebop/odom`` is x north, y west,
# z up, with yaw counter-clockwise from north. Reading x as east and y as north
# rotated every flight 90 degrees clockwise on the tactical map, and passing
# the yaw on as a compass heading mirrored the nose about the north-south axis.
# These two functions are the only place that knowledge lives.


def odom_to_enu(x: float, y: float) -> Tuple[float, float]:
    """Driver odometry position to ``(east, north)`` metres."""
    return -y, x


def odom_yaw_to_compass(yaw_deg: float) -> float:
    """Driver odometry yaw to a compass heading: 0 north, clockwise, [0, 360)."""
    heading = (-yaw_deg) % 360.0
    return 0.0 if heading >= 360.0 else heading


# -----------------------------------------------------------------------------
# Shared state
# -----------------------------------------------------------------------------


class TelemetryState:
    """Thread-safe aggregate of everything the GCS is told.

    Written from the ROS executor thread, the network monitor thread and the
    emitter thread; every access goes through `_lock`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Link
        self.connected = False
        self.wifi_ssid = ""
        self.host_signal_dbm: Optional[int] = None
        self.drone_rssi_dbm: Optional[int] = None
        self.drone_ip = BEBOP_IP

        # Power
        self.battery_pct: Optional[int] = None
        self.battery_source = "none"
        self.battery_at: Optional[float] = None

        # Navigation
        self.speed = 0.0
        self.altitude = 0.0
        self.sonar_altitude: Optional[float] = None
        self.heading = 0.0
        self.position_xy = (0.0, 0.0)
        self.odom_at: Optional[float] = None

        self.flying_state: Optional[int] = None
        self.flying_state_at: Optional[float] = None

        self.gps_fix = False
        #: Last gimbal angle commanded on /bebop/move_camera, in degrees.
        #: The mission drives this itself through its stages, so watching the
        #: topic is how the station learns where the camera is actually pointing
        #: rather than where the operator last dragged a slider.
        self.camera_tilt_deg: Optional[float] = None
        self.camera_tilt_at: Optional[float] = None
        self.gps_latitude: Optional[float] = None
        self.gps_longitude: Optional[float] = None
        self.gps_home: Optional[Tuple[float, float]] = None

        self.flight_started_at: Optional[float] = None
        self.flight_time_sec = 0

        # ROS 2 graph
        self.node_present = False
        self.topics: Dict[str, Dict[str, Any]] = {}
        self.topic_samples: Dict[str, float] = {}
        #: Full graph, for the diagnostics panel. Sourced from this live rclpy
        #: node rather than the `ros2` CLI, whose daemon caches the graph it saw
        #: when it first started and reports a driver launched later as absent.
        self.graph_nodes: List[str] = []
        self.graph_topics: List[str] = []

    # -- writers -------------------------------------------------------------

    def note_sample(self, topic: str) -> None:
        with self._lock:
            self.topic_samples[topic] = _now()

    def set_odom(
        self,
        speed: float,
        altitude: float,
        heading: float,
        position_xy: Tuple[float, float],
    ) -> None:
        with self._lock:
            self.speed = speed
            self.altitude = altitude
            self.heading = heading
            self.position_xy = position_xy
            self.odom_at = _now()

    def set_battery(self, percent: Optional[int], source: str) -> None:
        with self._lock:
            if percent is None:
                # Never overwrite a good reading with a failed probe; the
                # freshness stamp is what ages it out.
                return
            self.battery_pct = int(percent)
            self.battery_source = source
            self.battery_at = _now()

    def set_drone_rssi(self, rssi: Optional[int]) -> None:
        with self._lock:
            self.drone_rssi_dbm = rssi

    def set_flying_state(self, state: Optional[int]) -> None:
        with self._lock:
            previous = self.flying_state
            self.flying_state = state
            self.flying_state_at = _now() if state is not None else None

            if state is None:
                return
            airborne = state in AIRBORNE_STATES
            if airborne and self.flight_started_at is None:
                self.flight_started_at = _now()
            elif not airborne and previous in AIRBORNE_STATES:
                # Touchdown: freeze the elapsed time, then clear it so the next
                # flight starts from zero instead of accumulating forever.
                self.flight_started_at = None
                self.flight_time_sec = 0

    def set_sonar_altitude(self, altitude: Optional[float]) -> None:
        with self._lock:
            self.sonar_altitude = altitude

    def set_gps(self, fix: bool, latitude: float, longitude: float) -> None:
        with self._lock:
            self.gps_fix = fix
            self.gps_latitude = latitude if fix else None
            self.gps_longitude = longitude if fix else None
            if fix and self.gps_home is None:
                # The first real fix is where the aircraft was switched on,
                # which is the launch point for every flight of this session.
                self.gps_home = (latitude, longitude)

    def set_link(
        self,
        connected: bool,
        ssid: Optional[str],
        host_signal_dbm: Optional[int],
    ) -> None:
        with self._lock:
            self.connected = connected
            if ssid is not None:
                self.wifi_ssid = ssid
            self.host_signal_dbm = host_signal_dbm

    def set_graph(
        self,
        node_present: bool,
        topics: Dict[str, Dict[str, Any]],
        graph_nodes: List[str],
        graph_topics: List[str],
    ) -> None:
        with self._lock:
            self.node_present = node_present
            self.topics = topics
            self.graph_nodes = graph_nodes
            self.graph_topics = graph_topics

    # -- readers -------------------------------------------------------------

    def battery_is_live_from_aircraft(self) -> bool:
        """True while the ARSDK battery value is recent enough to trust.

        The ARSDK value arrives through the driver, the same pipe as the
        odometry, so it is only live while the odometry is. Without that
        condition a dead driver left its last charge standing for the full
        ``BATTERY_STALE_SEC`` and held off the console probe, which reads the
        charge over Wi-Fi independently of the driver.
        """
        with self._lock:
            if self.battery_source != "aircraft" or self.battery_at is None:
                return False
            now = _now()
            odom_fresh = self.odom_at is not None and now - self.odom_at < ODOM_STALE_SEC
            return odom_fresh and now - self.battery_at < BATTERY_STALE_SEC

    def odom_age(self) -> Optional[float]:
        with self._lock:
            return None if self.odom_at is None else _now() - self.odom_at

    def snapshot_sample_ages(self) -> Dict[str, float]:
        with self._lock:
            now = _now()
            return {t: now - at for t, at in self.topic_samples.items()}

    def set_camera_tilt(self, tilt_deg: float) -> None:
        with self._lock:
            self.camera_tilt_deg = tilt_deg
            self.camera_tilt_at = _now()

    def to_payload(self, base: Optional["BaseReference"]) -> Dict[str, Any]:
        """Render the wire format consumed by `main.cjs` and `types/bmg.ts`.

        ``base`` is the real reference the odometry is projected onto when the
        aircraft has no GPS fix -- the operator's own position, cached by the
        station while it still had internet. With no base at all, latitude and
        longitude are reported as zero with ``base_known`` false, so the map
        draws the local grid instead of placing the flight somewhere invented.
        """
        with self._lock:
            now = _now()

            odom_age = None if self.odom_at is None else now - self.odom_at
            odom_fresh = odom_age is not None and odom_age < ODOM_STALE_SEC

            battery_age = (
                None if self.battery_at is None else now - self.battery_at
            )
            battery_fresh = (
                battery_age is not None and battery_age < BATTERY_STALE_SEC
            )
            battery_known = self.battery_pct is not None and battery_fresh

            if self.flight_started_at is not None:
                self.flight_time_sec = int(now - self.flight_started_at)

            # The drone's own RSSI describes the link that actually matters;
            # the host counter is the fallback when the driver is not up yet.
            if self.drone_rssi_dbm is not None and self.drone_rssi_dbm != 0:
                signal_dbm = self.drone_rssi_dbm
                signal_source = "aircraft"
            elif self.host_signal_dbm is not None:
                signal_dbm = self.host_signal_dbm
                signal_source = "host"
            else:
                signal_dbm = -100
                signal_source = "none"

            # Position: a real GPS fix wins; otherwise project the odometry
            # offset onto the real base reference, when there is one.
            dx, dy = self.position_xy
            east, north = odom_to_enu(dx, dy)
            if self.gps_fix and self.gps_latitude is not None:
                latitude = round(self.gps_latitude, 7)
                longitude = round(self.gps_longitude or 0.0, 7)
                position_source = "gps"
            elif base is not None:
                d_lat = north / 111139.0
                d_lon = east / (111139.0 * max(1e-6, math.cos(math.radians(base.latitude))))
                latitude = round(base.latitude + d_lat, 7)
                longitude = round(base.longitude + d_lon, 7)
                position_source = "odometry"
            else:
                latitude = 0.0
                longitude = 0.0
                position_source = "odometry"

            if self.gps_home is not None:
                base_payload = {
                    "base_known": True,
                    "base_latitude": round(self.gps_home[0], 7),
                    "base_longitude": round(self.gps_home[1], 7),
                    "base_source": "gps",
                }
            elif base is not None:
                base_payload = {
                    "base_known": True,
                    "base_latitude": round(base.latitude, 7),
                    "base_longitude": round(base.longitude, 7),
                    "base_source": base.source,
                }
            else:
                base_payload = {
                    "base_known": False,
                    "base_latitude": None,
                    "base_longitude": None,
                    "base_source": "none",
                }

            topics = {
                name: dict(info) for name, info in self.topics.items()
            }
            required_out = [
                name
                for name, direction in ESSENTIAL_TOPICS
                if direction == "out"
            ]
            required_in = [
                name for name, direction in ESSENTIAL_TOPICS if direction == "in"
            ]
            topics_ready = self.node_present and all(
                topics.get(name, {}).get("receiving", False)
                for name in required_out
            ) and all(
                topics.get(name, {}).get("present", False)
                for name in required_in
            )

            # Nothing measured on the airframe survives the airframe being out
            # of reach. Every value below is the last one the drone reported,
            # and none of them expires on its own -- so an unreachable aircraft
            # kept publishing its final charge, altitude and speed as though it
            # were still flying. `wifi_ssid` is worse than stale: it holds the
            # network the *host* joined, which is some other network entirely
            # once the operator has left the drone's.
            reachable = bool(self.connected)
            # `reachable` is a ping to the aircraft's access point, which keeps
            # answering while the driver is dead or wedged; it says the host is
            # on the drone's network, not that anything is coming out of it.
            # Flight data is gated on the stricter fact that the driver is
            # actually publishing, as the position fields always were. The
            # console battery probe is exempt: it is read over Wi-Fi, not
            # through the driver, and ages out on its own window.
            data_fresh = odom_fresh and reachable
            battery_known = (
                battery_known
                and reachable
                and (self.battery_source != "aircraft" or odom_fresh)
            )

            return {
                # -- legacy contract, unchanged shape ------------------------
                "connected": reachable,
                "driver_running": bool(data_fresh and self.node_present),
                "battery_pct": int(self.battery_pct) if battery_known else 0,
                "wifi_ssid": self.wifi_ssid if data_fresh else "",
                "wifi_signal_dbm": int(signal_dbm) if data_fresh else -100,
                "speed": round(self.speed, 2) if data_fresh else 0.0,
                "altitude": round(self.altitude, 2) if data_fresh else 0.0,
                "flight_time_sec": int(self.flight_time_sec) if reachable else 0,
                "heading": round(odom_yaw_to_compass(self.heading), 1) if data_fresh else 0.0,
                "latitude": latitude,
                "longitude": longitude,
                # -- additions ----------------------------------------------
                "at": int(time.time() * 1000),
                "drone_ip": self.drone_ip,
                "battery_known": bool(battery_known),
                "battery_source": self.battery_source if battery_known else "none",
                "battery_age_sec": (
                    round(battery_age, 1) if battery_age is not None else None
                ),
                "signal_source": signal_source,
                "position_source": position_source,
                "gps_fix": bool(self.gps_fix) and data_fresh,
                # Raw /bebop/odom position, metres in the odometry frame. The
                # station draws the trail from these, never from a latitude
                # that may have switched between GPS and projection mid-flight.
                "odom_x_m": round(dx, 3) if data_fresh else None,
                "odom_y_m": round(dy, 3) if data_fresh else None,
                # The same position in the frame the map draws in: metres east
                # and north of the odometry origin. See `odom_to_enu`.
                "east_m": round(east, 3) if data_fresh else None,
                "north_m": round(north, 3) if data_fresh else None,
                **base_payload,
                # Where the camera is actually pointing, and how long ago that
                # was commanded. Null until something has commanded it at all:
                # the station must not draw a gimbal angle it has not observed.
                "camera_tilt_deg": self.camera_tilt_deg if reachable else None,
                "camera_tilt_age_sec": (
                    None if self.camera_tilt_at is None else now - self.camera_tilt_at
                ),
                "sonar_altitude": (
                    round(self.sonar_altitude, 2)
                    if self.sonar_altitude is not None
                    and not math.isnan(self.sonar_altitude)
                    else None
                ),
                "flying_state": self.flying_state if data_fresh else None,
                "flying_state_label": (
                    FLYING_STATE_LABELS.get(
                        self.flying_state if self.flying_state is not None else -1,
                        "unknown",
                    )
                    if data_fresh
                    else "disconnected"
                ),
                "data_fresh": bool(data_fresh),
                "odom_age_sec": (
                    round(odom_age, 2) if odom_age is not None else None
                ),
                "node_present": bool(self.node_present),
                "topics": topics,
                "topics_ready": bool(topics_ready),
                "graph": {
                    "nodes": list(self.graph_nodes),
                    "topics": list(self.graph_topics),
                },
            }


STATE = TelemetryState()


# -----------------------------------------------------------------------------
# ROS 2 node
# -----------------------------------------------------------------------------


class TelemetryNode(Node):
    """Subscribes to everything the driver exposes and validates the graph."""

    def __init__(self) -> None:
        super().__init__("bmg_telemetry_bridge")

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        # The state topics are latched by the driver so a bridge that starts
        # after the driver still gets the current battery immediately.
        state_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self.create_subscription(Odometry, TOPIC_ODOM, self._on_odom, sensor_qos)
        self.create_subscription(
            BatteryState, TOPIC_BATTERY, self._on_battery, state_qos
        )
        self.create_subscription(
            Int16, TOPIC_WIFI_RSSI, self._on_wifi_rssi, state_qos
        )
        self.create_subscription(
            UInt8, TOPIC_FLYING_STATE, self._on_flying_state, state_qos
        )
        self.create_subscription(
            Float32, TOPIC_ALTITUDE, self._on_altitude, state_qos
        )
        self.create_subscription(NavSatFix, TOPIC_GPS, self._on_gps, state_qos)
        # The gimbal topic is an input to the driver, so this bridge is a second
        # subscriber on it rather than the consumer. That is the point: whatever
        # commands the camera -- the mission's own stages, or the station's
        # slider -- passes through here and the interface can follow it.
        self.create_subscription(
            Vector3, TOPIC_MOVE_CAMERA, self._on_move_camera, state_qos
        )

        self.base = BaseResolver()

        # Graph validation runs on the executor so it never blocks the emitter.
        self.create_timer(1.0, self._inspect_graph)

    # -- subscriptions -------------------------------------------------------

    def _on_odom(self, msg: Odometry) -> None:
        STATE.note_sample(TOPIC_ODOM)

        linear = msg.twist.twist.linear
        speed = math.sqrt(linear.x**2 + linear.y**2 + linear.z**2)

        position = msg.pose.pose.position
        altitude = abs(float(position.z))

        orientation = msg.pose.pose.orientation
        siny_cosp = 2.0 * (
            orientation.w * orientation.z + orientation.x * orientation.y
        )
        cosy_cosp = 1.0 - 2.0 * (orientation.y**2 + orientation.z**2)
        heading = math.degrees(math.atan2(siny_cosp, cosy_cosp))

        STATE.set_odom(
            speed=float(speed),
            altitude=altitude,
            heading=float(heading),
            position_xy=(float(position.x), float(position.y)),
        )

    def _on_battery(self, msg: BatteryState) -> None:
        STATE.note_sample(TOPIC_BATTERY)
        if not msg.present or math.isnan(msg.percentage):
            return
        STATE.set_battery(int(round(msg.percentage * 100.0)), "aircraft")

    def _on_wifi_rssi(self, msg: Int16) -> None:
        STATE.note_sample(TOPIC_WIFI_RSSI)
        STATE.set_drone_rssi(int(msg.data) if msg.data != 0 else None)

    def _on_flying_state(self, msg: UInt8) -> None:
        STATE.note_sample(TOPIC_FLYING_STATE)
        STATE.set_flying_state(int(msg.data) if msg.data != 255 else None)

    def _on_altitude(self, msg: Float32) -> None:
        STATE.note_sample(TOPIC_ALTITUDE)
        value = float(msg.data)
        STATE.set_sonar_altitude(None if math.isnan(value) else value)

    def _on_gps(self, msg: NavSatFix) -> None:
        STATE.note_sample(TOPIC_GPS)
        latitude = float(msg.latitude)
        longitude = float(msg.longitude)
        STATE.set_gps(
            fix=msg.status.status >= 0 and _valid_coordinate(latitude, longitude),
            latitude=latitude,
            longitude=longitude,
        )

    def _on_move_camera(self, msg: Vector3) -> None:
        STATE.note_sample(TOPIC_MOVE_CAMERA)
        STATE.set_camera_tilt(float(msg.x))

    # -- graph validation ----------------------------------------------------

    def _inspect_graph(self) -> None:
        """Answer "is the aircraft actually talking to us" deterministically.

        A node name alone is not evidence: the driver advertises every topic in
        its constructor, before the ARSDK has produced a single sample. So for
        the topics the driver publishes we require a sample to have arrived
        recently, and for the ones it subscribes to we require the subscription
        to exist in the graph.
        """
        try:
            discovered = self.get_node_names_and_namespaces()
        except Exception:  # pragma: no cover - rclpy transport hiccup
            discovered = []
        node_names = {name for name, _ns in discovered}
        node_present = any(name in node_names for name in DRIVER_NODE_NAMES)

        graph_nodes = sorted(
            f"{namespace.rstrip('/')}/{name}" for name, namespace in discovered
        )
        try:
            graph_topics = sorted(
                name for name, _types in self.get_topic_names_and_types()
            )
        except Exception:  # pragma: no cover
            graph_topics = []

        ages = STATE.snapshot_sample_ages()
        topics: Dict[str, Dict[str, Any]] = {}

        for name, direction in ESSENTIAL_TOPICS:
            try:
                publishers = self.count_publishers(name)
                subscribers = self.count_subscribers(name)
            except Exception:  # pragma: no cover
                publishers = subscribers = 0

            age = ages.get(name)
            if direction == "out":
                present = publishers > 0
                if name in SUBSCRIBED_TOPICS:
                    # We hold a subscription, so "receiving" means a sample
                    # actually arrived, not merely that someone advertises it.
                    window = (
                        BATTERY_STALE_SEC
                        if name == TOPIC_BATTERY
                        else TOPIC_STALE_SEC
                    )
                    receiving = age is not None and age < window
                else:
                    # Image and camera_info belong to the MJPEG bridge; the
                    # frame counter on port 9090 is what proves they flow, and
                    # Electron combines the two signals. Here we can only
                    # attest that the driver is advertising them.
                    receiving = present
            else:
                present = subscribers > 0
                receiving = present

            topics[name] = {
                "direction": direction,
                "publishers": int(publishers),
                "subscribers": int(subscribers),
                "present": bool(present),
                "receiving": bool(receiving),
                "age_sec": round(age, 2) if age is not None else None,
            }

        STATE.set_graph(node_present, topics, graph_nodes, graph_topics)


def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _valid_coordinate(latitude: float, longitude: float) -> bool:
    """Whether a latitude/longitude pair is a position at all.

    The Bebop publishes 500.0 for both while it has no fix, and zero is what an
    uninitialised receiver reports; neither may reach the map as a place.
    """
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return False
    if abs(latitude) > 90.0 or abs(longitude) > 180.0:
        return False
    return abs(latitude) > 1e-6 or abs(longitude) > 1e-6


class BaseReference:
    """A real coordinate the odometry is projected onto."""

    def __init__(self, latitude: float, longitude: float, source: str) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.source = source


class BaseResolver:
    """Where the base is, re-read while the bridge runs.

    ``BMG_BASE_FILE`` is the station's geolocation cache (``main.cjs`` writes it
    while the host still has internet), re-read every few seconds so a position
    resolved after this bridge started is picked up without a restart. The
    ``BMG_BASE_LAT``/``BMG_BASE_LNG`` pair is an explicit override. There is no
    hard-coded city: without either, the base is unknown and said to be.
    """

    RELOAD_SEC = 5.0

    def __init__(self) -> None:
        self._path = os.environ.get("BMG_BASE_FILE", "")
        self._checked_at = 0.0
        self._mtime: Optional[float] = None
        self._cached: Optional[BaseReference] = None
        lat = _env_float("BMG_BASE_LAT")
        lng = _env_float("BMG_BASE_LNG")
        self._override = (
            BaseReference(lat, lng, "configured")
            if lat is not None and lng is not None and _valid_coordinate(lat, lng)
            else None
        )

    def current(self) -> Optional[BaseReference]:
        if self._override is not None:
            return self._override
        now = _now()
        if self._path and now - self._checked_at >= self.RELOAD_SEC:
            self._checked_at = now
            try:
                mtime = os.path.getmtime(self._path)
                if mtime != self._mtime:
                    self._mtime = mtime
                    with open(self._path, "r", encoding="utf-8") as stream:
                        data = json.load(stream)
                    lat = float(data.get("latitude"))
                    lng = float(data.get("longitude"))
                    self._cached = (
                        BaseReference(lat, lng, "operator-cache")
                        if _valid_coordinate(lat, lng)
                        else None
                    )
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        return self._cached


# -----------------------------------------------------------------------------
# Host network probes
# -----------------------------------------------------------------------------


def _run(argv: List[str], env: Dict[str, str], timeout: float) -> str:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        return completed.stdout if completed.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def active_wifi_device(env: Dict[str, str]) -> Tuple[str, str]:
    """Return `(device, ssid)` for the connected wireless interface.

    Reading `/proc/net/wireless` top-down and taking the first row is what made
    the old bridge report a phantom -100 dBm: on a host with a second radio or a
    p2p interface the first row is not the one carrying the Bebop link.
    """
    out = _run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
        env,
        1.5,
    )
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        device, kind, state, connection = parts[0], parts[1], parts[2], parts[3]
        if kind == "wifi" and state.lower().startswith("connected"):
            return device.strip(), connection.strip()

    # nmcli unavailable or not managing the radio: fall back to iwgetid.
    device = _run(["iwgetid"], env, 1.0).split(" ", 1)[0].strip()
    ssid = _run(["iwgetid", "-r"], env, 1.0).strip()
    return device, ssid


def host_signal_dbm(device: str) -> Optional[int]:
    """Signal level in dBm for `device`, or None when it cannot be read."""
    if not device:
        return None
    try:
        with open("/proc/net/wireless", "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip().startswith(f"{device}:"):
                    continue
                columns = line.split()
                if len(columns) < 4:
                    return None
                try:
                    return int(float(columns[3].rstrip(".")))
                except ValueError:
                    return None
    except OSError:
        return None
    return None


def has_bebop_route(env: Dict[str, str], ip: str) -> bool:
    prefix = ip.rsplit(".", 1)[0] + "."
    return prefix in _run(["ip", "route"], env, 1.0)


def ping(ip: str, env: Dict[str, str]) -> bool:
    try:
        completed = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            env=env,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# -----------------------------------------------------------------------------
# Battery over the drone's console — fallback only
# -----------------------------------------------------------------------------

_IAC = 255
_DO, _DONT, _WILL, _WONT, _SB, _SE = 253, 254, 251, 252, 250, 240

_BATTERY_PATHS = (
    "/sys/class/power_supply/battery/capacity",
    "/sys/class/power_supply/bcm_battery/capacity",
    "/sys/class/power_supply/ltc2943/capacity",
)
#: A marker the drone echoes back around the value, so the percentage is never
#: confused with a version number in the login banner.
_MARK = "BMGBAT"


def _strip_telnet_negotiation(chunk: bytes) -> bytes:
    """Answer IAC option negotiation and return only the payload bytes.

    The old implementation opened a raw socket and read 512 bytes 300 ms later,
    which on a busybox telnetd yields the option negotiation and the login
    banner, not the command output. Any digit in that banner then became the
    "battery level".
    """
    out = bytearray()
    index = 0
    while index < len(chunk):
        byte = chunk[index]
        if byte != _IAC:
            out.append(byte)
            index += 1
            continue
        if index + 1 >= len(chunk):
            break
        command = chunk[index + 1]
        if command in (_DO, _DONT, _WILL, _WONT):
            index += 3
        elif command == _SB:
            end = chunk.find(bytes([_IAC, _SE]), index)
            index = len(chunk) if end == -1 else end + 2
        else:
            index += 2
    return bytes(out)


def _negotiate(sock: socket.socket, chunk: bytes) -> None:
    """Refuse every offered option so the server stops waiting on us."""
    reply = bytearray()
    index = 0
    while index + 2 < len(chunk) + 1:
        if index >= len(chunk):
            break
        if chunk[index] != _IAC or index + 2 >= len(chunk) + 1:
            index += 1
            continue
        command = chunk[index + 1]
        if command in (_DO, _DONT):
            reply += bytes([_IAC, _WONT, chunk[index + 2]])
            index += 3
        elif command in (_WILL, _WONT):
            reply += bytes([_IAC, _DONT, chunk[index + 2]])
            index += 3
        else:
            index += 2
    if reply:
        try:
            sock.sendall(bytes(reply))
        except OSError:
            pass


def query_bebop_battery(ip: str, timeout: float = 3.0) -> Optional[int]:
    """Read the battery percentage from the Bebop's console.

    Only used until the ROS driver is up — once `/bebop/states/battery` is
    flowing, the ARSDK value supersedes this entirely.
    """
    command = (
        f"for f in {' '.join(_BATTERY_PATHS)}; do "
        f"[ -r $f ] && echo {_MARK}$(cat $f){_MARK} && break; done; "
        f"echo {_MARK}END{_MARK}\n"
    ).encode("ascii")

    deadline = time.monotonic() + timeout
    buffer = bytearray()
    try:
        with socket.create_connection((ip, 23), timeout=timeout) as sock:
            sock.settimeout(0.5)

            # Drain and answer the option negotiation, then send the command.
            sent = False
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    chunk = b""
                except OSError:
                    break

                if chunk:
                    _negotiate(sock, chunk)
                    buffer += _strip_telnet_negotiation(chunk)

                text = buffer.decode("utf-8", errors="ignore")
                if f"{_MARK}END{_MARK}" in text:
                    break
                if not sent and (chunk == b"" or b"#" in chunk or b"$" in chunk):
                    try:
                        sock.sendall(command)
                    except OSError:
                        break
                    sent = True
    except (OSError, socket.timeout):
        return None

    match = re.search(
        rf"{_MARK}(\d{{1,3}}){_MARK}", buffer.decode("utf-8", errors="ignore")
    )
    if not match:
        return None
    value = int(match.group(1))
    return value if 0 <= value <= 100 else None


# -----------------------------------------------------------------------------
# Monitor thread
# -----------------------------------------------------------------------------


def network_monitor(stop: threading.Event) -> None:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    consecutive_failures = 0
    last_telnet_attempt = 0.0

    while not stop.is_set():
        try:
            device, ssid = active_wifi_device(env)
            signal = host_signal_dbm(device)
            routed = has_bebop_route(env, BEBOP_IP)

            on_bebop_net = bool(
                routed or (ssid and re.search(r"bebop", ssid, re.IGNORECASE))
            )
            if not ssid and routed:
                ssid = "Parrot Bebop 2"

            odom_age = STATE.odom_age()
            odom_alive = odom_age is not None and odom_age < ODOM_STALE_SEC

            reachable = odom_alive or (
                on_bebop_net and ping(BEBOP_IP, env)
            )

            if reachable:
                consecutive_failures = 0
                connected = True
            elif on_bebop_net:
                # Tolerate two dropped cycles before declaring the link down;
                # a single lost ping on a saturated 2.4 GHz channel is normal.
                consecutive_failures += 1
                connected = consecutive_failures < 3
            else:
                consecutive_failures = 3
                connected = False

            STATE.set_link(connected, ssid or None, signal)

            # Console battery only while the aircraft answers and the ARSDK
            # value has not arrived; it is a slow, blocking probe.
            battery_from_aircraft = STATE.battery_is_live_from_aircraft()
            now = _now()
            if (
                connected
                and not battery_from_aircraft
                and now - last_telnet_attempt > 10.0
            ):
                last_telnet_attempt = now
                STATE.set_battery(query_bebop_battery(BEBOP_IP), "console")
        except Exception as error:  # pragma: no cover - defensive
            print(f"[telemetry] monitor cycle failed: {error}", file=sys.stderr)

        stop.wait(MONITOR_PERIOD_SEC)


def stdout_emitter(stop: threading.Event, base: BaseResolver) -> None:
    while not stop.is_set():
        try:
            payload = STATE.to_payload(base.current())
            print(f"BMG_TELEM:{json.dumps(payload)}", flush=True)
        except Exception as error:  # pragma: no cover - defensive
            print(f"[telemetry] emit failed: {error}", file=sys.stderr)
        stop.wait(EMIT_PERIOD_SEC)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def main() -> int:
    rclpy.init()
    node = TelemetryNode()
    stop = threading.Event()

    threads = [
        threading.Thread(target=network_monitor, args=(stop,), daemon=True),
        threading.Thread(
            target=stdout_emitter,
            args=(stop, node.base),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    print(
        f"[telemetry] bridge up · namespace={NAMESPACE} · drone={BEBOP_IP}",
        file=sys.stderr,
        flush=True,
    )

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except rclpy.executors.ExternalShutdownException:
        pass
    finally:
        stop.set()
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
