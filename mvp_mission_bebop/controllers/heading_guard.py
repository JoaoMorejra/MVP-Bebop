"""Heading telemetry of the Advanced terminal phases (centring and descent).

Diagnostic only: nothing here changes what is flown. The mission never
commands yaw in Stage 5 (``vyaw`` is a literal zero on every cycle and the
keyboard nudge is suspended during the precision landing), yet two kinds of
heading event were logged near the pad:

* 2026-11-03 06:14 -- the odometric heading (``/bebop/odom`` orientation, the
  firmware's magnetometer-fused estimate) moved -24 deg in 2.5 s while the
  marker's planar yaw in the image stayed within 1.3 deg and the body-frame
  marker error evolved as a translation: the estimate drifted, the airframe
  did not rotate. A guard that landed on that reading put the aircraft 0.8 m
  from the marker.
* 2026-11-03 05:50 -- the marker error vector rotated about 45 deg in 1.2 s in
  the descent: a physical rotation the mission did not command.

The centring and the descent act on the marker's instantaneous body-frame
error, which stays exact under rotation, exactly as the Simple profile does;
the odometric heading is never an input. This class only measures the two
excursions from the start of the centring so the log can tell an estimate
drift from a real rotation:

* the odometric yaw (``/bebop/odom`` orientation, the firmware's estimate);
* the marker's planar yaw in the image (``Aruco.calculateYawFromCorners``),
  compass-free and camera-relative.

A "violation" is latched for one log warning. Pure mathematics: no clock, no
ROS, no actuator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional

#: Odometric heading excursion reported as an event, degrees.
ODOM_HEADING_LIMIT_DEG: Final[float] = 20.0

#: Marker planar-yaw excursion reported as an event, degrees.
MARKER_HEADING_LIMIT_DEG: Final[float] = 30.0

#: Consecutive marker cycles beyond :data:`MARKER_HEADING_LIMIT_DEG` required.
MARKER_CONFIRM_CYCLES: Final[int] = 3


def wrap_deg(angle_deg: float) -> float:
    """``angle_deg`` wrapped into ``(-180, 180]`` degrees."""
    wrapped = math.fmod(angle_deg + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def _finite(value: Optional[float]) -> Optional[float]:
    """``value`` as a finite float, or ``None``."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class HeadingReading:
    """One cycle of heading supervision.

    Parameters
    ----------
    odom_delta_deg : Optional[float]
        Odometric heading change since the reference, degrees; ``None`` without
        odometric yaw.
    marker_delta_deg : Optional[float]
        Marker planar-yaw change since the reference, degrees; ``None`` when
        the marker was not seen this cycle.
    violated : bool
        The airframe has rotated beyond a limit. Latched until :meth:`HeadingGuard.reset`.
    note : str
        Human-readable reason.
    """

    odom_delta_deg: Optional[float]
    marker_delta_deg: Optional[float]
    violated: bool
    note: str


class HeadingGuard:
    """Latch a rotation of the airframe away from the heading of the phase start.

    Parameters
    ----------
    odom_limit_deg : float
        Odometric excursion limit, degrees. Strictly positive.
    marker_limit_deg : float
        Marker planar-yaw excursion limit, degrees. Strictly positive.
    marker_confirm_cycles : int
        Consecutive marker cycles beyond the limit required. At least one.

    Raises
    ------
    ValueError
        If a limit is not finite and strictly positive, or the confirmation
        count is below one.
    """

    def __init__(
        self,
        odom_limit_deg: float = ODOM_HEADING_LIMIT_DEG,
        marker_limit_deg: float = MARKER_HEADING_LIMIT_DEG,
        marker_confirm_cycles: int = MARKER_CONFIRM_CYCLES,
    ) -> None:
        for name, value in (("odom_limit_deg", odom_limit_deg), ("marker_limit_deg", marker_limit_deg)):
            checked = _finite(value)
            if checked is None or checked <= 0.0:
                raise ValueError(f"{name} must be finite and strictly positive, got {value!r}")
        if isinstance(marker_confirm_cycles, bool) or int(marker_confirm_cycles) < 1:
            raise ValueError(f"marker_confirm_cycles must be at least 1, got {marker_confirm_cycles!r}")
        self._odom_limit = float(odom_limit_deg)
        self._marker_limit = float(marker_limit_deg)
        self._confirm = int(marker_confirm_cycles)
        self.reset()

    def reset(self) -> None:
        """Forget both references and clear the latch."""
        self._odom_ref: Optional[float] = None
        self._marker_ref: Optional[float] = None
        self._marker_count: int = 0
        self._violated: bool = False
        self._note: str = ""

    @property
    def violated(self) -> bool:
        """Whether a rotation has been latched."""
        return self._violated

    def update(self, odom_yaw_rad: Optional[float], marker_yaw_deg: Optional[float]) -> HeadingReading:
        """Advance one cycle.

        Parameters
        ----------
        odom_yaw_rad : Optional[float]
            Odometric heading, radians, or ``None``.
        marker_yaw_deg : Optional[float]
            Marker planar yaw this cycle, degrees, or ``None`` when not seen.

        Returns
        -------
        HeadingReading
            The excursions and whether the airframe has rotated beyond a limit.
        """
        odom = _finite(odom_yaw_rad)
        marker = _finite(marker_yaw_deg)

        odom_delta: Optional[float] = None
        if odom is not None:
            odom_deg = math.degrees(odom)
            if self._odom_ref is None:
                self._odom_ref = odom_deg
            odom_delta = wrap_deg(odom_deg - self._odom_ref)

        marker_delta: Optional[float] = None
        if marker is not None:
            if self._marker_ref is None:
                self._marker_ref = marker
            marker_delta = wrap_deg(marker - self._marker_ref)
            self._marker_count = self._marker_count + 1 if abs(marker_delta) > self._marker_limit else 0

        if not self._violated:
            if odom_delta is not None and abs(odom_delta) > self._odom_limit:
                self._violated = True
                self._note = f"odometric heading moved {odom_delta:+.1f} deg (limit {self._odom_limit:.0f})"
            elif self._marker_count >= self._confirm and marker_delta is not None:
                self._violated = True
                self._note = (
                    f"marker planar yaw moved {marker_delta:+.1f} deg over {self._marker_count} cycles "
                    f"(limit {self._marker_limit:.0f})"
                )

        return HeadingReading(odom_delta, marker_delta, self._violated, self._note)

    def describe(self, reading: HeadingReading) -> str:
        """Compact ``hdg`` field for the control log."""
        odom = f"{reading.odom_delta_deg:+.1f}" if reading.odom_delta_deg is not None else "nan"
        marker = f"{reading.marker_delta_deg:+.1f}" if reading.marker_delta_deg is not None else "nan"
        return f"hdg odom {odom} marker {marker} deg"
