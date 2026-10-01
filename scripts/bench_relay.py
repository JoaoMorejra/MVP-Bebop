#!/usr/bin/env python3
"""One-way sensor relay between the driver domain and an isolated rehearsal domain.

A benchtop rehearsal with the aircraft powered runs ``mission.py --no-fly`` in a
private ``ROS_DOMAIN_ID``. This relay is the only bridge into that domain: it
subscribes to the driver's sensor topics (camera, odometry, aircraft state) in
the source domain and republishes them, as serialized bytes, in the target
domain. Nothing flows the other way.

Safety is structural rather than behavioural:

* the relay never creates a publisher in the source domain on any topic;
* the sensor list is validated at start-up to be disjoint from every topic the
  driver subscribes to (:data:`ACTUATION_TOPICS`), and the test suite pins that
  list against the driver source;
* whatever the rehearsal emits on an actuation topic in the target domain is
  captured by a sink subscription, counted and discarded;
* a watchdog counts publishers on the actuation topics in the source domain and
  reports any non-zero count, which would mean something other than the relay is
  commanding the aircraft. The exit status is 2 when that was ever observed.

Usage (inside ``nectar-activate``, with the driver on domain 14)::

    python3 scripts/bench_relay.py --source-domain 14 --target-domain 77
    ROS_DOMAIN_ID=77 python3 mvp_mission_bebop/mission.py --no-fly --stages 1,2,3,4,5
"""

from __future__ import annotations

import argparse
import importlib
import json
import signal
import sys
import threading
import time
from typing import Any, Dict, Final, Iterable, List, NamedTuple, Optional, Set, Tuple, Union

#: Highest ``ROS_DOMAIN_ID`` Fast DDS maps to a valid port range on Linux.
MAX_DOMAIN_ID: Final[int] = 232

#: QoS kinds understood by :func:`qos_profile`.
QOS_KINDS: Final[Tuple[str, ...]] = ("sensor", "latched", "default")


class RelayTopic(NamedTuple):
    """A sensor topic forwarded from the driver domain.

    Attributes
    ----------
    name : str
        Fully qualified topic name.
    type_name : str
        ROS 2 interface name, ``<package>/msg/<Type>``.
    qos : str
        One of :data:`QOS_KINDS`. ``sensor`` matches the driver's best-effort
        depth-1 camera profile; ``latched`` matches the transient-local depth-1
        state publishers; ``default`` is reliable depth 10 (odometry).
    """

    name: str
    type_name: str
    qos: str


#: Topics forwarded driver -> rehearsal. Every entry is a driver publication.
SENSOR_TOPICS: Final[Tuple[RelayTopic, ...]] = (
    RelayTopic("/bebop/camera/image_raw", "sensor_msgs/msg/Image", "sensor"),
    RelayTopic("/bebop/camera/image_raw/compressed", "sensor_msgs/msg/CompressedImage", "sensor"),
    RelayTopic("/bebop/camera/camera_info", "sensor_msgs/msg/CameraInfo", "sensor"),
    RelayTopic("/bebop/odom", "nav_msgs/msg/Odometry", "default"),
    RelayTopic("/bebop/states/battery", "sensor_msgs/msg/BatteryState", "latched"),
    RelayTopic("/bebop/states/wifi_rssi", "std_msgs/msg/Int16", "latched"),
    RelayTopic("/bebop/states/flying_state", "std_msgs/msg/UInt8", "latched"),
    RelayTopic("/bebop/states/altitude", "std_msgs/msg/Float32", "latched"),
    RelayTopic("/bebop/states/gps", "sensor_msgs/msg/NavSatFix", "latched"),
    RelayTopic("/bebop/states/picture_event", "std_msgs/msg/String", "default"),
    RelayTopic("/bebop/states/link", "std_msgs/msg/Bool", "latched"),
    RelayTopic("/bebop/states/flat_trim", "std_msgs/msg/UInt32", "latched"),
    RelayTopic("/bebop/states/magneto_calibration", "std_msgs/msg/String", "latched"),
)

#: Every topic the driver subscribes to. None of them may cross the relay.
ACTUATION_TOPICS: Final[Tuple[str, ...]] = (
    "/bebop/takeoff",
    "/bebop/land",
    "/bebop/reset",
    "/bebop/flattrim",
    "/bebop/autoflight/navigate_home",
    "/bebop/flip",
    "/bebop/cmd_vel",
    "/bebop/move_camera",
    "/bebop/photo",
    "/bebop/calibrate_magneto",
)

#: Topics whose publisher count is sampled in the source domain.
WATCHED_SOURCE_TOPICS: Final[Tuple[str, ...]] = ("/bebop/takeoff", "/bebop/cmd_vel", "/bebop/land")

#: Period of the graph scan that attaches discard sinks and samples the watchdog, seconds.
GRAPH_SCAN_SEC: Final[float] = 0.5


def _parse_domain(value: Union[int, str], label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TypeError(f"{label} domain must be int or str, got {type(value).__name__}")
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.lstrip("-").isdigit():
            raise ValueError(f"{label} domain {value!r} is not an integer")
        value = int(text)
    if not 0 <= value <= MAX_DOMAIN_ID:
        raise ValueError(f"{label} domain {value} outside [0, {MAX_DOMAIN_ID}]")
    return value


def validate_domains(source: Union[int, str], target: Union[int, str]) -> Tuple[int, int]:
    """Validate the relay's two DDS domain ids.

    Parameters
    ----------
    source : int or str
        Domain the driver runs in.
    target : int or str
        Isolated domain the rehearsal runs in.

    Returns
    -------
    tuple of int
        ``(source, target)`` as integers.

    Raises
    ------
    TypeError
        If either value is not an ``int`` or ``str`` (``bool`` is refused).
    ValueError
        If either value is not integral, lies outside ``[0, MAX_DOMAIN_ID]``, or
        both are equal: relaying a domain onto itself is a loop, not isolation.
    """
    source_id = _parse_domain(source, "source")
    target_id = _parse_domain(target, "target")
    if source_id == target_id:
        raise ValueError(f"source and target domains must be distinct, both are {source_id}")
    return source_id, target_id


def validate_relay_plan(sensors: Iterable[RelayTopic], actuation: Iterable[str]) -> Tuple[RelayTopic, ...]:
    """Refuse any relay plan that would forward an actuation topic.

    Parameters
    ----------
    sensors : iterable of RelayTopic
        Topics to forward.
    actuation : iterable of str
        Topics the driver subscribes to.

    Returns
    -------
    tuple of RelayTopic
        The validated plan.

    Raises
    ------
    ValueError
        If a sensor entry names an actuation topic, repeats a name, or has an
        unknown QoS kind.
    """
    plan = tuple(sensors)
    forbidden = set(actuation)
    seen: Set[str] = set()
    for topic in plan:
        if topic.name in forbidden:
            raise ValueError(f"{topic.name} is an actuation topic and cannot be relayed")
        if topic.name in seen:
            raise ValueError(f"{topic.name} is listed twice")
        if topic.qos not in QOS_KINDS:
            raise ValueError(f"{topic.name}: unknown QoS kind {topic.qos!r}")
        seen.add(topic.name)
    return plan


def resolve_message_type(type_name: str) -> Any:
    """Import the Python class of a ROS 2 message interface.

    Parameters
    ----------
    type_name : str
        Interface name in the form ``<package>/msg/<Type>``.

    Returns
    -------
    type
        The generated message class.

    Raises
    ------
    ValueError
        If the name is malformed or the interface cannot be imported.
    """
    parts = type_name.split("/")
    if len(parts) != 3 or parts[1] != "msg" or not all(parts):
        raise ValueError(f"{type_name!r} is not <package>/msg/<Type>")
    package, _, name = parts
    try:
        module = importlib.import_module(f"{package}.msg")
        return getattr(module, name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot import {type_name}: {exc}") from exc


def qos_profile(kind: str) -> Any:
    """Build the QoS profile for a :class:`RelayTopic` kind.

    Parameters
    ----------
    kind : str
        One of :data:`QOS_KINDS`.

    Returns
    -------
    rclpy.qos.QoSProfile

    Raises
    ------
    ValueError
        If ``kind`` is unknown.
    """
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

    if kind == "sensor":
        return QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1
        )
    if kind == "latched":
        return QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
    if kind == "default":
        return QoSProfile(reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10)
    raise ValueError(f"unknown QoS kind {kind!r}")


class SensorRelay:
    """Forward driver sensors into an isolated domain and discard every command.

    Parameters
    ----------
    source_domain : int or str
        Driver domain. Only subscriptions are created here.
    target_domain : int or str
        Rehearsal domain. Sensor publishers and discard sinks live here.
    sensors : iterable of RelayTopic, optional
        Forwarding plan; defaults to :data:`SENSOR_TOPICS`.

    Raises
    ------
    TypeError, ValueError
        From :func:`validate_domains` and :func:`validate_relay_plan`.
    """

    def __init__(
        self,
        source_domain: Union[int, str],
        target_domain: Union[int, str],
        sensors: Optional[Iterable[RelayTopic]] = None,
    ) -> None:
        self.source_domain, self.target_domain = validate_domains(source_domain, target_domain)
        self.plan = validate_relay_plan(SENSOR_TOPICS if sensors is None else sensors, ACTUATION_TOPICS)
        for topic in self.plan:
            resolve_message_type(topic.type_name)
        self._lock = threading.Lock()
        self._forwarded: Dict[str, int] = {topic.name: 0 for topic in self.plan}
        self._discarded: Dict[str, int] = {}
        self._source_publishers_max: Dict[str, int] = {name: 0 for name in WATCHED_SOURCE_TOPICS}
        self._sinks: Set[Tuple[str, str]] = set()
        self._threads: List[threading.Thread] = []
        self._started = False

    def start(self) -> None:
        """Create both contexts, wire the relay and spin each domain in its own thread."""
        import rclpy
        from rclpy.executors import SingleThreadedExecutor

        self._source_ctx = rclpy.Context()
        self._target_ctx = rclpy.Context()
        rclpy.init(context=self._source_ctx, domain_id=self.source_domain)
        rclpy.init(context=self._target_ctx, domain_id=self.target_domain)
        options = dict(enable_rosout=False, start_parameter_services=False)
        self._source = rclpy.create_node("bench_relay_source", context=self._source_ctx, **options)
        self._target = rclpy.create_node("bench_relay_target", context=self._target_ctx, **options)

        for topic in self.plan:
            msg_type = resolve_message_type(topic.type_name)
            qos = qos_profile(topic.qos)
            publisher = self._target.create_publisher(msg_type, topic.name, qos)
            self._source.create_subscription(
                msg_type, topic.name, self._forwarder(topic.name, publisher), qos, raw=True
            )

        self._target.create_timer(GRAPH_SCAN_SEC, self._attach_sinks)
        self._source.create_timer(GRAPH_SCAN_SEC, self._sample_source_publishers)

        self._executors = []
        for node, ctx in ((self._source, self._source_ctx), (self._target, self._target_ctx)):
            executor = SingleThreadedExecutor(context=ctx)
            executor.add_node(node)
            self._executors.append(executor)
            thread = threading.Thread(target=self._spin, args=(executor, ctx), daemon=True)
            thread.start()
            self._threads.append(thread)
        self._started = True

    def stop(self) -> None:
        """Shut both contexts down and join the spin threads."""
        if not self._started:
            return
        import rclpy

        for ctx in (self._source_ctx, self._target_ctx):
            if ctx.ok():
                rclpy.shutdown(context=ctx)
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._started = False

    def forwarded(self) -> Dict[str, int]:
        """Return a copy of the per-topic forwarded message counts."""
        with self._lock:
            return dict(self._forwarded)

    def discarded(self) -> Dict[str, int]:
        """Return a copy of the per-topic counts of rehearsal commands dropped."""
        with self._lock:
            return dict(self._discarded)

    def source_publishers_max(self) -> Dict[str, int]:
        """Return the maximum publisher count seen per watched source topic."""
        with self._lock:
            return dict(self._source_publishers_max)

    def isolation_breached(self) -> bool:
        """Whether any watched source topic ever had a publisher."""
        return any(count > 0 for count in self.source_publishers_max().values())

    @staticmethod
    def _spin(executor: Any, ctx: Any) -> None:
        from rclpy.executors import ExternalShutdownException

        try:
            while ctx.ok():
                executor.spin_once(timeout_sec=0.1)
        except (ExternalShutdownException, RuntimeError):
            pass

    def _forwarder(self, name: str, publisher: Any) -> Any:
        def forward(serialized: bytes) -> None:
            publisher.publish(serialized)
            with self._lock:
                self._forwarded[name] += 1

        return forward

    def _discarder(self, name: str) -> Any:
        def discard(_serialized: bytes) -> None:
            with self._lock:
                self._discarded[name] = self._discarded.get(name, 0) + 1

        return discard

    def _attach_sinks(self) -> None:
        # Sinks are created per (topic, type) actually seen in the rehearsal
        # graph: the SDK publishes some actuation topics with a type other than
        # the one the driver subscribes with (navigate_home: Empty vs Bool), and
        # a sink of the wrong type would never match and never count.
        forbidden = set(ACTUATION_TOPICS)
        for name, types in self._target.get_topic_names_and_types():
            if name not in forbidden:
                continue
            for type_name in types:
                key = (name, type_name)
                if key in self._sinks:
                    continue
                try:
                    msg_type = resolve_message_type(type_name)
                except ValueError:
                    continue
                self._target.create_subscription(msg_type, name, self._discarder(name), 10, raw=True)
                self._sinks.add(key)

    def _sample_source_publishers(self) -> None:
        for name in WATCHED_SOURCE_TOPICS:
            count = self._source.count_publishers(name)
            with self._lock:
                if count > self._source_publishers_max[name]:
                    self._source_publishers_max[name] = count


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface of the relay."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-domain", default="14", help="driver ROS_DOMAIN_ID (default 14)")
    parser.add_argument("--target-domain", default="77", help="rehearsal ROS_DOMAIN_ID (default 77)")
    parser.add_argument("--report-sec", type=float, default=5.0, help="status line period, seconds")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the relay until SIGINT/SIGTERM and print a JSON summary.

    Returns
    -------
    int
        0 on a clean run, 2 if a publisher was ever seen on a watched actuation
        topic in the source domain.
    """
    args = build_parser().parse_args(argv)
    if args.report_sec <= 0:
        raise ValueError("--report-sec must be positive")
    relay = SensorRelay(args.source_domain, args.target_domain)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    relay.start()
    print(f"[RELAY] {relay.source_domain} -> {relay.target_domain}, {len(relay.plan)} sensor topics", flush=True)
    while not stop.wait(args.report_sec):
        print(
            f"[RELAY] t={time.monotonic():.1f} forwarded={json.dumps(relay.forwarded())} "
            f"discarded={json.dumps(relay.discarded())} "
            f"source_publishers_max={json.dumps(relay.source_publishers_max())}",
            flush=True,
        )
    relay.stop()
    summary = {
        "forwarded": relay.forwarded(),
        "discarded": relay.discarded(),
        "source_publishers_max": relay.source_publishers_max(),
        "isolation_breached": relay.isolation_breached(),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 2 if relay.isolation_breached() else 0


if __name__ == "__main__":
    sys.exit(main())
