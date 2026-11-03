"""Blind closure of the visibility stand-off before the Advanced touchdown.

The Advanced return flies the gimbal at
:data:`~mvp_mission_bebop.controllers.rtl_guidance.ADVANCED_RETURN_TILT_DEG`,
twenty degrees short of nadir. At that depression the marker cannot sit under
the CoG with its quiet zone inside the frame at the return heights, so the
centring and the tracked descent hold the marker at the visibility stand-off
(:func:`~mvp_mission_bebop.controllers.rtl_guidance.visibility_standoff_m`)
instead of over the CoG. What remains at the commit height is a known,
metric, body-frame residual of one to two decimetres, measured by the last
sighting and carried forward by the centring law with the measured velocity.

This law flies that residual out without the camera. Displacement is the
integral of the measured body velocity over a few seconds -- the short-horizon
regime where the optical-flow odometry is trustworthy, unlike the minutes of
integration the return leg itself refuses to rely on. Without a measured
velocity the displacement is the integral of the command it sent.

Pure mathematics: no clock, no ROS, no actuator. Units are metres and metres
per second, body FLU, forward and left positive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional, Tuple

#: Proportional gain from the remaining distance to the commanded speed, 1/s.
#: Below the speed ceiling the approach is a first-order decay; 0.8 1/s takes a
#: 0.15 m residual into a 0.04 m tolerance in under two seconds.
CLOSURE_GAIN_PER_SEC: Final[float] = 0.8


@dataclass(frozen=True)
class ClosureCommand:
    """One cycle of the stand-off closure.

    Parameters
    ----------
    vx_mps, vy_mps : float
        Commanded body velocity, m/s, forward and left positive.
    remaining_forward_m, remaining_left_m : float
        Displacement still to fly, metres.
    done : bool
        The residual is inside the tolerance, or the airframe has passed the
        target along the initial direction. The command is zero.
    note : str
        Human-readable reason.
    """

    vx_mps: float
    vy_mps: float
    remaining_forward_m: float
    remaining_left_m: float
    done: bool
    note: str

    @property
    def remaining_m(self) -> float:
        """Planar distance still to fly, metres."""
        return math.hypot(self.remaining_forward_m, self.remaining_left_m)


def _finite(name: str, value: float) -> float:
    """``value`` as a finite float, or raise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {number!r}")
    return number


class StandoffClosure:
    """Fly a body-frame displacement on integrated odometry, then stop.

    Parameters
    ----------
    forward_m, left_m : float
        Displacement to fly, metres, body FLU: the marker position relative to
        the CoG at the last sighting, net of the motion since.
    max_speed_mps : float
        Ceiling on the resultant commanded speed, m/s. Strictly positive.
    tolerance_m : float
        Remaining distance at which the closure is complete, metres. Strictly
        positive.
    gain_per_sec : float
        Proportional gain, 1/s. Strictly positive.

    Raises
    ------
    TypeError
        If an argument is not a number.
    ValueError
        If an argument is not finite, or a ceiling, tolerance or gain is not
        strictly positive.
    """

    def __init__(
        self,
        forward_m: float,
        left_m: float,
        *,
        max_speed_mps: float,
        tolerance_m: float,
        gain_per_sec: float = CLOSURE_GAIN_PER_SEC,
    ) -> None:
        self._target: Tuple[float, float] = (_finite("forward_m", forward_m), _finite("left_m", left_m))
        self._max_speed = _finite("max_speed_mps", max_speed_mps)
        self._tolerance = _finite("tolerance_m", tolerance_m)
        self._gain = _finite("gain_per_sec", gain_per_sec)
        for name, value in (
            ("max_speed_mps", self._max_speed),
            ("tolerance_m", self._tolerance),
            ("gain_per_sec", self._gain),
        ):
            if value <= 0.0:
                raise ValueError(f"{name} must be strictly positive, got {value!r}")
        self._travelled: Tuple[float, float] = (0.0, 0.0)
        self._last_command: Tuple[float, float] = (0.0, 0.0)
        self._done: bool = False

    @property
    def distance_m(self) -> float:
        """Planar length of the displacement asked for, metres."""
        return math.hypot(*self._target)

    @property
    def done(self) -> bool:
        """Whether the closure has completed."""
        return self._done

    @staticmethod
    def _velocity(body_velocity_mps: Optional[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
        """A usable measured ``(forward, left)`` pair, or ``None``."""
        if body_velocity_mps is None:
            return None
        try:
            forward, left = float(body_velocity_mps[0]), float(body_velocity_mps[1])
        except (TypeError, ValueError, IndexError):
            return None
        if not (math.isfinite(forward) and math.isfinite(left)):
            return None
        return forward, left

    def update(self, body_velocity_mps: Optional[Tuple[float, float]], dt: float) -> ClosureCommand:
        """Advance one control cycle.

        Parameters
        ----------
        body_velocity_mps : Optional[Tuple[float, float]]
            Measured ``(forward, left)`` velocity over the last interval, m/s.
            ``None`` integrates the previous command instead.
        dt : float
            Elapsed interval, seconds. A non-positive interval integrates
            nothing.

        Returns
        -------
        ClosureCommand
            The velocity to fly this cycle, zero once done.
        """
        if dt > 0.0 and math.isfinite(dt):
            measured = self._velocity(body_velocity_mps)
            vx, vy = measured if measured is not None else self._last_command
            self._travelled = (self._travelled[0] + vx * dt, self._travelled[1] + vy * dt)

        rem_x = self._target[0] - self._travelled[0]
        rem_y = self._target[1] - self._travelled[1]
        remaining = math.hypot(rem_x, rem_y)
        # Passing the target along the original direction ends the closure even
        # outside the tolerance: reversing onto it would be a second blind leg
        # flown on an integral that has only grown older.
        passed = rem_x * self._target[0] + rem_y * self._target[1] <= 0.0

        if self._done or remaining <= self._tolerance or passed:
            self._done = True
            self._last_command = (0.0, 0.0)
            reason = "inside tolerance" if remaining <= self._tolerance else "target passed"
            return ClosureCommand(0.0, 0.0, rem_x, rem_y, True, f"closure complete: {reason}, {remaining:.3f} m left")

        speed = min(self._max_speed, self._gain * remaining)
        command = (speed * rem_x / remaining, speed * rem_y / remaining)
        self._last_command = command
        return ClosureCommand(
            command[0],
            command[1],
            rem_x,
            rem_y,
            False,
            f"closing stand-off: {remaining:.3f} m left at {speed:.3f} m/s",
        )
