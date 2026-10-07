"""Read-only watchdog of the driver's domain during an emulated ``--fly`` run (4.1.5, item 5).

Joins a domain (14 in the integration tests) with a node that creates no
publisher and no subscription, samples the publisher count of
``/bebop/{takeoff,cmd_vel,land}`` and reports any count above the one seen at
start. The topics are the ones ``scripts/bench_relay.py`` watches, pinned by
``test/test_fake_bebop_interlocks.py``: anything that appears there while an
emulated flight runs would be a path to the real aircraft.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Final, Optional, Tuple

#: Same topics as ``bench_relay.WATCHED_SOURCE_TOPICS``.
WATCHED_TOPICS: Final[Tuple[str, ...]] = ("/bebop/takeoff", "/bebop/cmd_vel", "/bebop/land")

#: Sampling period of the publisher counts, seconds.
SAMPLE_SEC: Final[float] = 0.25


class ActuationWatchdog:
    """Counts actuation publishers in ``domain`` and remembers the maximum above the baseline.

    Parameters
    ----------
    domain : int
        Domain to watch, typically the driver's (14).
    settle_sec : float
        Discovery wait before the baseline is taken, seconds.
    """

    def __init__(self, domain: int, settle_sec: float = 1.0) -> None:
        if isinstance(domain, bool) or not isinstance(domain, int) or not 0 <= domain <= 232:
            raise ValueError(f"domain must be an int in [0, 232], got {domain!r}")
        self.domain = domain
        self._settle = float(settle_sec)
        self._lock = threading.Lock()
        self._baseline: Dict[str, int] = {}
        self._max: Dict[str, int] = {name: 0 for name in WATCHED_TOPICS}
        self._samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> None:
        """Join the domain, take the baseline and start sampling."""
        import rclpy

        self._ctx = rclpy.Context()
        rclpy.init(context=self._ctx, domain_id=self.domain)
        self._node = rclpy.create_node(
            "fake_bebop_actuation_watchdog", context=self._ctx, enable_rosout=False, start_parameter_services=False
        )
        self._thread = threading.Thread(target=self._run, name="ActuationWatchdog", daemon=True)
        self._thread.start()
        self._ready.wait(self._settle + 2.0)

    def _counts(self) -> Dict[str, int]:
        return {name: self._node.count_publishers(name) for name in WATCHED_TOPICS}

    def _run(self) -> None:
        time.sleep(self._settle)
        with self._lock:
            self._baseline = self._counts()
        self._ready.set()
        while not self._stop.wait(SAMPLE_SEC):
            counts = self._counts()
            with self._lock:
                self._samples += 1
                for name, count in counts.items():
                    self._max[name] = max(self._max[name], count - self._baseline.get(name, 0))

    def breached(self) -> bool:
        """Whether any watched topic ever had more publishers than at the baseline."""
        with self._lock:
            return any(value > 0 for value in self._max.values())

    def report(self) -> Dict[str, Any]:
        """``{"domain", "baseline", "new_publishers", "samples"}``."""
        with self._lock:
            return {
                "domain": self.domain,
                "baseline": dict(self._baseline),
                "new_publishers": dict(self._max),
                "samples": self._samples,
            }

    def stop(self) -> None:
        """Stop sampling and leave the domain."""
        import rclpy

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._node.destroy_node()
        if self._ctx.ok():
            rclpy.shutdown(context=self._ctx)
