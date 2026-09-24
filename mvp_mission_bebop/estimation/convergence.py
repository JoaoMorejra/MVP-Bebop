"""Multivariate convergence detection over a sliding observation window.

Neither the SDK nor this package had any statistical notion of arrival. The SDK
tests a single sample against a threshold (``navigator.py:138``); the old RTL
counted N consecutive loop iterations inside a radius. Both accept a vehicle
that is merely *passing through* the target region, and the iteration counter
has a sharper flaw: it incremented regardless of what was being commanded, so a
drone circling inside the arrival radius under active lateral command satisfied
it just as well as a stationary one.

Settlement here requires four conditions to hold simultaneously across a time
window: enough samples, a long enough span, every sample inside the arrival
region, low positional variance, and low mean speed. Variance is what
distinguishes orbiting from stopping -- a circling vehicle keeps its distance
within tolerance while its position variance stays high.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional, Tuple

Clock = Callable[[], float]


@dataclass(frozen=True)
class SettlementCriteria:
    """Thresholds defining what counts as settled."""

    #: Duration the conditions must hold continuously.
    window_sec: float
    #: Minimum observations in the window. Guards against a fast loop declaring
    #: settlement from two samples that happen to straddle the required span.
    min_samples: int
    #: Ceiling on the mean speed magnitude across the window.
    max_speed: float
    #: Ceiling on the positional standard deviation across the window.
    max_position_sigma: float
    #: Optional arrival radius every sample must fall within.
    max_distance: Optional[float] = None
    #: Optional ceiling on the mean absolute *commanded* vertical speed across
    #: the window, in normalized units (the same units
    #: ``AltitudeHoldGovernor.compute_vz`` returns). ``None`` disables the
    #: check, reproducing the detector's behaviour before this field existed.
    #:
    #: This is deliberately the commanded value, not a measured vertical
    #: speed: the firmware's own autonomous hover mode (``do_hover``) engages
    #: only when the commanded ``gaz_speed`` is within 0.001 of zero
    #: (``bebop.cpp::Bebop::move``), so what determines whether the airframe
    #: can actually freeze is what is being asked of it, not what the noisy
    #: altitude estimate says happened.
    max_vertical_speed: Optional[float] = None

    def __post_init__(self) -> None:
        if self.window_sec <= 0.0:
            raise ValueError(f"window_sec must be positive, got {self.window_sec!r}")
        if self.min_samples < 2:
            raise ValueError(f"min_samples must be at least 2, got {self.min_samples!r}")


@dataclass(frozen=True)
class SettlementReport:
    """Diagnostic view of why settlement is or is not satisfied."""

    settled: bool
    reason: str
    samples: int
    span_sec: float
    mean_speed: float
    position_sigma: float
    max_distance: float
    mean_vertical_speed: float

    def __str__(self) -> str:
        return (
            f"{'SETTLED' if self.settled else 'unsettled'} ({self.reason}): "
            f"n={self.samples} span={self.span_sec:.2f}s "
            f"v_mean={self.mean_speed:.3f} sigma_pos={self.position_sigma:.3f} "
            f"d_max={self.max_distance:.3f} vz_mean={self.mean_vertical_speed:.3f}"
        )


class SettlementDetector:
    """Sliding-window detector for multivariate kinematic convergence."""

    __slots__ = ("_criteria", "_clock", "_samples")

    def __init__(self, criteria: SettlementCriteria, *, clock: Clock = time.monotonic) -> None:
        self._criteria = criteria
        self._clock = clock
        # (timestamp, x, y, speed, distance, vz)
        self._samples: Deque[Tuple[float, float, float, float, float, float]] = deque()

    @property
    def criteria(self) -> SettlementCriteria:
        """Thresholds in force."""
        return self._criteria

    @property
    def sample_count(self) -> int:
        """Observations currently inside the window."""
        return len(self._samples)

    def reset(self) -> None:
        """Discard the window. Call when the target or phase changes."""
        self._samples.clear()

    def update(
        self,
        *,
        x: float,
        y: float,
        speed: float,
        distance: float = 0.0,
        vz: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> SettlementReport:
        """Add an observation and re-evaluate the settlement conditions.

        Parameters
        ----------
        x, y : float
            Position, in metres.
        speed : float
            Speed magnitude, in metres per second.
        distance : float
            Distance to the target, in metres. Compared against
            ``criteria.max_distance`` when that is configured.
        vz : float
            Commanded vertical speed for this observation, in normalized
            units. Compared against ``criteria.max_vertical_speed`` when that
            is configured.
        timestamp : Optional[float]
            Observation time. Defaults to the injected clock, which tests
            override to drive the window deterministically.

        Returns
        -------
        SettlementReport
            Whether settlement holds, plus the statistics behind the verdict.
        """
        now = self._clock() if timestamp is None else timestamp
        self._samples.append((now, x, y, speed, distance, vz))

        # Retain exactly one sample at or before the horizon as the window's
        # left edge. Dropping everything older leaves the span strictly shorter
        # than the window, so the duration condition could never be met.
        horizon = now - self._criteria.window_sec
        while len(self._samples) > 1 and self._samples[1][0] <= horizon:
            self._samples.popleft()

        return self._evaluate(now)

    def _evaluate(self, now: float) -> SettlementReport:
        criteria = self._criteria
        count = len(self._samples)

        if count < criteria.min_samples:
            return SettlementReport(
                settled=False,
                reason=f"only {count}/{criteria.min_samples} samples",
                samples=count,
                span_sec=0.0,
                mean_speed=0.0,
                position_sigma=0.0,
                max_distance=0.0,
                mean_vertical_speed=0.0,
            )

        span = now - self._samples[0][0]
        xs = [s[1] for s in self._samples]
        ys = [s[2] for s in self._samples]
        speeds = [s[3] for s in self._samples]
        distances = [s[4] for s in self._samples]
        vertical_speeds = [s[5] for s in self._samples]

        mean_speed = statistics.fmean(speeds)
        # Isotropic positional spread: the radius of the scatter, not a
        # per-axis figure, so a vehicle drifting diagonally is judged the same
        # as one drifting along an axis.
        sigma = math.sqrt(statistics.pvariance(xs) + statistics.pvariance(ys))
        furthest = max(distances)
        mean_vz = statistics.fmean(abs(v) for v in vertical_speeds)

        reason = "converged"
        settled = True

        if span < criteria.window_sec:
            settled = False
            reason = f"span {span:.2f}s < {criteria.window_sec:.2f}s"
        elif criteria.max_distance is not None and furthest > criteria.max_distance:
            settled = False
            reason = f"excursion {furthest:.3f}m > {criteria.max_distance:.3f}m"
        elif sigma > criteria.max_position_sigma:
            settled = False
            reason = f"position sigma {sigma:.3f}m > {criteria.max_position_sigma:.3f}m"
        elif mean_speed > criteria.max_speed:
            settled = False
            reason = f"mean speed {mean_speed:.3f} > {criteria.max_speed:.3f} m/s"
        elif criteria.max_vertical_speed is not None and mean_vz > criteria.max_vertical_speed:
            settled = False
            reason = f"vertical speed {mean_vz:.3f} > {criteria.max_vertical_speed:.3f}"

        return SettlementReport(
            settled=settled,
            reason=reason,
            samples=count,
            span_sec=span,
            mean_speed=mean_speed,
            position_sigma=sigma,
            max_distance=furthest,
            mean_vertical_speed=mean_vz,
        )
