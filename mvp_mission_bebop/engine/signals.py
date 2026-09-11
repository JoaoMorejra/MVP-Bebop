"""Reentrancy-safe operator-interrupt handling with a bounded time budget.

The previous implementation had three defects that this module exists to fix:

1. The re-entrancy guard was tested *after* roughly five seconds of blocking
   work, so a second Ctrl-C re-ran the whole landing burst and then fell through
   without exiting -- insisting made the process hang longer, not quit faster.
2. It spent ~5 s inside the handler (0.3 s burst + 1.2 s flush + 3.5 s announcer
   wait) while the Electron GCS only waits 800 ms after sending SIGINT before it
   moves on (``electron/main.cjs:759``). The tail of that sequence was never
   observed by anyone.
3. ``sys.exit(0)`` raised ``SystemExit`` -- a ``BaseException`` -- at an
   arbitrary bytecode boundary, so it escaped the pipeline's ``except Exception``
   and unwound through two more ``finally`` blocks, running cleanup three times.

The handler here checks its guard first, bounds the whole sequence by an
explicit budget that fits inside the GCS window, and runs finalization exactly
once regardless of how it is reached.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from types import FrameType
from typing import Callable, Final, Optional

logger = logging.getLogger("EmergencyHandler")

#: Total wall-clock budget for the emergency sequence. Must stay below the
#: 800 ms the Electron GCS waits after sending SIGINT, or the operator sees the
#: process killed mid-landing instead of landing cleanly.
DEFAULT_BUDGET_SEC: Final[float] = 0.70

#: Exit status used when the operator insists with a second interrupt.
FORCED_EXIT_CODE: Final[int] = 130

Clock = Callable[[], float]

#: Called with the seconds remaining in the budget; must transmit the landing
#: commands and return within that window.
LandSequence = Callable[[float], None]

#: Called once, after the landing sequence, to release resources.
Finalizer = Callable[[], None]


class EmergencyHandler:
    """Installs SIGINT/SIGTERM handlers driving one bounded controlled landing.

    Parameters
    ----------
    emergency_event : threading.Event
        Set synchronously on the first signal so mission loops observe the abort
        at their next iteration boundary. This is the only cross-thread-safe
        part of the sequence and therefore happens first.
    land_sequence : Callable[[float], None]
        Transmits the controlled landing. Receives the seconds left in the
        budget and is expected to respect it.
    finalizer : Callable[[], None]
        Releases resources. Guaranteed to run at most once for the lifetime of
        this handler, whether reached via signal or via :meth:`finalize_once`.
    budget_sec : float
        Wall-clock ceiling for the whole sequence.
    exit_code : int
        Status passed to ``sys.exit`` on the normal path.

    Notes
    -----
    Never cuts motors. The landing sequence is a controlled descent command; the
    process only leaves once that has been transmitted or the budget is spent.
    """

    __slots__ = (
        "_event",
        "_land_sequence",
        "_finalizer",
        "_budget",
        "_exit_code",
        "_clock",
        "_lock",
        "_triggered",
        "_finalized",
        "_installed",
        "_previous",
    )

    def __init__(
        self,
        emergency_event: threading.Event,
        land_sequence: LandSequence,
        finalizer: Finalizer,
        *,
        budget_sec: float = DEFAULT_BUDGET_SEC,
        exit_code: int = 0,
        clock: Clock = time.monotonic,
    ) -> None:
        if budget_sec <= 0.0:
            raise ValueError(f"budget_sec must be positive, got {budget_sec!r}")

        self._event: threading.Event = emergency_event
        self._land_sequence: LandSequence = land_sequence
        self._finalizer: Finalizer = finalizer
        self._budget: float = budget_sec
        self._exit_code: int = exit_code
        self._clock: Clock = clock

        self._lock: threading.Lock = threading.Lock()
        self._triggered: bool = False
        self._finalized: bool = False
        self._installed: bool = False
        self._previous: dict[int, object] = {}

    # ------------------------------------------------------------------ state

    @property
    def triggered(self) -> bool:
        """True once an operator interrupt has been observed."""
        return self._triggered

    @property
    def finalized(self) -> bool:
        """True once the finalizer has run."""
        return self._finalized

    @property
    def installed(self) -> bool:
        """True while this handler owns the signal dispositions."""
        return self._installed

    # -------------------------------------------------------------- lifecycle

    def install(self) -> None:
        """Register SIGINT and SIGTERM handlers.

        Registration is explicit rather than a constructor side effect, so the
        owning object can be built off the main thread (in a test, say) without
        tripping ``ValueError: signal only works in main thread``.

        Raises
        ------
        RuntimeError
            If called from a thread other than the main thread.
        """
        if self._installed:
            return
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("EmergencyHandler.install() must be called from the main thread")

        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._on_signal)
        self._installed = True
        logger.debug("Operator interrupt handlers installed (budget=%.2f s).", self._budget)

    def uninstall(self) -> None:
        """Restore the previous signal dispositions."""
        if not self._installed:
            return
        for signum, previous in self._previous.items():
            try:
                signal.signal(signum, previous)  # type: ignore[arg-type]
            except (ValueError, TypeError, OSError):
                pass
        self._previous.clear()
        self._installed = False

    def finalize_once(self) -> None:
        """Run the finalizer if it has not run yet.

        The normal (non-interrupted) shutdown path calls this, which is what
        makes the double cleanup impossible: whichever path gets there first
        wins and the other becomes a no-op.
        """
        with self._lock:
            if self._finalized:
                return
            self._finalized = True

        try:
            self._finalizer()
        except Exception as exc:  # noqa: BLE001 - finalization must not raise
            logger.error("Exception during finalization: %s", exc, exc_info=True)

    # ----------------------------------------------------------------- signal

    def _on_signal(self, signum: int, frame: Optional[FrameType] = None) -> None:
        """Handle an operator interrupt. Guard first, work second."""
        with self._lock:
            first = not self._triggered
            self._triggered = True

        if not first:
            # The operator insisted. Honour it immediately rather than replaying
            # a landing burst that is already in flight.
            logger.critical("Second interrupt received. Terminating immediately.")
            os._exit(FORCED_EXIT_CODE)

        self._event.set()
        started = self._clock()

        logger.critical("=" * 65)
        logger.critical("OPERATOR EMERGENCY SIGNAL (%s). TRANSMITTING CONTROLLED LANDING...", signum)
        logger.critical("=" * 65)

        try:
            remaining = max(0.0, self._budget - (self._clock() - started))
            self._land_sequence(remaining)
        except Exception as exc:  # noqa: BLE001 - never abort the exit path
            logger.error("Exception during emergency landing dispatch: %s", exc, exc_info=True)

        self.finalize_once()

        spent = self._clock() - started
        logger.info("Emergency sequence finalized in %.2f s. Exiting.", spent)
        raise SystemExit(self._exit_code)
