"""Acknowledgement of the native 14 MP photo (RecordPictureV2).

The driver republishes the aircraft's ``PictureStateChangedV2`` and
``PictureEventChanged`` as JSON on ``states/picture_event``
(``ros2_bebop_driver/src/bebop_driver_node.cpp:publishState``), each tagged with
a monotonically increasing ``sequence``. The inspection stage reads the current
sequence, requests the photo, and waits for the first ``event`` published after
that mark: ``taken`` is the aircraft reporting the image on its internal
storage, ``failed`` carries the reason. State changes alone
(``ready``/``busy``/``notavailable``) never count as an acknowledgement.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, Final, Optional, Union

logger = logging.getLogger("PictureAck")

#: ``ARCOMMANDS_ARDRONE3_MEDIARECORDEVENT_PICTUREEVENTCHANGED_EVENT``.
EVENT_NAMES: Final[Dict[int, str]] = {0: "taken", 1: "failed"}

#: ``ARCOMMANDS_ARDRONE3_MEDIARECORDEVENT_PICTUREEVENTCHANGED_ERROR``.
ERROR_NAMES: Final[Dict[int, str]] = {
    0: "ok",
    1: "unknown",
    2: "busy",
    3: "notavailable",
    4: "memoryfull",
    5: "lowbattery",
}

#: ``ARCOMMANDS_ARDRONE3_MEDIARECORDSTATE_PICTURESTATECHANGEDV2_STATE``.
STATE_NAMES: Final[Dict[int, str]] = {0: "ready", 1: "busy", 2: "notavailable"}

PictureAck = Dict[str, Union[bool, str, int]]


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


class PictureAckTracker:
    """Latest photo event from the driver, waitable by sequence.

    Thread-safe: :meth:`update` runs on the ROS executor thread and
    :meth:`wait_after` on the mission thread.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._state: Optional[str] = None
        self._event: Optional[PictureAck] = None

    @property
    def sequence(self) -> int:
        """Sequence number of the last message accepted, 0 before the first."""
        with self._condition:
            return self._sequence

    @property
    def state(self) -> Optional[str]:
        """Last reported camera state (``ready``, ``busy``, ``notavailable``)."""
        with self._condition:
            return self._state

    def update(self, text: str) -> None:
        """Accept one ``states/picture_event`` payload.

        Parameters
        ----------
        text : str
            JSON object ``{"sequence", "kind", "value", "error", "stamp"}``.
            Anything else is logged and ignored: a malformed message must not
            reach the mission thread as an exception.
        """
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed picture event: %r", text)
            return
        if not isinstance(payload, dict):
            logger.warning("Ignoring non-object picture event: %r", text)
            return
        sequence = _as_int(payload.get("sequence"))
        value = _as_int(payload.get("value"))
        kind = payload.get("kind")
        if sequence is None or value is None or kind not in ("state", "event"):
            logger.warning("Ignoring incomplete picture event: %r", text)
            return

        with self._condition:
            if sequence <= self._sequence:
                return
            self._sequence = sequence
            if kind == "state":
                self._state = STATE_NAMES.get(value, str(value))
            else:
                error = _as_int(payload.get("error"))
                event = EVENT_NAMES.get(value, str(value))
                self._event = {
                    "acknowledged": event == "taken",
                    "event": event,
                    "error": ERROR_NAMES.get(error, str(error)) if error is not None else "unknown",
                    "sequence": sequence,
                }
            self._condition.notify_all()

    def wait_after(self, sequence: int, timeout_sec: float) -> Optional[PictureAck]:
        """First photo event published after ``sequence``.

        Parameters
        ----------
        sequence : int
            Mark read from :attr:`sequence` before the photo was requested.
        timeout_sec : float
            Longest wait, seconds. Non-positive values poll once.

        Returns
        -------
        dict or None
            ``{"acknowledged", "event", "error", "sequence"}``, or ``None`` when
            the aircraft reported nothing within the window.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        with self._condition:
            while True:
                event = self._event
                if event is not None and int(event["sequence"]) > sequence:
                    return dict(event)
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return None
                self._condition.wait(remaining)
