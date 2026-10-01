"""Acknowledgement of the IMU flat trim (``FlatTrimChanged``).

The driver publishes the number of ``PilotingState.FlatTrimChanged`` events it
has received on ``states/flat_trim`` (``std_msgs/UInt32``, reliable,
transient-local, depth 1; ``ros2_bebop_driver/src/bebop_driver_node.cpp``).
Stage 1 reads the count before requesting the flat trim and waits for it to
advance; the Nectar SDK's ``flat_trim`` publishes the request and reports
nothing back.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class FlatTrimAckTracker:
    """Latest flat-trim count from the driver, waitable.

    Thread-safe: :meth:`update` runs on the ROS executor thread and
    :meth:`wait_after` on the mission thread.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._sequence: Optional[int] = None

    @property
    def sequence(self) -> Optional[int]:
        """Last count received; ``None`` before the driver has published one."""
        with self._condition:
            return self._sequence

    def update(self, count: object) -> None:
        """Accept one ``states/flat_trim`` value. Non-integers and negatives are ignored."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return
        with self._condition:
            self._sequence = count
            self._condition.notify_all()

    def wait_after(self, mark: Optional[int], timeout_sec: float) -> bool:
        """True once the count exceeds ``mark`` within ``timeout_sec``.

        Parameters
        ----------
        mark : int or None
            :attr:`sequence` read before the request. ``None`` (nothing heard
            yet) is satisfied by any count received afterwards.
        timeout_sec : float
            Longest wait, seconds.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        with self._condition:
            while True:
                current = self._sequence
                if current is not None and (mark is None or current > mark):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
