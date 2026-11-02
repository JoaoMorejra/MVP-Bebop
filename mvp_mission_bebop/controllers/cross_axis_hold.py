"""Retention of the axis orthogonal to a translational leg.

The lateral steps transmit the orthogonal axis as a literal ``0.0``. With zero
pitch commanded the firmware does not hold position on that axis while the
other one translates: wind, thrust asymmetry and trim error become a diagonal
drift that nothing measures. This law closes the orthogonal axis on the
odometric body velocity while the main axis is commanded:

    correction = -(kp_velocity * v_cross + ki * integral(v_cross))

saturated at ``max_correction`` (0.03 normalized, about three times the driver's
0.01 quantum) and active only while ``|main_command| >= min_active_speed``; in
hover the firmware holds position on its own. The integral is clamped to
``max_correction / ki`` (anti-windup) and cleared when the law goes inactive.

An optional heading term (``heading_hold_enabled``, off by default) returns a
yaw correction ``-heading_kp * yaw_error`` outside ``heading_deadband_deg``,
bounded by ``heading_max_vyaw``. It is off because a yaw rate corrupts the
optical-flow odometry (``actuators/yaw_authority.py``); it is meant to be
enabled only after flight data shows heading drift on the lateral leg, and only
inside a supervisor heading window.
"""

from __future__ import annotations

import math
from typing import Any, Final, Optional, Tuple

_AXES: Final[Tuple[str, str]] = ("vx", "vy")


def body_velocity(vx_world: float, vy_world: float, yaw_rad: float) -> Tuple[float, float]:
    """Rotate an odometric velocity from the odometry frame into body FLU.

    ``/bebop/odom`` reports velocity in the odometry frame (the kinematic
    simulator publishes it the same way), so the forward and left components
    are ``R(-yaw) * v``.

    Parameters
    ----------
    vx_world, vy_world : float
        Velocity in the odometry frame, m/s.
    yaw_rad : float
        Heading, radians.

    Returns
    -------
    Tuple[float, float]
        ``(forward, left)``, m/s.
    """
    c, s = math.cos(yaw_rad), math.sin(yaw_rad)
    return c * vx_world + s * vy_world, -s * vx_world + c * vy_world


class CrossAxisHold:
    """Orthogonal-velocity and optional heading retention for a translational leg.

    Parameters
    ----------
    cfg : TranslationHoldConfig
        ``enabled``, ``kp_velocity``, ``ki``, ``max_correction``,
        ``min_active_speed``, ``heading_hold_enabled``, ``heading_kp``,
        ``heading_max_vyaw``, ``heading_deadband_deg``.

    Raises
    ------
    TypeError
        If a gain is not numeric or a flag is not boolean.
    ValueError
        If a gain or limit is negative or not finite, or ``max_correction`` or
        ``heading_max_vyaw`` exceeds 1.
    """

    def __init__(self, cfg: Any) -> None:
        for flag in ("enabled", "heading_hold_enabled"):
            if not isinstance(getattr(cfg, flag), bool):
                raise TypeError(f"translation_hold.{flag} must be a bool")
        values = {}
        for name in (
            "kp_velocity",
            "ki",
            "max_correction",
            "min_active_speed",
            "heading_kp",
            "heading_max_vyaw",
            "heading_deadband_deg",
        ):
            value = getattr(cfg, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"translation_hold.{name} must be a number, got {type(value).__name__}")
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"translation_hold.{name} must be finite and >= 0, got {value!r}")
            values[name] = float(value)
        if values["max_correction"] > 1.0 or values["heading_max_vyaw"] > 1.0:
            raise ValueError("translation_hold limits are normalized and must not exceed 1")
        self._enabled: bool = cfg.enabled
        self._heading_enabled: bool = cfg.heading_hold_enabled
        self._kp: float = values["kp_velocity"]
        self._ki: float = values["ki"]
        self._max: float = values["max_correction"]
        self._min_active: float = values["min_active_speed"]
        self._heading_kp: float = values["heading_kp"]
        self._heading_max: float = values["heading_max_vyaw"]
        self._heading_deadband: float = values["heading_deadband_deg"]
        self._integral: float = 0.0

    @property
    def enabled(self) -> bool:
        """Whether the law produces any correction."""
        return self._enabled

    @property
    def integral(self) -> float:
        """Integrated orthogonal velocity, m."""
        return self._integral

    def reset(self) -> None:
        """Clear the integrator."""
        self._integral = 0.0

    def update(
        self,
        main_axis: str,
        main_command: float,
        cross_velocity_mps: float,
        yaw_error_deg: Optional[float],
        dt: float,
    ) -> Tuple[float, float]:
        """Corrections for one cycle.

        Parameters
        ----------
        main_axis : str
            ``"vx"`` or ``"vy"``: the axis the leg translates on.
        main_command : float
            Normalized command on the main axis.
        cross_velocity_mps : float
            Measured body velocity on the orthogonal axis, m/s.
        yaw_error_deg : Optional[float]
            Heading minus the reference heading of the leg, degrees; ``None``
            when unknown.
        dt : float
            Elapsed interval, s.

        Returns
        -------
        Tuple[float, float]
            ``(cross_correction, yaw_correction)``, normalized. ``(0.0, 0.0)``
            when disabled, inactive or fed a non-finite input.

        Raises
        ------
        ValueError
            If ``main_axis`` is not ``"vx"`` or ``"vy"``.
        """
        if main_axis not in _AXES:
            raise ValueError(f"main_axis must be 'vx' or 'vy', got {main_axis!r}")
        if not self._enabled:
            return 0.0, 0.0
        try:
            main = float(main_command)
            cross = float(cross_velocity_mps)
            step = float(dt)
        except (TypeError, ValueError):
            return 0.0, 0.0
        if not (math.isfinite(main) and math.isfinite(cross) and math.isfinite(step)) or step < 0.0:
            return 0.0, 0.0
        if abs(main) < self._min_active:
            self._integral = 0.0
            return 0.0, 0.0

        if self._ki > 0.0:
            bound = self._max / self._ki
            self._integral = max(-bound, min(bound, self._integral + cross * step))
        correction = -(self._kp * cross + self._ki * self._integral)
        correction = max(-self._max, min(self._max, correction))

        yaw = 0.0
        if self._heading_enabled and yaw_error_deg is not None:
            try:
                error = float(yaw_error_deg)
            except (TypeError, ValueError):
                error = math.nan
            if math.isfinite(error) and abs(error) > self._heading_deadband:
                yaw = max(-self._heading_max, min(self._heading_max, -self._heading_kp * math.radians(error)))
        return correction, yaw
