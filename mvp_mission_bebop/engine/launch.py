"""Launch of a pre-warmed mission: the go command and the shared takeoff deadline.

Measured: 40 s at the station from the click to the first countdown tick. The
mission's start-up -- SDK, driver link, camera, the detector on the MX110 --
ran after the click, under the load of the driver, the video bridges and the
speech cache. ``mission.py --standby`` does all of it beforehand and waits;
the click sends the parameter document and the click instant on stdin, and the
takeoff happens at that instant plus ``kinematics.countdown_sec``, a deadline
the station's overlay counts towards from the click as well.

Fields read while preparing cannot change at the go: a document that differs in
any of :data:`CRITICAL_PATHS` is refused, and the station spawns a fresh
mission instead.

The deadline is only as good as the instant it is read. A mission spawned cold
reads the click after its own 6-12 s start-up; pinned to it, the deadline was
born behind the mission and Stage 1 took off with no countdown at all. A click
older than :data:`MAX_GO_DELAY_SEC` pins nothing: Stage 1 counts the whole
countdown itself.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Final, List, Optional, Tuple

from mvp_mission_bebop.perception.inference_device import normalize_inference_device
from mvp_mission_bebop.perception.worker import normalize_imgsz

if TYPE_CHECKING:
    from mvp_mission_bebop.parameters import MissionParameters

#: Exit status of a standby mission that refused the go (critical mismatch).
EXIT_STANDBY_MISMATCH: Final[int] = 5

#: Exit status of a standby mission that failed precondition revalidation on go (R4b).
EXIT_STANDBY_STALE: Final[int] = 6

#: Longest the click may precede the deadline being pinned to it. Measured from
#: standby: the go reaches the mission and the first tick goes out 0.06 s after
#: the click; a cold spawn takes 6-12 s.
MAX_GO_DELAY_SEC: Final[float] = 1.0

#: Line the standby mission prints once prepared; the station waits for it.
STANDBY_READY_LINE: Final[str] = "[STANDBY] ready"

#: Parameters consumed while preparing: the arming mode, the network and
#: topics, the detector and the loops built before the go.
CRITICAL_PATHS: Final[Tuple[str, ...]] = (
    "no_fly",
    "network.drone_ip",
    "network.namespace",
    "network.camera_raw_topic",
    "network.odometry_topic",
    "vision.model_path",
    "vision.inference_device",
    "vision.inference_imgsz",
    "kinematics.control_loop_hz",
    "dead_reckoning.enabled",
)


_ABSENT: Final = object()


def _get(doc: Any, path: str) -> Any:
    node = doc
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return _ABSENT
        node = node[key]
    return node


def _normalized(path: str, value: Any) -> Any:
    try:
        if path == "vision.inference_device":
            return normalize_inference_device(value)
        if path == "vision.inference_imgsz":
            return normalize_imgsz(value)
    except (TypeError, ValueError):
        return ("invalid", repr(value))
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def critical_mismatches(prepared: "MissionParameters", doc: Dict[str, Any]) -> List[str]:
    """Paths of :data:`CRITICAL_PATHS` where ``doc`` differs from what was prepared.

    A path the document does not carry is not a mismatch: the merge keeps the
    prepared value.
    """
    current = prepared.to_dict()
    mismatched: List[str] = []
    for path in CRITICAL_PATHS:
        wanted = _get(doc, path)
        if wanted is _ABSENT:
            continue
        if _normalized(path, wanted) != _normalized(path, _get(current, path)):
            mismatched.append(path)
    return mismatched


def launch_deadline(launch_at_ms: Any, countdown_sec: float, now_wall: float, now_mono: float) -> Optional[float]:
    """Monotonic instant of the takeoff: the click plus the countdown.

    None when the click is more than :data:`MAX_GO_DELAY_SEC` old: the time
    between them went to a start-up nobody saw counted, and the countdown is
    never shortened by it. A countdown of zero is no countdown either way.

    Parameters
    ----------
    launch_at_ms : int
        Click instant, milliseconds since the epoch (the station's
        ``Date.now()``).
    countdown_sec : float
        ``kinematics.countdown_sec`` of the launch document.
    now_wall, now_mono : float
        ``time.time()`` and ``time.monotonic()`` read together.

    Returns
    -------
    float or None
        The deadline on the monotonic clock, or None for a stale click.

    Raises
    ------
    TypeError
        If ``launch_at_ms`` is not a number.
    ValueError
        If it is negative or not finite.
    """
    if isinstance(launch_at_ms, bool) or not isinstance(launch_at_ms, (int, float)):
        raise TypeError(f"launch_at_ms must be a number, got {type(launch_at_ms).__name__}")
    if not math.isfinite(launch_at_ms) or launch_at_ms < 0:
        raise ValueError(f"launch_at_ms must be a non-negative finite number, got {launch_at_ms!r}")
    countdown = max(0.0, float(countdown_sec))
    if countdown <= 0.0:
        return now_mono
    elapsed = max(0.0, now_wall - launch_at_ms / 1000.0)
    if elapsed > MAX_GO_DELAY_SEC:
        return None
    return now_mono + max(0.0, countdown - elapsed)


def parse_go_command(line: str) -> Dict[str, Any]:
    """One stdin command for a standby mission: ``go`` or ``quit``.

    ``go`` carries ``params`` (the launch document, an object) and
    ``launch_at_ms`` (a number).

    Raises
    ------
    ValueError
        For anything else.
    """
    try:
        command = json.loads(line)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a JSON command: {line!r}") from exc
    if not isinstance(command, dict):
        raise ValueError(f"not a command object: {line!r}")
    op: Optional[str] = command.get("op")
    if op == "quit":
        return {"op": "quit"}
    if op != "go":
        raise ValueError(f"unknown standby command {op!r}")
    params = command.get("params")
    launch_at = command.get("launch_at_ms")
    if not isinstance(params, dict):
        raise ValueError("go needs a params object")
    if isinstance(launch_at, bool) or not isinstance(launch_at, (int, float)):
        raise ValueError("go needs a numeric launch_at_ms")
    return {"op": "go", "params": params, "launch_at_ms": launch_at}


class CountdownTicker:
    """Emits the whole seconds left to a launch deadline, from the go onward.

    Measured from standby: the first tick came 3 s after the go, because
    Stage 1 waits for the flat-trim acknowledgement before its countdown loop
    starts. The ticks now follow the deadline on a thread of their own, so the
    station's overlay counts from the click whatever Stage 1 is doing; the
    countdown loop keeps the clearance call and the final zero, the takeoff.

    Parameters
    ----------
    deadline : float
        Monotonic takeoff instant (:func:`launch_deadline`).
    emit : callable
        Called with each whole number of seconds left, ``> 0``, once each.

    Attributes
    ----------
    shown : bool
        At least one tick went out: the window up to the deadline was counted
        where the operator sees it. Stage 1 takes off on a passed deadline only
        then.
    """

    def __init__(self, deadline: float, emit: Callable[[int], None]) -> None:
        self._deadline = deadline
        self._emit = emit
        self.shown = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="CountdownTicker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(1.0)

    def _run(self) -> None:
        last: Optional[int] = None
        while not self._stop.is_set():
            remaining = self._deadline - time.monotonic()
            whole = int(math.ceil(remaining))
            if whole <= 0:
                return
            if whole != last:
                last = whole
                self._emit(whole)
                self.shown = True
            self._stop.wait(min(0.05, max(0.0, remaining - (whole - 1))))
