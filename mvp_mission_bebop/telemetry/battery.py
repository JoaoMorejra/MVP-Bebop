"""Battery state ingestion and the onboard low-charge safety net.

The Nectar SDK exposes no battery state for the Bebop: ``BebopDrone`` owns
publishers only, and the SDK's one battery reader
(``examples/control/navigation.py``) is a MAVROS snapshot of
``/mavros/battery``. The charge reaches ROS through the C++ driver instead,
which republishes the ARSDK ``CommonState.BatteryStateChanged`` event every
500 ms as ``sensor_msgs/BatteryState`` on ``/<namespace>/states/battery``
(``bebop_driver_node.cpp:227-252``), with ``present = false`` and a NaN
``percentage`` until the aircraft has reported a charge at all.

The ground station owns the battery failsafe and the RTL-versus-land decision
(``bebop_mission_control/src/lib/batteryFailsafe.ts``). This module is the
redundant net inside the mission process for the runs the station is not in
command of, and it does one thing: at ``battery.land_pct`` it asks
:meth:`FailsafeSupervisor.trigger_emergency_land` to land the aircraft where it
is. It never flies a return.

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
from typing import TYPE_CHECKING, Final, Optional, Union

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


class BatterySupervisor:
    """Owns the latest battery reading and the land-threshold decision.

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
        self._percentage: Optional[float] = None
        self._timestamp: float = 0.0
        self._warning_emitted = False

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

    def land_reason(self) -> Optional[str]:
        """Failure reason for :meth:`FailsafeSupervisor.evaluate_system_health`.

        Returns
        -------
        Optional[str]
            A reason when a fresh reading is at or below ``land_pct``; ``None``
            when the charge is above it or there is no fresh reading at all.
        """
        percentage = self.current_percentage()
        if percentage is None or percentage > self.land_pct:
            return None
        return (
            f"Battery at {percentage:.0f}% <= land threshold {self.land_pct:.0f}%; "
            "landing in place."
        )
