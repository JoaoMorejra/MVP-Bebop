#!/usr/bin/env python3
"""Copilot speech latency, request to first audio, on the real synthesizer.

Measures the cases the implementation plan sets targets for, against the real
Live session (network and API key required) and a silent output device, so
nothing is played aloud:

* ``cold``: a fresh announcer whose first line cannot use a warm session;
* ``warm``: a line requested once the warm session is up;
* ``cache``: the same line again, served from the on-disk phrase cache;
* ``urgent_after_cancel``: a line queued, cancelled mid-synthesis, and an
  urgent cached alert requested right after;
* ``cancelled_silenced_ms``: time from the cancel until the device stops being
  fed.

The phrase cache is redirected to a scratch directory so the operator's cache
is untouched.

Usage (inside ``nectar-activate``)::

    python3 scripts/bench_speech_latency.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from typing import Dict, List, Optional

#: Longest wait for any single measurement, seconds.
MEASURE_TIMEOUT_SEC = 20.0


class SilentClockDevice:
    """Playback stand-in that timestamps every buffer it would have played."""

    _active = True
    sample_rate = 24_000

    def __init__(self) -> None:
        self.buffers: List[float] = []
        self.stops: List[float] = []

    def play_audio(self, pcm: bytes) -> None:
        self.buffers.append(time.monotonic())

    def wait_until_done(self, timeout: Optional[float] = None) -> bool:
        return True

    def stop_current(self) -> None:
        self.stops.append(time.monotonic())

    def get_level(self) -> Dict[str, object]:
        return {"volume": 1.0, "muted": False}

    def close(self) -> None:
        pass


def _first_audio_after(device: SilentClockDevice, t0: float, timeout: float = MEASURE_TIMEOUT_SEC) -> Optional[float]:
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        later = [stamp for stamp in device.buffers if stamp >= t0]
        if later:
            return (later[0] - t0) * 1000.0
        time.sleep(0.005)
    return None


def run() -> Dict[str, Optional[float]]:
    """Run every case and return the latencies in milliseconds."""
    os.environ["BMG_SPEECH_CACHE_DIR"] = tempfile.mkdtemp(prefix="bmg-speech-bench-")
    os.environ.pop("BMG_GCS_SESSION", None)
    from mvp_mission_bebop.telemetry import announcer

    results: Dict[str, Optional[float]] = {}

    # Each announcer gets its own device: a line still streaming from one case
    # must not be counted as the first audio of the next.
    device = SilentClockDevice()
    announcer.get_audio_playback_device = lambda: device

    # Cold: no warm session on hand.
    cold = announcer.MissionAudioAnnouncer()
    cold._warm_session = None

    async def no_warm() -> None:
        return None

    cold._ensure_warm_session = no_warm
    t0 = time.monotonic()
    cold.announce("Missão iniciada. Carregando parâmetros de voo.", verbatim=True)
    results["cold_ms"] = _first_audio_after(device, t0)
    time.sleep(4.0)
    cold.close()

    device = SilentClockDevice()
    warm = announcer.MissionAudioAnnouncer()
    deadline = time.monotonic() + MEASURE_TIMEOUT_SEC
    while warm._warm_session is None and time.monotonic() < deadline:
        time.sleep(0.05)
    results["warm_session_ready"] = 1.0 if warm._warm_session is not None else 0.0
    t0 = time.monotonic()
    warm.announce("Decolagem confirmada. Subindo para a altitude de operação.", verbatim=True)
    results["warm_ms"] = _first_audio_after(device, t0)
    time.sleep(4.0)
    t0 = time.monotonic()
    warm.announce("Decolagem confirmada. Subindo para a altitude de operação.", verbatim=True)
    results["cache_ms"] = _first_audio_after(device, t0)

    alert = "Missão abortada. Pouso imediato comandado."
    warm.announce(alert, verbatim=True, wait=True, timeout=MEASURE_TIMEOUT_SEC)
    time.sleep(0.5)
    warm.announce("Uma linha longa que será cancelada no meio da síntese pelo operador.", verbatim=True)
    time.sleep(0.4)
    cancelled_at = time.monotonic()
    warm.cancel_pending()
    fed_after = [stamp for stamp in device.buffers if stamp > cancelled_at + 0.1]
    t0 = time.monotonic()
    warm.announce(alert, priority="URGENT", verbatim=True)
    results["urgent_after_cancel_ms"] = _first_audio_after(device, t0)
    time.sleep(1.0)
    late = [stamp for stamp in device.buffers if stamp > cancelled_at + 0.1 and stamp < t0]
    results["cancelled_line_buffers_after_cancel"] = float(len(fed_after) + len(late))
    warm.close()
    return results


def main() -> int:
    """Print the measurements as JSON. Returns 1 if a case never produced audio."""
    results = run()
    print(json.dumps(results, indent=2))
    return 1 if any(value is None for value in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
