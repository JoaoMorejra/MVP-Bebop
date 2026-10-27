"""Camera gimbal state readback and the check that a commanded tilt was reached.

The Bebop 2 gimbal is commanded open loop: ``camera_control`` publishes on
``/<namespace>/move_camera`` and the ARSDK answers nothing the mission could
wait on. The ARSDK documents that the drone saturates the requested tilt at the
limits it reports in ``CameraSettingsChanged``, and the controller sends each
setpoint in a burst of ten packets, so a commanded angle can be dropped or
clamped with nothing on the mission side to say so. The marker-guided return
needs the camera near nadir (``rtl.camera_tilt_deg``, -80 deg by default): a
camera that stops tens of degrees short looks at the horizon, never sees the
pad marker, and the centering law holds station for its whole window.

The driver republishes what the aircraft reports:

* ``/<namespace>/states/ardrone3/CameraState/OrientationV2``
  (``geometry_msgs/Vector3``: ``x`` tilt, ``y`` pan, degrees);
* ``/<namespace>/states/ardrone3/CameraState/Limits``
  (``std_msgs/Float32MultiArray``: ``[tilt_min, tilt_max, pan_min, pan_max]``).

:class:`GimbalStateMonitor` keeps the latest of each and answers whether the
camera is at the angle the mission asked for. A missing reading is never a
verdict: a driver that does not publish the topics leaves the step flying open
loop, as before.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Final, Optional, Tuple

logger = logging.getLogger("GimbalStateMonitor")

#: Subtopic of the aircraft namespace carrying the reported camera orientation.
ORIENTATION_TOPIC_SUFFIX: Final[str] = "states/ardrone3/CameraState/OrientationV2"

#: Subtopic carrying ``[tilt_min, tilt_max, pan_min, pan_max]``.
LIMITS_TOPIC_SUFFIX: Final[str] = "states/ardrone3/CameraState/Limits"

#: Age, in seconds, past which the last orientation stops counting as a reading.
#: The driver republishes at 2 Hz and the topic is also sent on every change.
GIMBAL_STALE_TIMEOUT_SEC: Final[float] = 2.0


@dataclass(frozen=True)
class GimbalOutcome:
    """Result of :meth:`GimbalStateMonitor.wait_for_tilt`.

    Attributes
    ----------
    reached : bool
        The reported tilt came within the tolerance of the target.
    actual : Optional[float]
        Last reported tilt in degrees, or ``None`` when none was available.
    waited : float
        Seconds spent waiting.
    telemetry : bool
        Whether any orientation reading was available at all.
    interrupted : bool
        The wait ended because the step was interrupted.
    """

    reached: bool
    actual: Optional[float]
    waited: float
    telemetry: bool
    interrupted: bool

    def describe(self, target: float) -> str:
        """One log line stating what the camera did against ``target`` degrees."""
        if not self.telemetry:
            return f"no gimbal telemetry; the {target:.1f} deg command cannot be confirmed"
        assert self.actual is not None
        if self.reached:
            return f"gimbal confirmed at {self.actual:.1f} deg (commanded {target:.1f}) after {self.waited:.2f} s"
        cause = "interrupted" if self.interrupted else f"not reached after {self.waited:.1f} s"
        return f"gimbal at {self.actual:.1f} deg, commanded {target:.1f} ({cause})"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class GimbalStateMonitor:
    """Latest camera orientation and tilt limits reported by the aircraft.

    Parameters
    ----------
    stale_timeout_sec : float
        Age past which the last orientation no longer counts. Must be positive.
    clock : Callable[[], float]
        Monotonic clock, injectable for tests.

    Raises
    ------
    TypeError
        If ``stale_timeout_sec`` is not a number or ``clock`` is not callable.
    ValueError
        If ``stale_timeout_sec`` is not a positive finite number.
    """

    def __init__(
        self,
        stale_timeout_sec: float = GIMBAL_STALE_TIMEOUT_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(stale_timeout_sec, bool) or not isinstance(stale_timeout_sec, (int, float)):
            raise TypeError(f"stale_timeout_sec must be a number, got {type(stale_timeout_sec).__name__}")
        if not math.isfinite(stale_timeout_sec) or stale_timeout_sec <= 0.0:
            raise ValueError("stale_timeout_sec must be a positive finite number")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.stale_timeout_sec = float(stale_timeout_sec)
        self._clock = clock
        self._lock = threading.Lock()
        self._tilt: Optional[float] = None
        self._pan: Optional[float] = None
        self._stamp: float = 0.0
        self._limits: Optional[Tuple[float, float]] = None

    # ------------------------------------------------------------- ingestion

    def orientation_callback(self, message: Any) -> None:
        """Store a ``geometry_msgs/Vector3`` orientation; non-finite tilt is dropped."""
        tilt = _finite(getattr(message, "x", None))
        if tilt is None:
            return
        pan = _finite(getattr(message, "y", None))
        with self._lock:
            self._tilt = tilt
            self._pan = pan
            self._stamp = self._clock()

    def limits_callback(self, message: Any) -> None:
        """Store ``[tilt_min, tilt_max, pan_min, pan_max]``; malformed arrays are dropped."""
        data = getattr(message, "data", None)
        try:
            values = [_finite(v) for v in list(data)]
        except TypeError:
            return
        if len(values) < 4 or values[0] is None or values[1] is None or values[0] >= values[1]:
            return
        with self._lock:
            self._limits = (values[0], values[1])

    # --------------------------------------------------------------- queries

    def current_tilt(self) -> Optional[float]:
        """Reported tilt in degrees, or ``None`` when absent or stale."""
        with self._lock:
            tilt, stamp = self._tilt, self._stamp
        if tilt is None or (self._clock() - stamp) > self.stale_timeout_sec:
            return None
        return tilt

    def tilt_limits(self) -> Optional[Tuple[float, float]]:
        """``(tilt_min, tilt_max)`` the aircraft reported, or ``None``."""
        with self._lock:
            return self._limits

    # ------------------------------------------------------------------ wait

    def wait_for_tilt(
        self,
        target: float,
        tolerance: float,
        timeout: float,
        assert_command: Callable[[], None],
        interrupted: Callable[[], bool],
        period: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> GimbalOutcome:
        """Re-assert ``target`` until the aircraft reports it, or ``timeout`` elapses.

        The command is repeated every ``period`` seconds because the controller
        sends each setpoint in a bounded burst and a lost burst is otherwise
        final. Without any orientation reading the wait returns at once with
        ``telemetry=False``: no reading is not a reason to hold a flight leg.

        Parameters
        ----------
        target : float
            Commanded tilt in degrees.
        tolerance : float
            Accepted distance from ``target``, in degrees; non-negative.
        timeout : float
            Longest wait in seconds; positive.
        assert_command : Callable[[], None]
            Re-sends the gimbal command. Any exception it raises is logged and
            does not end the wait.
        interrupted : Callable[[], bool]
            True when the running step must stop early.
        period : float
            Seconds between checks and re-assertions.
        sleep : Callable[[float], None]
            Injectable sleep, for tests.

        Returns
        -------
        GimbalOutcome

        Raises
        ------
        TypeError
            If ``target``, ``tolerance`` or ``timeout`` is not a number, or a
            callable argument is not callable.
        ValueError
            If a numeric argument is out of range.
        """
        for name, value in (("target", target), ("tolerance", tolerance), ("timeout", timeout), ("period", period)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a number, got {type(value).__name__}")
        if not math.isfinite(target):
            raise ValueError("target must be finite")
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError("tolerance must be a non-negative finite number")
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("timeout must be a positive finite number")
        if not math.isfinite(period) or period <= 0.0:
            raise ValueError("period must be a positive finite number")
        if not callable(assert_command) or not callable(interrupted) or not callable(sleep):
            raise TypeError("assert_command, interrupted and sleep must be callable")

        actual = self.current_tilt()
        if actual is None:
            return GimbalOutcome(False, None, 0.0, False, False)

        waited = 0.0
        while waited < timeout:
            if abs(actual - target) <= tolerance:
                return GimbalOutcome(True, actual, waited, True, False)
            if interrupted():
                return GimbalOutcome(False, actual, waited, True, True)
            try:
                assert_command()
            except Exception as exc:  # noqa: BLE001 - a failed re-send must not end the wait
                logger.warning("Gimbal re-assert failed: %s", exc)
            sleep(period)
            waited += period
            latest = self.current_tilt()
            if latest is not None:
                actual = latest
        return GimbalOutcome(abs(actual - target) <= tolerance, actual, waited, True, False)
