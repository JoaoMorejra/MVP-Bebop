"""Deterministic loop pacing and elapsed-time primitives.

Every mission loop in this package used to advance on an ad-hoc ``time.sleep``
whose constant was dwarfed by the blocking cost of ``take_photo`` plus YOLO
inference. Controllers nonetheless assumed a fixed cadence, so their derivative
and ramp terms were computed against a period that never held.

``LoopRate`` fixes both halves of that problem: it paces the loop toward a target
period *and* reports the interval that actually elapsed, clamped into a sane
band so a stalled frame grab cannot inject a multi-second ``dt`` into a PID.

The clock and sleep function are injectable so controllers and steps can be
exercised deterministically in tests without real wall-clock delays.
"""

from __future__ import annotations

import time
from typing import Callable, Final

#: Lower clamp for a reported interval. Guards against division by zero in
#: derivative terms when two ticks land inside the clock's resolution.
MIN_INTERVAL_SEC: Final[float] = 1e-4

#: Upper clamp for a reported interval. A blocking frame grab or a descheduled
#: process must not hand a controller a ``dt`` large enough to produce an
#: integral jump or a derivative of effectively zero.
MAX_INTERVAL_SEC: Final[float] = 0.5

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class LoopRate:
    """Fixed-period loop pacer reporting the true elapsed interval.

    Parameters
    ----------
    frequency_hz : float
        Target loop frequency. Must be strictly positive.
    clock : Callable[[], float]
        Monotonic time source, in seconds. Injectable for tests.
    sleeper : Callable[[float], None]
        Blocking sleep function. Injectable for tests.
    max_interval_sec : float
        Upper clamp applied to the reported interval.

    Notes
    -----
    ``tick`` never sleeps a negative amount: when the loop body already overran
    the target period it returns immediately and reports the real (clamped)
    overrun, so a slow loop degrades in cadence rather than in correctness.
    """

    __slots__ = ("_period", "_clock", "_sleeper", "_max_interval", "_last_tick", "_overruns", "_ticks")

    def __init__(
        self,
        frequency_hz: float,
        *,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        max_interval_sec: float = MAX_INTERVAL_SEC,
    ) -> None:
        if frequency_hz <= 0.0:
            raise ValueError(f"frequency_hz must be positive, got {frequency_hz!r}")
        if max_interval_sec < MIN_INTERVAL_SEC:
            raise ValueError(f"max_interval_sec must be >= {MIN_INTERVAL_SEC}, got {max_interval_sec!r}")

        self._period: float = 1.0 / frequency_hz
        self._clock: Clock = clock
        self._sleeper: Sleeper = sleeper
        self._max_interval: float = max_interval_sec
        self._last_tick: float = clock()
        self._overruns: int = 0
        self._ticks: int = 0

    @property
    def period_sec(self) -> float:
        """Target loop period in seconds."""
        return self._period

    @property
    def frequency_hz(self) -> float:
        """Target loop frequency in hertz."""
        return 1.0 / self._period

    @property
    def overruns(self) -> int:
        """Number of ticks whose body exceeded the target period."""
        return self._overruns

    @property
    def ticks(self) -> int:
        """Number of completed ticks."""
        return self._ticks

    def reset(self) -> None:
        """Re-anchor the pacer to now, discarding any accumulated lateness."""
        self._last_tick = self._clock()

    def tick(self) -> float:
        """Sleep the remainder of the period and return the elapsed interval.

        Returns
        -------
        float
            Seconds elapsed since the previous tick, clamped into
            ``[MIN_INTERVAL_SEC, max_interval_sec]``. This is the value to feed
            to any ``dt``-dependent control law.
        """
        now = self._clock()
        elapsed = now - self._last_tick

        remaining = self._period - elapsed
        if remaining > 0.0:
            self._sleeper(remaining)
            now = self._clock()
            elapsed = now - self._last_tick
        else:
            self._overruns += 1

        self._last_tick = now
        self._ticks += 1
        return self.clamp_interval(elapsed)

    @staticmethod
    def clamp_interval(interval_sec: float, max_interval_sec: float = MAX_INTERVAL_SEC) -> float:
        """Clamp a raw interval into the band safe for derivative computation."""
        if interval_sec < MIN_INTERVAL_SEC:
            return MIN_INTERVAL_SEC
        if interval_sec > max_interval_sec:
            return max_interval_sec
        return interval_sec


class Deadline:
    """Monotonic countdown for bounding a mission phase.

    Replaces the ``while time.time() - start < timeout`` idiom, which used the
    wall clock (subject to NTP steps) and recomputed the origin inline at every
    call site.
    """

    __slots__ = ("_duration", "_clock", "_start")

    def __init__(self, duration_sec: float, *, clock: Clock = time.monotonic) -> None:
        if duration_sec < 0.0:
            raise ValueError(f"duration_sec must be non-negative, got {duration_sec!r}")
        self._duration: float = duration_sec
        self._clock: Clock = clock
        self._start: float = clock()

    @property
    def duration_sec(self) -> float:
        """Total configured duration in seconds."""
        return self._duration

    @property
    def elapsed_sec(self) -> float:
        """Seconds since the deadline was created or last reset."""
        return self._clock() - self._start

    @property
    def remaining_sec(self) -> float:
        """Seconds left before expiry, floored at zero."""
        return max(0.0, self._duration - self.elapsed_sec)

    @property
    def expired(self) -> bool:
        """True once the configured duration has elapsed."""
        return self.elapsed_sec >= self._duration

    @property
    def active(self) -> bool:
        """True while the deadline has not yet expired."""
        return not self.expired

    def reset(self) -> None:
        """Restart the countdown from now."""
        self._start = self._clock()
