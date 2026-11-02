"""Battery state ingestion and the onboard low-charge safety net.

The Nectar SDK exposes no battery state for the Bebop: ``BebopDrone`` owns
publishers only, and the SDK's one battery reader
(``examples/control/navigation.py``) is a MAVROS snapshot of
``/mavros/battery``. The charge reaches ROS through the C++ driver instead,
which republishes the ARSDK ``CommonState.BatteryStateChanged`` event every
500 ms as ``sensor_msgs/BatteryState`` on ``/<namespace>/states/battery``
(``bebop_driver_node.cpp:227-252``), with ``present = false`` and a NaN
``percentage`` until the aircraft has reported a charge at all.

The mission process is the single authority for the critical-charge return.
At ``battery.land_pct``, with the airframe in the air, this module calls a
one-shot hook (:meth:`BatterySupervisor.set_return_hook`) wired by
``mission.py`` to ``MissionContext.request_battery_return_or_land``, which
jumps the runner to Stage 5 through the station's stage gate and marks the
return as forced, so Stage 5 flies the return of the active profile even with
``rtl.enabled = false``. The charge is not a health condition: the airframe
never lands in place because of it, only at the end of the return. The ground
station (``bebop_mission_control/src/lib/batteryFailsafe.ts``) keeps its own
trigger as redundancy. This module holds no actuator and commands nothing.

A charge the aircraft has not reported is not a reading to act on. Absent or
stale telemetry makes this module silent -- no warning, no failsafe -- and the
flight continues on the odometry and video supervisors, which already own link
loss.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Callable, Final, Optional, Union

from mvp_mission_bebop.parameters import BatteryConfig
from mvp_mission_bebop.telemetry.milestones import emit_milestone
from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

if TYPE_CHECKING:  # pragma: no cover - import kept out of the runtime path
    from sensor_msgs.msg import BatteryState

logger = logging.getLogger("BatterySupervisor")

#: Charge, in percent, at which the low-battery warning is raised. Fixed by
#: design and deliberately not ``battery.land_pct``: crossing it logs and emits
#: ``mission.battery_warning`` once, and the mission carries on unchanged.
BATTERY_WARNING_PCT: Final[float] = 10.0

#: Age, in seconds, past which the last valid reading stops counting as one.
#: Six publication periods of the driver's 2 Hz state timer, so a single late
#: callback on a loaded executor does not read as a lost stream.
BATTERY_STALE_TIMEOUT_SEC: Final[float] = 3.0


def normalize_percentage(value: Union[int, float, str], name: str = "percentage") -> float:
    """Convert a charge threshold or reading into a validated percentage.

    Parameters
    ----------
    value : Union[int, float, str]
        Charge in percent. Strings are accepted because ``--params-json`` and a
        hand-edited ``mission_config.json`` may carry the figure quoted.
    name : str
        Field name used in error messages.

    Returns
    -------
    float
        The value as a float in ``[0, 100]``.

    Raises
    ------
    TypeError
        If ``value`` is a boolean, ``None``, or any other non-numeric type.
    ValueError
        If ``value`` is a non-numeric string, non-finite, or outside ``[0, 100]``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    try:
        percentage = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not numeric: {value!r}") from exc
    if not math.isfinite(percentage):
        raise ValueError(f"{name} must be finite, got {percentage!r}")
    if not 0.0 <= percentage <= 100.0:
        raise ValueError(f"{name} must lie in [0, 100], got {percentage:g}")
    return percentage


def normalize_enabled(value: Union[bool, int, str, None], name: str = "battery.enabled") -> bool:
    """Convert the failsafe switch into a boolean.

    Parameters
    ----------
    value : Union[bool, int, str, None]
        The switch. ``None`` (a payload from a station that predates the
        switch) reads as ``True``. The strings ``"true"`` and ``"false"``,
        any case, are accepted because ``--params-json`` and a hand-edited
        ``mission_config.json`` may carry the value quoted.
    name : str
        Field name used in error messages.

    Returns
    -------
    bool
        The switch.

    Raises
    ------
    TypeError
        For any other value, including numbers and other strings: a non-empty
        string such as ``"no"`` must never read as ``True`` by truthiness.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token == "true":
            return True
        if token == "false":
            return False
    raise TypeError(f"{name} must be a boolean or 'true'/'false', got {value!r}")


class BatterySupervisor:
    """Owns the latest battery reading and the critical-charge return request.

    Parameters
    ----------
    battery_cfg : BatteryConfig
        Source of ``land_pct``. Validated here, so a malformed threshold fails
        on the ground at construction rather than inside a flight stage.
    stale_timeout_sec : float
        Freshness window for a reading, in seconds.

    Raises
    ------
    TypeError, ValueError
        If ``battery_cfg.land_pct`` is not a valid percentage, or the timeout
        is not a positive finite number.
    """

    def __init__(
        self,
        battery_cfg: BatteryConfig,
        stale_timeout_sec: float = BATTERY_STALE_TIMEOUT_SEC,
    ) -> None:
        if not math.isfinite(stale_timeout_sec) or stale_timeout_sec <= 0.0:
            raise ValueError(f"stale_timeout_sec must be positive, got {stale_timeout_sec!r}")
        self.land_pct: float = normalize_percentage(battery_cfg.land_pct, "battery.land_pct")
        self.stale_timeout_sec = float(stale_timeout_sec)

        self._lock = threading.Lock()
        self._enabled: bool = normalize_enabled(getattr(battery_cfg, "enabled", None))
        self._percentage: Optional[float] = None
        self._timestamp: float = 0.0
        self._warning_emitted = False
        self._return_hook: Optional[Callable[[str], bool]] = None
        self._airborne: Optional[Callable[[], bool]] = None
        self._return_requested = False

    # ------------------------------------------------------------------ switch

    @property
    def enabled(self) -> bool:
        """Whether the critical-charge return and the pre-flight gate act."""
        with self._lock:
            return self._enabled

    def configure(
        self,
        *,
        enabled: Optional[Union[bool, str]] = None,
        land_pct: Optional[Union[int, float, str]] = None,
    ) -> None:
        """Change the switch or the threshold, typically from the station in flight.

        Both values are validated before either is applied, so a refused call
        leaves the supervisor unchanged. Switching off never cancels a return
        already requested.

        Parameters
        ----------
        enabled : Optional[Union[bool, str]]
            New switch; ``None`` keeps the current one.
        land_pct : Optional[Union[int, float, str]]
            New threshold in percent; ``None`` keeps the current one.

        Raises
        ------
        TypeError, ValueError
            If a value is invalid (see :func:`normalize_enabled` and
            :func:`normalize_percentage`).
        """
        switch = normalize_enabled(enabled) if enabled is not None else None
        threshold = normalize_percentage(land_pct, "battery.land_pct") if land_pct is not None else None
        with self._lock:
            if switch is not None:
                self._enabled = switch
            if threshold is not None:
                self.land_pct = threshold
        logger.info(
            "Battery failsafe %s, return threshold %.0f%%.",
            "enabled" if self.enabled else "disabled",
            self.land_pct,
        )

    # ------------------------------------------------------------------ wiring

    def set_return_hook(
        self, hook: Callable[[str], bool], airborne: Callable[[], bool]
    ) -> None:
        """Install the callable that requests the critical-charge return.

        Parameters
        ----------
        hook : Callable[[str], bool]
            Called once per flight with :meth:`critical_reason` when a fresh
            reading is at or below ``land_pct`` and ``airborne()`` is true. It
            runs on the executor thread that delivered the reading. Its return
            value says whether the return was requested; ``False`` means the
            caller has already handled the refusal itself.
        airborne : Callable[[], bool]
            Whether the airframe is in the air. On the ground the hook never
            fires: the Stage 1 pre-arm gate refuses that launch instead.

        Raises
        ------
        TypeError
            If either argument is not callable.
        """
        if not callable(hook):
            raise TypeError(f"hook must be callable, got {type(hook).__name__}")
        if not callable(airborne):
            raise TypeError(f"airborne must be callable, got {type(airborne).__name__}")
        with self._lock:
            self._return_hook = hook
            self._airborne = airborne

    def reset_return(self) -> None:
        """Rearm the one-shot return request for a new flight."""
        with self._lock:
            self._return_requested = False

    @property
    def return_requested(self) -> bool:
        """True once the hook has been called this flight."""
        with self._lock:
            return self._return_requested

    # --------------------------------------------------------------- ingestion

    def battery_callback(self, msg: "BatteryState") -> None:
        """Ingest a ``sensor_msgs/BatteryState`` message. Runs on an executor thread.

        ``present = false`` is the driver's "never reported" marker and is
        dropped without refreshing the heartbeat. ``percentage`` is a fraction
        in ``[0, 1]`` per the message definition; anything outside it, or
        non-finite, is dropped the same way rather than clamped into a reading.
        """
        if not msg.present:
            return
        fraction = float(msg.percentage)
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            logger.debug("Discarding battery sample with percentage %r.", fraction)
            return
        self._store(fraction * 100.0)

    def inject_synthetic_reading(self, percentage: Union[int, float, str]) -> None:
        """Feed a reading in percent, bypassing the ROS message.

        Raises
        ------
        TypeError, ValueError
            If ``percentage`` is not a valid percentage.
        """
        self._store(normalize_percentage(percentage))

    def _store(self, percentage: float) -> None:
        with self._lock:
            self._percentage = percentage
            self._timestamp = time.monotonic()
        if percentage <= BATTERY_WARNING_PCT:
            self._raise_warning(percentage)
        if self.enabled and percentage <= self.land_pct:
            self._request_return()

    def _request_return(self) -> None:
        """Call the return hook once per flight, airborne only.

        The flag is taken under the lock before the call, so two readings
        delivered on different executor threads cannot both request the
        return. A hook that raises rearms the request: the next reading, one
        driver state period later, tries again rather than leaving the
        airframe without a return.
        """
        with self._lock:
            hook = self._return_hook
            airborne = self._airborne
            if hook is None or airborne is None or self._return_requested:
                return
        try:
            in_flight = bool(airborne())
        except Exception as exc:  # noqa: BLE001 - an unreadable altitude is not a reason to act
            logger.warning("Airborne state unavailable (%s); return not requested on this reading.", exc)
            return
        if not in_flight:
            return
        reason = self.critical_reason()
        if reason is None:
            return
        with self._lock:
            if self._return_requested:
                return
            self._return_requested = True
        try:
            hook(reason)
        except Exception as exc:  # noqa: BLE001 - retried on the next reading
            logger.error("Battery return request failed (%s); retrying on the next reading.", exc)
            with self._lock:
                self._return_requested = False

    def _raise_warning(self, percentage: float) -> None:
        """Log and emit ``mission.battery_warning`` once per flight."""
        with self._lock:
            if self._warning_emitted:
                return
            self._warning_emitted = True
        logger.warning(
            "Battery at %.0f%% (warning threshold %.0f%%). Mission continues unchanged.",
            percentage,
            BATTERY_WARNING_PCT,
        )
        emit_milestone(
            "mission.battery_warning",
            {"battery_pct": round(percentage, 1), "threshold_pct": BATTERY_WARNING_PCT},
        )

    # ------------------------------------------------------------------ health

    def telemetry_health(self) -> TelemetryHealth:
        """Classify the battery stream, on the same scale as odometry."""
        with self._lock:
            percentage = self._percentage
            last = self._timestamp
        if percentage is None:
            return TelemetryHealth.NEVER_RECEIVED
        if (time.monotonic() - last) > self.stale_timeout_sec:
            return TelemetryHealth.STALE
        return TelemetryHealth.HEALTHY

    def current_percentage(self) -> Optional[float]:
        """Latest charge in percent, or ``None`` when absent or stale."""
        if not self.telemetry_health().is_healthy:
            return None
        with self._lock:
            return self._percentage

    @property
    def warning_emitted(self) -> bool:
        """True once ``mission.battery_warning`` has gone out this flight."""
        with self._lock:
            return self._warning_emitted

    # ---------------------------------------------------------------- decision

    def critical_reason(self) -> Optional[str]:
        """Reason handed to the return hook.

        Returns
        -------
        Optional[str]
            A reason when the failsafe is enabled and a fresh reading is at or
            below ``land_pct``; ``None`` when disabled, when the charge is
            above it or when there is no fresh reading at all.
        """
        if not self.enabled:
            return None
        percentage = self.current_percentage()
        if percentage is None or percentage > self.land_pct:
            return None
        return (
            f"Battery at {percentage:.0f}% <= return threshold {self.land_pct:.0f}%; "
            "returning to base."
        )
