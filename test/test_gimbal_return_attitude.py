"""The marker-guided return confirms the gimbal reached ``rtl.camera_tilt_deg``.

Every flight log since 2026-10-24 shows the step commanding -80 deg and then
centering for its whole window without ever seeing the pad marker. Nothing in
the mission read the camera back, so a gimbal that stopped short was
indistinguishable from a marker that was not there. ``_engage_return_attitude``
now waits on the aircraft's reported orientation and says which it was.
"""

from __future__ import annotations

import logging
import types
from typing import List

import pytest

from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.telemetry.gimbal import GimbalStateMonitor


class _Recorder(logging.Handler):
    """Attached directly: this environment's pytest does not deliver ``caplog``."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self, level: int) -> str:
        return "\n".join(r.getMessage() for r in self.records if r.levelno == level)


@pytest.fixture
def recorder():
    handler = _Recorder()
    log = logging.getLogger("Step5RTL")
    previous = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    yield handler
    log.removeHandler(handler)
    log.setLevel(previous)


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rtl_module, "_GIMBAL_SETTLE_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(rtl_module, "_GIMBAL_CHECK_PERIOD_SEC", 0.1)


def _ctx(monitor=None, tilt: float = -80.0, interrupted: bool = False):
    tilts: List[float] = []
    drone = types.SimpleNamespace(camera_control=lambda tilt, pan=0.0: tilts.append(tilt))
    ctx = types.SimpleNamespace(
        params=types.SimpleNamespace(rtl=types.SimpleNamespace(camera_tilt_deg=tilt)),
        drone=drone,
        current_tilt_deg=None,
        interrupted=lambda: interrupted,
    )
    if monitor is not None:
        ctx.gimbal_monitor = monitor
    return ctx, tilts


def _report(monitor: GimbalStateMonitor, tilt: float) -> None:
    monitor.orientation_callback(types.SimpleNamespace(x=tilt, y=0.0, z=0.0))


def test_without_a_monitor_the_command_is_sent_once_and_nothing_waits(recorder) -> None:
    ctx, tilts = _ctx()
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert tilts == [-80.0]
    assert ctx.current_tilt_deg == -80.0


def test_without_telemetry_the_step_flies_open_loop_and_says_so(recorder) -> None:
    ctx, tilts = _ctx(GimbalStateMonitor())
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert tilts == [-80.0]
    assert "no gimbal telemetry" in recorder.text(logging.WARNING)


def test_a_camera_at_the_target_is_confirmed_without_resending(recorder) -> None:
    monitor = GimbalStateMonitor()
    _report(monitor, -79.2)
    ctx, tilts = _ctx(monitor)
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert tilts == [-80.0]
    assert "gimbal confirmed at -79.2" in recorder.text(logging.INFO)
    assert recorder.text(logging.WARNING) == ""


def test_a_camera_that_stops_short_is_resent_and_reported(recorder) -> None:
    monitor = GimbalStateMonitor()
    _report(monitor, -52.0)
    ctx, tilts = _ctx(monitor)
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert len(tilts) > 1 and set(tilts) == {-80.0}
    warning = recorder.text(logging.WARNING)
    assert "gimbal at -52.0 deg, commanded -80.0" in warning
    assert "marker" in warning.lower()
    assert ctx.current_tilt_deg == -80.0


def test_a_target_beyond_the_reported_limit_names_the_limit(recorder) -> None:
    monitor = GimbalStateMonitor()
    monitor.limits_callback(types.SimpleNamespace(data=[-65.0, 17.0, -35.0, 35.0]))
    _report(monitor, -65.0)
    ctx, _ = _ctx(monitor)
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    warning = recorder.text(logging.WARNING)
    assert "-65.0" in warning and "limit" in warning.lower()


def test_an_interrupted_step_does_not_wait(recorder) -> None:
    monitor = GimbalStateMonitor()
    _report(monitor, -40.0)
    ctx, tilts = _ctx(monitor, interrupted=True)
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert tilts == [-80.0]


def test_the_reported_limits_are_always_logged(recorder) -> None:
    monitor = GimbalStateMonitor()
    monitor.limits_callback(types.SimpleNamespace(data=[-83.0, 17.0, -35.0, 35.0]))
    _report(monitor, -80.0)
    ctx, _ = _ctx(monitor)
    ClosedLoopRTLStep()._engage_return_attitude(ctx)
    assert "tilt limits -83.0 to 17.0 deg" in recorder.text(logging.INFO)
