"""Slew-rate plausibility gating for the fused altitude estimate.

The Bebop holds height with a downward ultrasonic rangefinder. Flying low over
an obstacle -- a bench, a parked vehicle, a person -- shortens the measured
range, the firmware reads that as lost height, and climbs to correct it. No
raw range is exposed on this driver to filter directly: ``bebop_driver_node.cpp``
integrates the fused vertical speed estimate into ``/bebop/odom.z``, and the
one other altitude topic the driver exposes (``/bebop/states/altitude``) is
plausibly the same class of fused estimate, not an independent one. The only
lever available from this package is bounding how fast the trusted reading is
allowed to move.

A true climb or sink is bounded by the airframe's own dynamics. An
obstacle-induced range-shortening event is a near-instantaneous jump nothing
in the airframe's real trajectory can produce. This filter holds the last
trusted altitude across any sample that violates that physical envelope, and
requires a run of consecutive outliers -- not a single one -- before accepting
a new regime, so a genuine step change (the drone actually landing) is not
rejected forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PlausibilityLimits:
    """Physical envelope a genuine altitude change must respect."""

    #: Ceiling on the airframe's real vertical speed, in metres per second.
    max_speed_mps: float
    #: Ceiling on the airframe's real vertical acceleration, in metres per
    #: second squared. Sized from the same bound the altitude governor already
    #: profiles its own commands against
    #: (``AltitudeGovernorConfig.max_accel_mps2``), since a controller that
    #: cannot command more than this cannot have produced more than this
    #: either.
    max_accel_mps2: float
    #: Consecutive out-of-envelope samples required before the envelope is
    #: abandoned in favour of the new reading, rather than held forever.
    reject_streak: int = 3

    def __post_init__(self) -> None:
        if self.max_speed_mps <= 0.0:
            raise ValueError(f"max_speed_mps must be positive, got {self.max_speed_mps!r}")
        if self.max_accel_mps2 <= 0.0:
            raise ValueError(f"max_accel_mps2 must be positive, got {self.max_accel_mps2!r}")
        if self.reject_streak < 1:
            raise ValueError(f"reject_streak must be at least 1, got {self.reject_streak!r}")


class AltitudePlausibilityFilter:
    """Holds the last trusted altitude across a sample the airframe could not
    have produced.

    Not a Kalman filter: there is no documented noise model for the Bebop's
    fused altitude estimate to fit one against, and the failure this exists to
    reject is a step discontinuity, not zero-mean noise. A bounded slew-rate
    gate with accept/reject hysteresis is the simplest model that rejects the
    failure without needing a tuned covariance.
    """

    __slots__ = ("_limits", "_last_trusted", "_reject_run")

    def __init__(self, limits: PlausibilityLimits, initial_altitude: float = 0.0) -> None:
        self._limits = limits
        self._last_trusted = initial_altitude
        self._reject_run = 0

    @property
    def last_trusted(self) -> float:
        """Most recently accepted altitude, in metres."""
        return self._last_trusted

    def reset(self, altitude: float = 0.0) -> None:
        """Re-anchor the filter, discarding any pending rejection streak."""
        self._last_trusted = altitude
        self._reject_run = 0

    def update(self, measured_altitude: float, dt: float) -> float:
        """Filter one altitude sample against the physical envelope.

        Parameters
        ----------
        measured_altitude : float
            The raw fused reading for this cycle, in metres.
        dt : float
            Elapsed interval since the previous sample, in seconds. A
            non-positive value is treated as an absent sample: the last
            trusted altitude is returned unchanged, since no meaningful
            envelope can be computed against it.

        Returns
        -------
        float
            ``measured_altitude`` when it falls inside the envelope reachable
            from the last trusted value in ``dt`` seconds, otherwise the last
            trusted value.
        """
        if not math.isfinite(measured_altitude) or dt <= 0.0:
            return self._last_trusted

        limits = self._limits
        max_excursion = limits.max_speed_mps * dt + 0.5 * limits.max_accel_mps2 * dt * dt
        excursion = measured_altitude - self._last_trusted

        if abs(excursion) <= max_excursion:
            self._reject_run = 0
            self._last_trusted = measured_altitude
            return self._last_trusted

        self._reject_run += 1
        if self._reject_run >= limits.reject_streak:
            # A run this long is no longer noise: accept the new regime rather
            # than latch onto a stale reading indefinitely.
            self._reject_run = 0
            self._last_trusted = measured_altitude
            return self._last_trusted

        return self._last_trusted
