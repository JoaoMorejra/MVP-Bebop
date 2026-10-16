"""Authority to command yaw, granted by window and closed by default.

The mission holds ``vyaw == 0.0`` as a hard invariant: the Bebop derives its
horizontal odometry from optical flow and a yaw rate corrupts it. The invariant
is enforced at two independent points, the actuator proxy and the failsafe
supervisor. The Simple mission profile is the one flight that must rotate, and
it must do so without weakening either point for any other stage.

A window is the same device as ``FailsafeSupervisor.altitude_hold_window``:
authority exists only inside a ``with`` block, nests, restores the previous
ceiling on exit and cannot outlive an exception. The proxy and the supervisor
are given the *same* instance by ``mission.py``; if they are ever given two
different ones, the window opened on one is closed on the other and the
airframe receives zero, which is the safe direction.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from typing import Iterator


class YawAuthority:
    """Bounded, scoped permission to command a yaw rate."""

    def __init__(self) -> None:
        self._ceiling: float = 0.0
        self._lock = threading.Lock()

    @property
    def ceiling(self) -> float:
        """Largest yaw magnitude currently authorized, normalized. Zero when closed."""
        with self._lock:
            return self._ceiling

    @property
    def is_open(self) -> bool:
        """Whether any yaw is currently authorized."""
        return self.ceiling > 0.0

    @contextmanager
    def window(self, max_vyaw: float) -> Iterator[None]:
        """Authorize yaw up to ``max_vyaw`` for the duration of the block.

        Parameters
        ----------
        max_vyaw : float
            Ceiling on the yaw magnitude, normalized. Zero authorizes nothing.

        Raises
        ------
        ValueError
            If ``max_vyaw`` is negative or not finite.
        """
        if not math.isfinite(max_vyaw) or max_vyaw < 0.0:
            raise ValueError(f"max_vyaw must be a finite non-negative number, got {max_vyaw!r}")
        with self._lock:
            previous = self._ceiling
            self._ceiling = float(max_vyaw)
        try:
            yield
        finally:
            with self._lock:
                self._ceiling = previous

    def clamp(self, vyaw: float) -> float:
        """Saturate a yaw command onto the authorized ceiling.

        A non-finite value is rejected to zero rather than saturated: ``NaN``
        compares False against every bound and would otherwise resolve to
        whichever bound ``min``/``max`` happens to evaluate first.

        Parameters
        ----------
        vyaw : float
            Requested yaw rate, normalized.

        Returns
        -------
        float
            ``0.0`` when the authority is closed or ``vyaw`` is not finite,
            otherwise ``vyaw`` saturated to ``[-ceiling, ceiling]``.
        """
        ceiling = self.ceiling
        if ceiling <= 0.0 or not math.isfinite(vyaw):
            return 0.0
        return max(-ceiling, min(ceiling, float(vyaw)))
