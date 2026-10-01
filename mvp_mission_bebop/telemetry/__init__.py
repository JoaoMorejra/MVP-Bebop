"""Telemetry supervision and acoustic notification package.

Exports resolve on first access (PEP 562): importing any submodule, as
``telemetry.odometry`` is on every mission start, no longer loads the
announcer and its speech synthesizer (7.1).
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Final

_EXPORTS: Final[Dict[str, str]] = {
    "AudioPlaybackDevice": "mvp_mission_bebop.telemetry.announcer",
    "MissionAudioAnnouncer": "mvp_mission_bebop.telemetry.announcer",
    "announce": "mvp_mission_bebop.telemetry.announcer",
    "announce_sync": "mvp_mission_bebop.telemetry.announcer",
    "FailsafeSupervisor": "mvp_mission_bebop.telemetry.failsafe",
    "OdometrySupervisor": "mvp_mission_bebop.telemetry.odometry",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
