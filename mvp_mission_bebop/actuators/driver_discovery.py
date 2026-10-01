"""In-process discovery of the Bebop driver and recovery of a stale ``ros2`` daemon (7.2).

The Nectar SDK decides whether the driver is up with ``ros2 node list``
(``nectar/utils/process.py``), which answers from the ``ros2`` daemon's cached
graph. Reproduced live: with the driver running, a daemon started before it
reported no nodes until it was restarted. The mission's own rclpy node sees
the live graph, so it is asked first; when it sees the driver and the SDK does
not, the daemon is the one that is wrong.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Final, Iterable, Tuple

logger = logging.getLogger("DriverDiscovery")

#: Node names ``ros2_bebop_driver`` registers under, by launch file and by executable.
DRIVER_NODE_NAMES: Final[Tuple[str, ...]] = ("bebop_driver", "bebop_driver_node")
#: Longest active wait for the driver to appear in the graph, seconds.
DRIVER_PROBE_SEC: Final[float] = 2.0
#: Ceiling on each ``ros2 daemon`` command, seconds.
DAEMON_COMMAND_TIMEOUT_SEC: Final[float] = 10.0


def driver_in_graph(
    node: Any,
    *,
    namespace: str = "/bebop",
    names: Iterable[str] = DRIVER_NODE_NAMES,
    timeout_sec: float = DRIVER_PROBE_SEC,
    poll_sec: float = 0.05,
) -> bool:
    """Whether the driver node appears in ``node``'s view of the graph within ``timeout_sec``.

    Parameters
    ----------
    node : rclpy.node.Node
        Any live node; only ``get_node_names_and_namespaces`` is used, which
        reads DDS discovery directly and needs no executor.
    namespace : str
        Driver namespace (``network.namespace``).
    """
    wanted = set(names)
    expected = "/" + namespace.strip("/")
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        try:
            discovered = node.get_node_names_and_namespaces()
        except Exception:  # noqa: BLE001 - a transport hiccup is a negative sample, not a crash
            discovered = []
        if any(name in wanted and ns.rstrip("/") == expected for name, ns in discovered):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_sec)


def restart_ros2_daemon() -> None:
    """``ros2 daemon stop`` then ``ros2 daemon start``; failures are logged, not raised."""
    for action in ("stop", "start"):
        try:
            subprocess.run(
                ["ros2", "daemon", action],
                capture_output=True,
                timeout=DAEMON_COMMAND_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("ros2 daemon %s failed: %s", action, exc)
