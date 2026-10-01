"""Start-up phase timing for ``mission.py`` (7.5).

Every phase between the process start and Stage 1 logs one line,
``[TIMING] phase=<name> ms=<n>``, read by ``scripts/bench_startup.py``. The
baseline it was introduced against: 11.8 s from spawn to ``[STEP 1``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Final, Iterator, Optional

logger = logging.getLogger("StartupTiming")

_PHASE_NAME: Final[re.Pattern] = re.compile(r"^[a-z][a-z0-9_]*$")


def process_age_ms() -> Optional[float]:
    """Milliseconds since this process was created, from ``/proc``; ``None`` elsewhere.

    Covers what no in-process clock can: the interpreter's own start-up and
    the imports that run before the first line of ``mission.py``.
    """
    try:
        with open("/proc/self/stat", "r", encoding="ascii") as handle:
            # Field 22 (starttime, clock ticks since boot) follows the command
            # name, which may contain spaces; split after its closing paren.
            fields = handle.read().rsplit(")", 1)[1].split()
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            uptime = float(handle.read().split()[0])
        started = int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None
    return max(0.0, (uptime - started) * 1000.0)


class StartupTimer:
    """Logs the duration of each named start-up phase.

    :meth:`phase` times a block; :meth:`lap` logs the time since the previous
    lap (or since the timer was created), for a sequence of steps that do not
    nest.
    """

    def __init__(self) -> None:
        self._last_lap = time.monotonic()

    def lap(self, name: str) -> None:
        """Log the time since the previous lap as phase ``name``."""
        now = time.monotonic()
        self.record(name, (now - self._last_lap) * 1000.0)
        self._last_lap = now

    @staticmethod
    def _validate(name: str) -> None:
        if not isinstance(name, str) or not _PHASE_NAME.match(name):
            raise ValueError(f"phase name must be a lowercase identifier, got {name!r}")

    def record(self, name: str, ms: float) -> None:
        """Log a duration measured elsewhere.

        Raises
        ------
        ValueError
            If ``name`` is not a lowercase identifier.
        """
        self._validate(name)
        logger.info("[TIMING] phase=%s ms=%d", name, int(round(ms)))

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time the enclosed block; logged even when it raises."""
        self._validate(name)
        started = time.monotonic()
        try:
            yield
        finally:
            self.record(name, (time.monotonic() - started) * 1000.0)
