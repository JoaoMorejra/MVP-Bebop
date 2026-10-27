"""Suite-wide isolation of the copilot's synthesis reachability probe."""

from __future__ import annotations

import sys
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def _synthesis_never_probed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep every test off the synthesis endpoint.

    ``BMG_SPEECH_OFFLINE=0`` makes ``synthesis_reachable`` answer True without
    a socket, so a test that builds a real announcer neither opens a TCP
    connection nor depends on the machine having a route (the fair station has
    none). Tests of the probe itself set the variable explicitly. The verdict
    cache is cleared around each test so none inherits another's.
    """
    monkeypatch.setenv("BMG_SPEECH_OFFLINE", "0")
    module = sys.modules.get("mvp_mission_bebop.telemetry.announcer")
    if module is not None:
        module._reachability.clear()
    yield
    module = sys.modules.get("mvp_mission_bebop.telemetry.announcer")
    if module is not None:
        module._reachability.clear()
