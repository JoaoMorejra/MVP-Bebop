"""Inter-command cadence measured at the proxy.

The Bebop latches the last ``Twist`` it received, so the effective control
period of any loop is the interval between two commands leaving
``BenchtopDroneProxy.move_velocity``, not the period the loop asked its
``LoopRate`` for. A frame wait or an inline marker detection that stretches the
cycle shows up here and nowhere else for the loops that do not report their own
cadence (Stage 1 ascent, Stage 5 centring and touchdown).

The emitted line is a stable format parsed by ``scripts/analyze_flight_logs.py``::

    [CMD t=<mono> vx=<f> vy=<f> vz=<f> vyaw=<f> dt_ms=<f> p95_ms=<f> max_ms=<f>]
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Callable, Deque, Final, Optional, Tuple

#: Default size of the circular interval window: about 17 s at 15 Hz, long
#: enough to cover a whole centring phase and short enough to forget a stage.
DEFAULT_WINDOW: Final[int] = 256


class CommandCadenceMonitor:
    """Circular record of the intervals between transmitted commands.

    Parameters
    ----------
    clock : Callable[[], float]
        Monotonic time source in seconds. Injectable for tests.
    window : int
        Number of intervals kept. Must be at least 2.

    Raises
    ------
    ValueError
        If ``window`` is smaller than 2.

    Notes
    -----
    Percentiles are computed by sorting the window on demand: at 256 samples
    and a 1 Hz report this is negligible and keeps the module free of numpy.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, window: int = DEFAULT_WINDOW) -> None:
        if isinstance(window, bool) or not isinstance(window, int):
            raise TypeError(f"window must be an int, got {type(window).__name__}")
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window!r}")
        self._clock: Callable[[], float] = clock
        self._intervals: Deque[float] = deque(maxlen=window)
        self._last_t: Optional[float] = None
        self._last_command: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._last_dt_ms: float = 0.0
        self._last_log_t: Optional[float] = None
        self.nonfinite: int = 0
        self.count: int = 0

    def note(self, vx: float, vy: float, vz: float, vyaw: float) -> None:
        """Record one transmitted command.

        Parameters
        ----------
        vx, vy, vz, vyaw : float
            Normalized components as sent to the driver. A non-finite component
            is counted in :attr:`nonfinite` and never raises.
        """
        now = self._clock()
        if self._last_t is not None:
            self._last_dt_ms = (now - self._last_t) * 1000.0
            self._intervals.append(self._last_dt_ms)
        self._last_t = now
        self.count += 1
        components = (vx, vy, vz, vyaw)
        try:
            finite = all(math.isfinite(float(c)) for c in components)
        except (TypeError, ValueError):
            finite = False
        if not finite:
            self.nonfinite += 1
            return
        self._last_command = (float(vx), float(vy), float(vz), float(vyaw))

    def intervals_ms(self) -> Tuple[float, ...]:
        """Intervals currently in the window, in milliseconds, oldest first."""
        return tuple(self._intervals)

    def _percentile(self, fraction: float) -> float:
        if not self._intervals:
            return 0.0
        ordered = sorted(self._intervals)
        index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
        return ordered[index]

    def p95_ms(self) -> float:
        """95th percentile of the windowed intervals; ``0.0`` when empty."""
        return self._percentile(0.95)

    def max_ms(self) -> float:
        """Largest windowed interval; ``0.0`` when empty."""
        return max(self._intervals) if self._intervals else 0.0

    def report(self) -> str:
        """The ``[CMD ...]`` line for the most recent command."""
        vx, vy, vz, vyaw = self._last_command
        t = self._last_t if self._last_t is not None else self._clock()
        line = (
            f"[CMD t={t:.3f} vx={vx:.3f} vy={vy:.3f} vz={vz:.3f} vyaw={vyaw:.3f} "
            f"dt_ms={self._last_dt_ms:.1f} p95_ms={self.p95_ms():.1f} max_ms={self.max_ms():.1f}"
        )
        if self.nonfinite:
            line += f" nonfinite={self.nonfinite}"
        return line + "]"

    def maybe_log(self, target: logging.Logger, period_sec: float = 1.0) -> None:
        """Log :meth:`report` at most once per ``period_sec``.

        Parameters
        ----------
        target : logging.Logger
            Logger receiving the line at INFO.
        period_sec : float
            Minimum spacing between two lines, in monotonic seconds.
        """
        now = self._clock()
        if self._last_log_t is not None and now - self._last_log_t < period_sec:
            return
        self._last_log_t = now
        target.info("%s", self.report())
