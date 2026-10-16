"""The Stage 5 fields the station's advanced settings edit reach the mission."""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import numpy as np

from mvp_mission_bebop.config_audit import audit_safe_envelopes
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception import intrinsics
from mvp_mission_bebop.steps import rtl

EDITED = {"rtl": {"target_aruco_id": 12, "marker_dict": "DICT_4X4_50", "tag_size": 0.16}}


def _edited() -> MissionParameters:
    params = MissionParameters()
    params.update_from_dict(EDITED)
    return params


def test_the_edited_marker_identity_is_inside_every_envelope_with_the_sdk_resolver():
    assert audit_safe_envelopes(_edited()) == []


def test_an_id_past_the_dictionary_size_is_reported_with_the_sdk_resolver():
    params = _edited()
    params.update_from_dict({"rtl": {"target_aruco_id": 50}})
    assert [d.path for d in audit_safe_envelopes(params)] == ["rtl.target_aruco_id"]


def test_stage5_builds_its_detector_from_the_edited_fields(monkeypatch):
    built = {}

    class FakeAruco:
        def __init__(self, marker_dict, tag_size):
            built.update(marker_dict=marker_dict, tag_size=tag_size)
            self.tag_size = tag_size
            self.camera_matrix = np.array([[537.0, 0.0, 428.0], [0.0, 537.0, 240.0], [0.0, 0.0, 1.0]])
            self.camera_distortion = np.zeros(5)

    fake_vision = types.ModuleType("nectar.vision")
    fake_vision.Aruco = FakeAruco
    monkeypatch.setitem(sys.modules, "nectar.vision", fake_vision)
    monkeypatch.setattr(intrinsics, "resolve_driver_calibration_path", lambda: None)

    ctx = SimpleNamespace(params=_edited(), frame_width=856, frame_height=480)
    sensor = rtl.ClosedLoopRTLStep.__new__(rtl.ClosedLoopRTLStep)._build_sensor(ctx)

    assert sensor is not None
    assert built == {"marker_dict": "DICT_4X4_50", "tag_size": 0.16}
    assert sensor._target_id == 12


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_stage5_centers_for_the_edited_window_and_then_lands(monkeypatch):
    """``--params-json`` / ``go`` -> ``update_from_dict`` -> the centering deadline."""
    from test_aruco_rtl import Ctx, ScriptedSensor, sighting

    windows = []

    class RecordingDeadline(rtl.Deadline):
        def __init__(self, duration_sec, **kwargs):
            windows.append(duration_sec)
            super().__init__(duration_sec, **kwargs)

    class StuckSensor(ScriptedSensor):
        """The pad stays in view at a fixed offset: the centering never converges."""

        def observe(self, frame):
            self._seen += 1
            if self._seen <= self._acquire_after:
                return None
            return sighting(self._ex, self._ey, 1.0, marker_id=self.target_id)

    monkeypatch.setattr(rtl, "Deadline", RecordingDeadline)
    ctx = Ctx(altitude=0.30)
    ctx.params.rtl.timeout_sec = 4.0
    ctx.params.update_from_dict({"rtl": {"centering_timeout_sec": 0.75}})
    sensor = StuckSensor(ctx.drone, acquire_after=3, ex=0.35, ey=-0.25)
    handler = _Lines()
    target = logging.getLogger("Step5RTL")
    target.addHandler(handler)
    try:
        status = rtl.ClosedLoopRTLStep(sensor=sensor).execute(ctx)
    finally:
        target.removeHandler(handler)

    assert status is rtl.StepStatus.SUCCESS
    assert 0.75 in windows
    assert any("Centering window of 0.8 s expired" in line for line in handler.lines)
    assert ctx.drone.land_calls > 0
