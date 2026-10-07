"""rclpy adapter of the driver emulator: node ``/bebop/bebop_driver`` with the driver's contract.

Endpoints, types and QoS are :data:`DRIVER_CONTRACT`, pinned against
``ros2_bebop_driver/src/bebop_driver_node.cpp`` by
``test/test_fake_bebop_contract.py``. Publication cadence follows the driver
too: odometry only on a new speed sample, aircraft state republished at 2 Hz
(``state_timer`` 500 ms), photo events drained every 50 ms, camera frames from
their own thread.

Every command received, every flying-state change and publication, every fault
and the trajectory are written to :class:`CommandLog` with the emulator's own
monotonic and wall clocks: the ground truth the integration tests measure the
mission against (section 4.1.6).

The node refuses a context in the driver's domain or outside the emulator's
reserved range; ``scripts/fake_bebop_driver.py`` adds the environment and graph
interlocks of section 4.1.5.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Final, List, NamedTuple, Optional, TextIO, Tuple

from support.fake_bebop.plant import (
    BebopPlant,
    FaultFired,
    FlatTrimAck,
    OdometrySample,
    PictureEvent,
    StateChange,
)
from support.fake_bebop.scene import FRAME_HEIGHT, FRAME_WIDTH, CameraView, Scene, UniformScene

NODE_NAME: Final[str] = "bebop_driver"
NAMESPACE: Final[str] = "/bebop"

#: ``ROS_DOMAIN_ID`` of the real driver. The emulator never joins it.
DRIVER_DOMAIN: Final[int] = 14
#: Domains reserved to the emulator.
EMULATOR_DOMAINS: Final[range] = range(80, 100)

#: Plant integration period, seconds.
PLANT_PERIOD_SEC: Final[float] = 0.01
#: ``state_timer`` and ``event_timer`` of the driver, seconds.
STATE_PERIOD_SEC: Final[float] = 0.5
EVENT_PERIOD_SEC: Final[float] = 0.05
#: Trajectory record period, seconds.
TRAJECTORY_PERIOD_SEC: Final[float] = 0.2
#: RSSI reported while linked, dBm (measured -51 dBm live).
LINKED_RSSI_DBM: Final[int] = -51


class TopicContract(NamedTuple):
    """One endpoint: direction, name relative to the namespace, type, reliability, durability, depth."""

    direction: str
    name: str
    type_name: str
    reliability: str
    durability: str
    depth: int


DRIVER_CONTRACT: Final[Tuple[TopicContract, ...]] = (
    TopicContract("sub", "takeoff", "std_msgs/msg/Empty", "reliable", "volatile", 1),
    TopicContract("sub", "land", "std_msgs/msg/Empty", "reliable", "volatile", 1),
    TopicContract("sub", "reset", "std_msgs/msg/Empty", "reliable", "volatile", 1),
    TopicContract("sub", "flattrim", "std_msgs/msg/Empty", "reliable", "volatile", 1),
    TopicContract("sub", "autoflight/navigate_home", "std_msgs/msg/Bool", "reliable", "volatile", 1),
    TopicContract("sub", "flip", "std_msgs/msg/UInt8", "reliable", "volatile", 1),
    TopicContract("sub", "cmd_vel", "geometry_msgs/msg/Twist", "reliable", "volatile", 1),
    TopicContract("sub", "move_camera", "geometry_msgs/msg/Vector3", "reliable", "volatile", 1),
    TopicContract("sub", "photo", "std_msgs/msg/Bool", "reliable", "volatile", 1),
    TopicContract("sub", "calibrate_magneto", "std_msgs/msg/Bool", "reliable", "volatile", 1),
    TopicContract("pub", "camera/image_raw", "sensor_msgs/msg/Image", "best_effort", "volatile", 1),
    TopicContract("pub", "camera/camera_info", "sensor_msgs/msg/CameraInfo", "best_effort", "volatile", 1),
    TopicContract("pub", "odom", "nav_msgs/msg/Odometry", "reliable", "volatile", 10),
    TopicContract("pub", "states/battery", "sensor_msgs/msg/BatteryState", "reliable", "transient_local", 1),
    TopicContract("pub", "states/wifi_rssi", "std_msgs/msg/Int16", "reliable", "transient_local", 1),
    TopicContract("pub", "states/flying_state", "std_msgs/msg/UInt8", "reliable", "transient_local", 1),
    TopicContract("pub", "states/altitude", "std_msgs/msg/Float32", "reliable", "transient_local", 1),
    TopicContract("pub", "states/gps", "sensor_msgs/msg/NavSatFix", "reliable", "transient_local", 1),
    TopicContract("pub", "states/picture_event", "std_msgs/msg/String", "reliable", "volatile", 10),
    TopicContract("pub", "states/flat_trim", "std_msgs/msg/UInt32", "reliable", "transient_local", 1),
    TopicContract("pub", "states/magneto_calibration", "std_msgs/msg/String", "reliable", "transient_local", 1),
    TopicContract("pub", "states/link", "std_msgs/msg/Bool", "reliable", "transient_local", 1),
)

#: ``config/bebop2_camera_calib.yaml`` of the driver, as published on ``camera_info``.
CAMERA_K: Final[Tuple[float, ...]] = (537.292878, 0.0, 427.331854, 0.0, 527.000348, 240.226888, 0.0, 0.0, 1.0)
CAMERA_D: Final[Tuple[float, ...]] = (0.004974, -0.000130, -0.001212, 0.002192, 0.0)
CAMERA_P: Final[Tuple[float, ...]] = (
    539.403503, 0.0, 429.275072, 0.0, 0.0, 529.838562, 238.941372, 0.0, 0.0, 0.0, 1.0, 0.0,
)


def check_domain(domain: int) -> int:
    """Refuse the driver's domain and anything outside :data:`EMULATOR_DOMAINS`.

    Raises
    ------
    TypeError
        If ``domain`` is not an int.
    ValueError
        If it is the driver's domain or outside the reserved range.
    """
    if isinstance(domain, bool) or not isinstance(domain, int):
        raise TypeError(f"domain must be an int, got {type(domain).__name__}")
    if domain == DRIVER_DOMAIN:
        raise ValueError(f"ROS_DOMAIN_ID {DRIVER_DOMAIN} is the real driver's domain; the emulator never joins it")
    if domain not in EMULATOR_DOMAINS:
        raise ValueError(
            f"ROS_DOMAIN_ID {domain} outside the emulator range "
            f"{EMULATOR_DOMAINS.start}-{EMULATOR_DOMAINS.stop - 1}"
        )
    return domain


class CommandLog:
    """JSON-lines ground truth of an emulator run, kept in memory and optionally on disk.

    Parameters
    ----------
    path : str, optional
        File to append to; ``None`` keeps the records in memory only.
    clock, wall : callable
        Monotonic and wall clocks, seconds.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._wall = wall
        self.records: List[Dict[str, Any]] = []
        self._handle: Optional[TextIO] = open(path, "a", encoding="utf-8") if path else None

    def write(self, event: str, mono: Optional[float] = None, **fields: Any) -> Dict[str, Any]:
        """Append one record ``{"ev", "mono", "wall", ...}`` and return it."""
        now_mono = self._clock() if mono is None else mono
        record = {"ev": event, "mono": round(now_mono, 6), "wall": round(self._wall() - self._clock() + now_mono, 6)}
        record.update(fields)
        with self._lock:
            self.records.append(record)
            if self._handle is not None:
                self._handle.write(json.dumps(record) + "\n")
                self._handle.flush()
        return record

    def select(self, event: str, **match: Any) -> List[Dict[str, Any]]:
        """Records of ``event`` whose fields equal ``match``."""
        with self._lock:
            return [r for r in self.records if r["ev"] == event and all(r.get(k) == v for k, v in match.items())]

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


class FakeBebopDriver:
    """The emulated driver node, its timers and its camera thread.

    Parameters
    ----------
    context : rclpy.Context
        An initialised context in a domain of :data:`EMULATOR_DOMAINS`.
    plant : BebopPlant
        Built with ``start_time`` on ``clock``'s timeline, or advanced to it.
    scene : Scene, optional
        Frame source; an empty floor by default.
    log : CommandLog, optional
    camera_hz : float
        Frame rate of the camera thread (the stream runs at 30 FPS).
    publish_compressed : bool
        Also publish ``camera/image_raw/compressed`` (JPEG), as the
        ``image_transport`` compressed plugin does on the station.
    clock : callable
        Monotonic clock driving the plant.

    Raises
    ------
    ValueError
        If the context's domain is refused by :func:`check_domain`.
    """

    def __init__(
        self,
        context: Any,
        plant: BebopPlant,
        scene: Optional[Scene] = None,
        log: Optional[CommandLog] = None,
        *,
        camera_hz: float = 30.0,
        publish_compressed: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        import rclpy
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data

        check_domain(context.get_domain_id())
        if isinstance(camera_hz, bool) or not isinstance(camera_hz, (int, float)) or not camera_hz > 0.0:
            raise ValueError(f"camera_hz must be a positive number, got {camera_hz!r}")
        self._context = context
        self.plant = plant
        self.scene: Scene = scene if scene is not None else UniformScene()
        self.log = log if log is not None else CommandLog(clock=clock)
        self._clock = clock
        self._camera_period = 1.0 / float(camera_hz)
        self._publish_compressed = bool(publish_compressed)
        self._lock = threading.RLock()
        self._tilt = 0.0
        self._pan = 0.0
        self._pictures: Deque[PictureEvent] = deque()
        self._last_flat_trim: Optional[int] = None
        self._magneto_sent = False
        self._battery_report: Tuple[int, float] = (-1, 0.0)
        self._wall_offset = time.time() - clock()
        self._last_trajectory = 0.0
        self._running = threading.Event()
        self._camera_thread: Optional[threading.Thread] = None
        self._spin_thread: Optional[threading.Thread] = None

        self.node = rclpy.create_node(NODE_NAME, namespace=NAMESPACE, context=context)
        commands = MutuallyExclusiveCallbackGroup()
        timers = ReentrantCallbackGroup()
        self._msgs = self._import_messages()
        m = self._msgs

        def reliable(depth: int, transient: bool = False) -> QoSProfile:
            return QoSProfile(
                depth=depth,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL if transient else DurabilityPolicy.VOLATILE,
            )

        sensor = QoSProfile(depth=1, reliability=qos_profile_sensor_data.reliability,
                            durability=qos_profile_sensor_data.durability)
        subscriptions = {
            "takeoff": (m["Empty"], self._on_takeoff),
            "land": (m["Empty"], self._on_land),
            "reset": (m["Empty"], self._on_reset),
            "flattrim": (m["Empty"], self._on_flattrim),
            "autoflight/navigate_home": (m["Bool"], self._on_ignored("autoflight/navigate_home")),
            "flip": (m["UInt8"], self._on_ignored("flip")),
            "cmd_vel": (m["Twist"], self._on_cmd_vel),
            "move_camera": (m["Vector3"], self._on_move_camera),
            "photo": (m["Bool"], self._on_photo),
            "calibrate_magneto": (m["Bool"], self._on_ignored("calibrate_magneto")),
        }
        self._publishers: Dict[str, Any] = {}
        for entry in DRIVER_CONTRACT:
            qos = sensor if entry.reliability == "best_effort" else reliable(
                entry.depth, entry.durability == "transient_local"
            )
            if entry.direction == "sub":
                msg_type, callback = subscriptions[entry.name]
                self.node.create_subscription(msg_type, entry.name, callback, qos, callback_group=commands)
            else:
                self._publishers[entry.name] = self.node.create_publisher(
                    m[entry.type_name.rsplit("/", 1)[1]], entry.name, qos
                )
        if self._publish_compressed:
            self._publishers["camera/image_raw/compressed"] = self.node.create_publisher(
                m["CompressedImage"], "camera/image_raw/compressed", sensor
            )

        self.node.create_timer(PLANT_PERIOD_SEC, self._advance, callback_group=timers)
        self.node.create_timer(STATE_PERIOD_SEC, self._publish_state, callback_group=timers)
        self.node.create_timer(EVENT_PERIOD_SEC, self._publish_events, callback_group=timers)
        self._camera_info = self._build_camera_info()

    @staticmethod
    def _import_messages() -> Dict[str, Any]:
        from geometry_msgs.msg import Twist, Vector3
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import BatteryState, CameraInfo, CompressedImage, Image, NavSatFix
        from std_msgs.msg import Bool, Empty, Float32, Int16, String, UInt8, UInt32

        return {
            "Twist": Twist, "Vector3": Vector3, "Odometry": Odometry, "BatteryState": BatteryState,
            "CameraInfo": CameraInfo, "CompressedImage": CompressedImage, "Image": Image, "NavSatFix": NavSatFix,
            "Bool": Bool, "Empty": Empty, "Float32": Float32, "Int16": Int16, "String": String,
            "UInt8": UInt8, "UInt32": UInt32,
        }

    # ------------------------------------------------------------- lifecycle

    def start(self, **fields: Any) -> None:
        """Spin the node on its own executor and start the camera thread.

        Parameters
        ----------
        **fields
            Written into the ``start`` record of the log (scenario, faults).
        """
        from rclpy.executors import SingleThreadedExecutor

        self._running.set()
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(self.node)
        self._spin_thread = threading.Thread(target=self._spin, name="FakeBebopSpin", daemon=True)
        self._spin_thread.start()
        self._camera_thread = threading.Thread(target=self._camera_loop, name="FakeBebopCamera", daemon=True)
        self._camera_thread.start()
        self.log.write("start", plant=_plant_summary(self.plant), domain=self._context.get_domain_id(),
                       pid=os.getpid(), **fields)

    def stop(self) -> None:
        """Stop the threads and destroy the node; the context stays with the caller."""
        if not self._running.is_set():
            return
        self._running.clear()
        for thread in (self._camera_thread, self._spin_thread):
            if thread is not None:
                thread.join(timeout=2.0)
        self._executor.remove_node(self.node)
        self._executor.shutdown(timeout_sec=2.0)
        self.node.destroy_node()
        self.log.write("stop", state=int(self.plant.state))
        self.log.close()

    def _spin(self) -> None:
        try:
            while self._running.is_set() and self._context.ok():
                self._executor.spin_once(timeout_sec=0.05)
        except Exception:  # noqa: BLE001 - shutdown race between the executor and the context
            pass

    # ------------------------------------------------------------- commands

    def _record_command(self, topic: str, **fields: Any) -> float:
        now = self._clock()
        self.log.write("cmd", mono=now, topic=topic, state=int(self.plant.state), **fields)
        return now

    def _on_takeoff(self, _msg: Any) -> None:
        with self._lock:
            self.plant.takeoff(self._record_command("takeoff"))

    def _on_land(self, _msg: Any) -> None:
        with self._lock:
            self.plant.land(self._record_command("land"))

    def _on_reset(self, _msg: Any) -> None:
        with self._lock:
            self.plant.emergency(self._record_command("reset"))

    def _on_flattrim(self, _msg: Any) -> None:
        with self._lock:
            now = self._record_command("flattrim")
            for event in self.plant.advance(max(self.plant.time, now)):
                self._handle(event)
            self.plant.flat_trim(now)

    def _on_photo(self, msg: Any) -> None:
        with self._lock:
            now = self._record_command("photo", data=bool(msg.data))
            if msg.data:
                self.plant.photo(now)

    def _on_cmd_vel(self, msg: Any) -> None:
        values = [float(msg.linear.x), float(msg.linear.y), float(msg.linear.z), float(msg.angular.z)]
        rest = [float(msg.angular.x), float(msg.angular.y)]
        with self._lock:
            now = self._record_command("cmd_vel", v=values, nonzero=any(v != 0.0 for v in values + rest))
            self.plant.pcmd(now, *values)

    def _on_move_camera(self, msg: Any) -> None:
        with self._lock:
            self._record_command("move_camera", tilt=float(msg.x), pan=float(msg.y))
            self._tilt = float(msg.x)
            self._pan = float(msg.y)

    def _on_ignored(self, topic: str) -> Callable[[Any], None]:
        def record(msg: Any) -> None:
            data = getattr(msg, "data", None)
            self._record_command(topic, data=data if isinstance(data, (bool, int)) else None)

        return record

    # ------------------------------------------------------------- plant and publications

    def _advance(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self.plant.time:
                return
            events = self.plant.advance(now)
            for event in events:
                self._handle(event)
            if now - self._last_trajectory >= TRAJECTORY_PERIOD_SEC:
                self._last_trajectory = now
                x, y, z = self.plant.true_position
                vx, vy, vz = self.plant.true_velocity
                odom = self.plant.last_odometry
                self.log.write("traj", mono=now, x=round(x, 4), y=round(y, 4), z=round(z, 4), vx=round(vx, 4),
                               vy=round(vy, 4), vz=round(vz, 4), state=int(self.plant.state),
                               odom_z=None if odom is None else round(odom.z, 4),
                               battery=round(self.plant.battery_pct, 2), tilt=self._tilt)

    def _handle(self, event: Any) -> None:
        if isinstance(event, OdometrySample):
            self._publish_odometry(event)
        elif isinstance(event, StateChange):
            self.log.write("state", mono=event.t, old=int(event.old), new=int(event.new))
            self._publishers["states/flying_state"].publish(self._msgs["UInt8"](data=int(event.new)))
        elif isinstance(event, PictureEvent):
            self._pictures.append(event)
        elif isinstance(event, FlatTrimAck):
            self.log.write("flat_trim_ack", mono=event.t, sequence=event.sequence)
        elif isinstance(event, FaultFired):
            self.log.write("fault", mono=event.t, kind=event.kind, detail=event.detail)

    def _stamp(self, mono: float) -> Any:
        from builtin_interfaces.msg import Time

        wall = mono + self._wall_offset
        sec = int(math.floor(wall))
        return Time(sec=sec, nanosec=int((wall - sec) * 1e9))

    def _publish_odometry(self, sample: OdometrySample) -> None:
        msg = self._msgs["Odometry"]()
        msg.header.stamp = self._stamp(sample.t)
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position.x = sample.x
        msg.pose.pose.position.y = sample.y
        msg.pose.pose.position.z = sample.z
        qx, qy, qz, qw = _quaternion_from_rpy(sample.roll, sample.pitch, sample.yaw)
        msg.pose.pose.orientation.x = qx
        msg.pose.pose.orientation.y = qy
        msg.pose.pose.orientation.z = qz
        msg.pose.pose.orientation.w = qw
        msg.twist.twist.linear.x = sample.vx
        msg.twist.twist.linear.y = sample.vy
        msg.twist.twist.linear.z = sample.vz
        self._publishers["odom"].publish(msg)

    def _publish_state(self) -> None:
        m = self._msgs
        with self._lock:
            now = self._clock()
            state = int(self.plant.state)
            battery = int(round(self.plant.battery_pct))
            altitude = float(self.plant.true_altitude)
            sequence = self.plant.flat_trim_sequence
            if battery != self._battery_report[0]:
                self._battery_report = (battery, now)
        self._publishers["states/link"].publish(m["Bool"](data=True))

        report = m["BatteryState"]()
        report.header.stamp = self._stamp(self._battery_report[1])
        report.header.frame_id = "base_link"
        report.present = True
        report.percentage = battery / 100.0
        nan = float("nan")
        report.voltage = report.current = report.charge = report.capacity = report.design_capacity = nan
        report.power_supply_technology = m["BatteryState"].POWER_SUPPLY_TECHNOLOGY_LIPO
        report.power_supply_status = m["BatteryState"].POWER_SUPPLY_STATUS_DISCHARGING
        report.power_supply_health = m["BatteryState"].POWER_SUPPLY_HEALTH_UNKNOWN
        self._publishers["states/battery"].publish(report)

        self._publishers["states/wifi_rssi"].publish(m["Int16"](data=LINKED_RSSI_DBM))
        self._publishers["states/flying_state"].publish(m["UInt8"](data=state))
        self.log.write("pub", mono=now, topic="states/flying_state", value=state)
        self._publishers["states/altitude"].publish(m["Float32"](data=altitude))

        gps = m["NavSatFix"]()
        gps.header.stamp = self._stamp(now)
        gps.header.frame_id = "base_link"
        gps.status.status = gps.status.STATUS_NO_FIX
        gps.status.service = gps.status.SERVICE_GPS
        gps.position_covariance_type = gps.COVARIANCE_TYPE_UNKNOWN
        self._publishers["states/gps"].publish(gps)

        if sequence != self._last_flat_trim or sequence == 0:
            self._last_flat_trim = sequence
            self._publishers["states/flat_trim"].publish(m["UInt32"](data=sequence))
            self.log.write("pub", mono=now, topic="states/flat_trim", value=sequence)

        if not self._magneto_sent:
            self._magneto_sent = True
            text = json.dumps({"sequence": 1, "kind": "required", "x": 0, "y": 0, "z": 0, "failed": 0,
                               "required": 0, "axis": "none", "started": 0, "stamp": now + self._wall_offset})
            self._publishers["states/magneto_calibration"].publish(m["String"](data=text))

    def _publish_events(self) -> None:
        while True:
            with self._lock:
                if not self._pictures:
                    return
                event = self._pictures.popleft()
            text = json.dumps({"sequence": event.sequence, "kind": event.kind, "value": event.value,
                               "error": event.error, "stamp": event.t + self._wall_offset})
            self._publishers["states/picture_event"].publish(self._msgs["String"](data=text))
            self.log.write("pub", mono=self._clock(), topic="states/picture_event", kind=event.kind,
                           value=event.value, sequence=event.sequence)

    # ------------------------------------------------------------- camera

    def _build_camera_info(self) -> Any:
        info = self._msgs["CameraInfo"]()
        info.width = FRAME_WIDTH
        info.height = FRAME_HEIGHT
        info.distortion_model = "plumb_bob"
        info.k = list(CAMERA_K)
        info.d = list(CAMERA_D)
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.p = list(CAMERA_P)
        info.header.frame_id = "camera_optical"
        return info

    def current_view(self) -> CameraView:
        """The camera's view of the plant now."""
        with self._lock:
            x, y, z = self.plant.true_position
            return CameraView(x, y, z, self._tilt, self._pan, int(self.plant.state))

    def _camera_loop(self) -> None:
        next_frame = self._clock()
        while self._running.is_set():
            try:
                self._publish_frame(self.current_view())
            except Exception:  # noqa: BLE001 - a publisher torn down at shutdown
                if not self._running.is_set():
                    return
                raise
            next_frame += self._camera_period
            delay = next_frame - self._clock()
            if delay > 0.0:
                time.sleep(delay)
            else:
                next_frame = self._clock()

    def _publish_frame(self, view: CameraView) -> None:
        frame = self.scene.render(view)
        now = self._clock()
        stamp = self._stamp(now)
        image = self._msgs["Image"]()
        image.header.stamp = stamp
        image.header.frame_id = "camera_optical"
        image.height, image.width = int(frame.shape[0]), int(frame.shape[1])
        image.encoding = "bgr8"
        image.is_bigendian = 0
        image.step = 3 * image.width
        image.data = frame.tobytes()
        info = self._camera_info
        info.header.stamp = stamp
        self._publishers["camera/image_raw"].publish(image)
        self._publishers["camera/camera_info"].publish(info)
        if self._publish_compressed:
            import cv2

            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                compressed = self._msgs["CompressedImage"]()
                compressed.header.stamp = stamp
                compressed.header.frame_id = "camera_optical"
                compressed.format = "bgr8; jpeg compressed bgr8"
                compressed.data = encoded.tobytes()
                self._publishers["camera/image_raw/compressed"].publish(compressed)


def _quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> Tuple[float, float, float, float]:
    """``tf2::Quaternion::setRPY``: x, y, z, w."""
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _plant_summary(plant: BebopPlant) -> Dict[str, Any]:
    cfg = plant.config
    return {"seed": cfg.seed, "efficiency": cfg.efficiency, "tau_sec": cfg.tau_sec, "latency_sec": cfg.latency_sec,
            "battery_start_pct": cfg.battery_start_pct}
