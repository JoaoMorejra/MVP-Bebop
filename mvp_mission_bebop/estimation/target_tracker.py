"""Alpha-beta tracking and short-horizon extrapolation of a visual target.

When the detector drops a frame the old tracking step held its last velocity
command blind for up to four seconds and hoped the target came back. There was
no estimate of where the target had gone, so re-acquisition depended entirely on
the target drifting back into view on its own.

This filter maintains position and velocity in image space and can extrapolate
through a dropout. An alpha-beta filter is used rather than a full Kalman
filter on purpose: a Kalman gain is only better than a fixed one when the
process and measurement noise covariances are actually known, and for a YOLO
bounding-box centroid on a vibrating airframe they are not. A fixed-gain filter
with the same structure is auditable, has no covariance to diverge, and is
testable without pulling in a linear algebra dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Optional, Tuple

#: Extrapolating a constant-velocity model much beyond this is fiction; the
#: caller should give up and search instead.
DEFAULT_MAX_COAST_SEC: Final[float] = 1.5


@dataclass(frozen=True)
class TrackerGains:
    """Fixed alpha-beta gains.

    ``alpha`` weights the position correction, ``beta`` the velocity
    correction. Higher values follow the measurement more closely at the cost of
    passing more detector jitter through; lower values smooth harder and lag.
    The critically-damped relationship ``beta = alpha^2 / (2 - alpha)`` is a
    reasonable starting point when tuning.
    """

    alpha: float = 0.60
    beta: float = 0.20

    def __post_init__(self) -> None:
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError(f"alpha must lie in (0, 1], got {self.alpha!r}")
        if not 0.0 <= self.beta <= 1.0:
            raise ValueError(f"beta must lie in [0, 1], got {self.beta!r}")


@dataclass(frozen=True)
class TrackEstimate:
    """Filtered target state in image coordinates."""

    x: float
    y: float
    vx: float
    vy: float
    #: Seconds of extrapolation since the last real measurement.
    coast_sec: float
    #: False once the coast horizon has been exceeded.
    trustworthy: bool

    @property
    def center(self) -> Tuple[float, float]:
        """Estimated target centre, in pixels."""
        return self.x, self.y

    @property
    def speed(self) -> float:
        """Estimated image-space speed, in pixels per second."""
        return math.hypot(self.vx, self.vy)


class ConstantVelocityTracker:
    """Alpha-beta filter over a target centroid in image space."""

    __slots__ = ("_gains", "_max_coast", "_x", "_y", "_vx", "_vy", "_initialized", "_coast")

    def __init__(
        self,
        gains: Optional[TrackerGains] = None,
        *,
        max_coast_sec: float = DEFAULT_MAX_COAST_SEC,
    ) -> None:
        if max_coast_sec <= 0.0:
            raise ValueError(f"max_coast_sec must be positive, got {max_coast_sec!r}")
        self._gains = gains or TrackerGains()
        self._max_coast = max_coast_sec
        self._x: float = 0.0
        self._y: float = 0.0
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._initialized: bool = False
        self._coast: float = 0.0

    @property
    def initialized(self) -> bool:
        """True once at least one measurement has been absorbed."""
        return self._initialized

    @property
    def coast_sec(self) -> float:
        """Seconds elapsed since the last real measurement."""
        return self._coast

    def reset(self) -> None:
        """Forget the track entirely."""
        self._initialized = False
        self._x = self._y = self._vx = self._vy = 0.0
        self._coast = 0.0

    def update(self, measurement: Tuple[float, float], dt: float) -> TrackEstimate:
        """Absorb a detection and return the corrected estimate."""
        mx, my = float(measurement[0]), float(measurement[1])

        if not self._initialized or dt <= 0.0:
            self._x, self._y = mx, my
            if not self._initialized:
                self._vx = self._vy = 0.0
            self._initialized = True
            self._coast = 0.0
            return self._estimate()

        # Predict, then correct by the residual.
        px = self._x + self._vx * dt
        py = self._y + self._vy * dt
        rx = mx - px
        ry = my - py

        alpha, beta = self._gains.alpha, self._gains.beta
        self._x = px + alpha * rx
        self._y = py + alpha * ry
        self._vx += beta * rx / dt
        self._vy += beta * ry / dt
        self._coast = 0.0
        return self._estimate()

    def coast(self, dt: float) -> Optional[TrackEstimate]:
        """Extrapolate through a dropout without a measurement.

        Returns
        -------
        Optional[TrackEstimate]
            The extrapolated state, or ``None`` if the track was never
            initialized. The estimate's ``trustworthy`` flag goes false once the
            coast horizon is passed; the caller decides what to do about it.
        """
        if not self._initialized or dt <= 0.0:
            return None

        self._x += self._vx * dt
        self._y += self._vy * dt
        self._coast += dt
        return self._estimate()

    def _estimate(self) -> TrackEstimate:
        return TrackEstimate(
            x=self._x,
            y=self._y,
            vx=self._vx,
            vy=self._vy,
            coast_sec=self._coast,
            trustworthy=self._coast <= self._max_coast,
        )
