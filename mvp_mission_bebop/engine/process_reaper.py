"""Kill the child processes libraries leave behind, before the mission exits.

The mission itself starts no ``multiprocessing`` children. Libraries do: the
OpenVINO runtime's usage telemetry forks one to send an HTTP request. A child
forked after the mission installed its signal handlers inherits them, and the
SIGTERM that ``multiprocessing``'s exit hook sends it runs the mission's
emergency handler instead of ending it; the hook then joins it forever, and
the station never receives the mission's exit code. SIGKILL cannot be handled.
"""

from __future__ import annotations

import atexit
import logging
import multiprocessing
import multiprocessing.util  # noqa: F401 - registers its exit hook before ours
from typing import List

logger = logging.getLogger("ProcessReaper")


def reap_child_processes(timeout_sec: float = 2.0) -> List[int]:
    """SIGKILL every live ``multiprocessing`` child and wait for it.

    Parameters
    ----------
    timeout_sec : float
        Longest wait for each child after the kill, seconds.

    Returns
    -------
    list of int
        PIDs of the children killed.
    """
    reaped: List[int] = []
    for child in multiprocessing.active_children():
        logger.info("Killing leftover child process %s (pid %s) before exit.", child.name, child.pid)
        try:
            child.kill()
            child.join(timeout_sec)
        except (OSError, ValueError) as exc:
            logger.warning("Could not reap child %s: %s", child.pid, exc)
            continue
        if child.pid is not None:
            reaped.append(child.pid)
    return reaped


def install_exit_reaper() -> None:
    """Run :func:`reap_child_processes` at interpreter exit, ahead of ``multiprocessing``.

    ``atexit`` runs handlers last-in first-out, and ``multiprocessing.util``
    (imported above) has already registered the exit hook that joins every
    child, so this one runs first and leaves it nothing to wait for.
    """
    atexit.register(reap_child_processes)
