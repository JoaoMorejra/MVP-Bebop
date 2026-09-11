"""Jerk-limited velocity profiling.

Neither the Nectar SDK nor this package had any notion of a velocity profile.
``BebopDrone.move_velocity`` publishes a step command and latches it until the
next one arrives; the search and RTL steps computed a target speed from a
piecewise function of displacement and sent it directly. The command therefore
jumped discontinuously -- most visibly from zero to the anti-stall floor at the
edge of the deadband -- and the airframe answered each step with a pitch
transient that swings the camera exactly when the mission needs it steady.

This module bounds the first and second derivatives of the commanded velocity.
Limiting acceleration alone is not enough: a bounded acceleration that switches
sign instantaneously is still an impulsive torque demand. Bounding jerk is what
produces the S-shaped velocity curve and keeps the gimbal quiet.

The profiler is deliberately free of ROS, wall-clock reads, and mission types:
it takes a target and a ``dt`` and returns the next command.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional

#: Below this magnitude a residual velocity or acceleration is treated as zero,
#: which is what lets the profiler settle exactly on target instead of
#: oscillating around it at the resolution of the arithmetic.
EPSILON: Final[float] = 1e-9


@dataclass(frozen=True)
class ProfileLimits:
    """Kinematic ceilings for a single profiled axis.

    Units are whatever the axis is expressed in -- metres per second for a
    guidance law working in physical units -- as long as they are consistent.
    """

    max_velocity: float
    max_accel: float
    max_jerk: float

    def __post_init__(self) -> None:
        if self.max_velocity <= 0.0:
            raise ValueError(f"max_velocity must be positive, got {self.max_velocity!r}")
        if self.max_accel <= 0.0:
            raise ValueError(f"max_accel must be positive, got {self.max_accel!r}")
        if self.max_jerk <= 0.0:
            raise ValueError(f"max_jerk must be positive, got {self.max_jerk!r}")


class JerkLimitedProfile:
    """Second-order rate limiter tracking a velocity target.

    Maintains commanded velocity and acceleration as internal state and advances
    them toward the target subject to ``|da/dt| <= max_jerk`` and
    ``|a| <= max_accel``, with the velocity clamped to ``max_velocity``.

    The deceleration decision uses the velocity the axis would reach if it began
    ramping acceleration to zero right now -- ``v + a|a| / (2 * jerk)`` -- rather
    than the instantaneous velocity. Comparing the instantaneous value instead
    makes the profiler commit to accelerating until it is already too late to
    stop within the jerk limit, and it overshoots.
    """

    __slots__ = ("_limits", "_velocity", "_accel")

    def __init__(self, limits: ProfileLimits, *, initial_velocity: float = 0.0) -> None:
        self._limits = limits
        self._velocity: float = self._clamp_velocity(initial_velocity)
        self._accel: float = 0.0

    @property
    def velocity(self) -> float:
        """Current commanded velocity."""
        return self._velocity

    @property
    def acceleration(self) -> float:
        """Current commanded acceleration."""
        return self._accel

    @property
    def limits(self) -> ProfileLimits:
        """Kinematic ceilings in force."""
        return self._limits

    def reset(self, velocity: float = 0.0, acceleration: float = 0.0) -> None:
        """Re-seed the profiler state, typically from a measured velocity."""
        self._velocity = self._clamp_velocity(velocity)
        self._accel = max(-self._limits.max_accel, min(self._limits.max_accel, acceleration))

    def step(self, target_velocity: float, dt: float) -> float:
        """Advance one control cycle toward ``target_velocity``.

        Parameters
        ----------
        target_velocity : float
            Desired velocity. Clamped to ``max_velocity`` before tracking.
        dt : float
            Elapsed time since the previous step, in seconds. Must be positive;
            pass the value reported by :class:`~mvp_mission_bebop.engine.rate.LoopRate`
            rather than an assumed nominal period.

        Returns
        -------
        float
            The velocity to command this cycle.
        """
        if dt <= 0.0:
            return self._velocity

        limits = self._limits
        target = self._clamp_velocity(target_velocity)

        # Velocity this axis would coast to if acceleration were ramped to zero
        # starting now, at the jerk limit. This is the quantity that must be
        # compared against the target for the profile not to overshoot.
        stopping_delta = self._accel * abs(self._accel) / (2.0 * limits.max_jerk)
        projected = self._velocity + stopping_delta

        error = target - projected
        if abs(error) <= EPSILON:
            desired_accel = 0.0
        else:
            desired_accel = math.copysign(limits.max_accel, error)

        # Bound the change in acceleration by the jerk ceiling, then the
        # acceleration itself.
        max_delta = limits.max_jerk * dt
        accel = max(self._accel - max_delta, min(self._accel + max_delta, desired_accel))
        accel = max(-limits.max_accel, min(limits.max_accel, accel))

        velocity = self._velocity + accel * dt

        # Landing exactly on the target beats hunting around it: if this step
        # crosses the target, settle there and drop the acceleration.
        if (self._velocity - target) * (velocity - target) < 0.0:
            velocity = target
            accel = 0.0
        elif abs(velocity - target) <= EPSILON:
            velocity = target
            accel = 0.0

        self._velocity = self._clamp_velocity(velocity)
        self._accel = accel
        return self._velocity

    def _clamp_velocity(self, value: float) -> float:
        limit = self._limits.max_velocity
        return max(-limit, min(limit, value))


def braking_velocity(
    distance_to_go: float,
    max_decel: float,
    *,
    cruise_velocity: float,
    arrival_tolerance: float = 0.0,
) -> float:
    """Speed that can still be brought to rest within the remaining distance.

    The classic kinematic result ``v = sqrt(2 * a * d)``, saturated at the cruise
    speed. This is the feedforward half of a guidance law: it says how fast the
    vehicle is *allowed* to be given how far it has left to travel, which is a
    physically meaningful statement -- unlike a ramp defined directly on
    displacement, which has no relationship to whether the vehicle can stop.

    Parameters
    ----------
    distance_to_go : float
        Remaining distance to the target. Negative values are treated as zero.
    max_decel : float
        Deceleration the vehicle can sustain, in the same units.
    cruise_velocity : float
        Upper saturation.
    arrival_tolerance : float
        Distance inside which the target counts as reached, subtracted from the
        remaining distance so the profile aims at the edge of the arrival window
        rather than at a mathematical point it can never hit.

    Returns
    -------
    float
        Non-negative allowable speed magnitude.
    """
    if max_decel <= 0.0:
        raise ValueError(f"max_decel must be positive, got {max_decel!r}")

    effective = max(0.0, distance_to_go - arrival_tolerance)
    if effective <= 0.0:
        return 0.0
    return min(abs(cruise_velocity), math.sqrt(2.0 * max_decel * effective))
