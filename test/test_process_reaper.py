"""The mission exits even when a library left a child process behind.

Measured: OpenVINO's usage telemetry forks a ``multiprocessing`` child to send
an HTTP request. Forked after the mission installed its signal handlers, the
child answered the SIGTERM of ``multiprocessing``'s exit hook with the
mission's own emergency handler, which waits for a landing burst, so the
mission never exited after touchdown and the station never saw its exit code.
"""

from __future__ import annotations

import multiprocessing
import signal
import time

from mvp_mission_bebop.engine.process_reaper import reap_child_processes


def _ignore_term_and_sleep():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)


def test_a_child_that_ignores_sigterm_is_killed():
    child = multiprocessing.get_context("fork").Process(target=_ignore_term_and_sleep, daemon=True)
    child.start()
    time.sleep(0.2)
    started = time.monotonic()
    reaped = reap_child_processes(timeout_sec=1.0)
    assert child.pid in reaped
    assert not child.is_alive()
    assert time.monotonic() - started < 3.0
    assert multiprocessing.active_children() == []


def test_nothing_to_reap_is_a_no_op():
    assert reap_child_processes() == []


_HANG_PROGRAM = r"""
import multiprocessing, signal, sys, threading, time
from mvp_mission_bebop.engine.process_reaper import install_exit_reaper
install_exit_reaper()
# The mission's emergency handler: on SIGTERM it waits for a landing that,
# in a forked child, never comes.
signal.signal(signal.SIGTERM, lambda *_: threading.Event().wait())
child = multiprocessing.get_context("fork").Process(target=time.sleep, args=(60,), daemon=True)
child.start()
sys.exit(0)
"""


def test_the_mission_exits_with_a_child_that_inherited_its_handlers():
    import subprocess
    import sys

    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-c", _HANG_PROGRAM], timeout=30, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr[-1500:]
    assert time.monotonic() - started < 15.0
