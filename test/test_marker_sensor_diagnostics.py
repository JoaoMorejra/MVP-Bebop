"""The marker sensor says why a frame produced no sighting.

Until now every one of these read as ``marker absent`` in the flight log: no
frame, nothing detected, a marker of the right dictionary but the wrong id, a
mirrored pose, a detector exception. Four flights in a row logged 75 absent
frames and the log could not say which of them it was.
"""

from __future__ import annotations

from typing import List

import numpy as np

from mvp_mission_bebop.steps.rtl import ArucoMarkerSensor


class _Detector:
    tag_size = 0.20

    def __init__(self, returns, raises_on=()):
        self._returns = list(returns)
        self._raises_on = set(raises_on)
        self.calls = 0

    def pose_estimate(self, img, draw=False):
        self.calls += 1
        if self.calls in self._raises_on:
            raise RuntimeError("degenerate corner set")
        return self._returns[min(self.calls - 1, len(self._returns) - 1)]


def _sensor(returns, target_id=8, raises_on=()):
    return ArucoMarkerSensor(_Detector(returns, raises_on), target_id=target_id, backend="sdk")


FRAME = np.zeros((480, 856, 3), dtype=np.uint8)


def _run(sensor, frames: List[object]):
    return [sensor.observe(frame) for frame in frames]


def test_a_missing_frame_is_counted_apart_from_an_empty_one() -> None:
    sensor = _sensor([(None, None, None)])
    _run(sensor, [None, None, FRAME])
    d = sensor.diagnostics()
    assert (d["frames"], d["no_frame"], d["no_detection"], d["accepted"]) == (3, 2, 1, 0)


def test_a_marker_of_the_wrong_id_is_named() -> None:
    sensor = _sensor([(3, np.array([0.0, 0.0, 0.8]), 0.0)])
    _run(sensor, [FRAME, FRAME])
    d = sensor.diagnostics()
    assert d["wrong_ids"] == {3: 2}
    assert d["accepted"] == 0
    assert "wrong ids {3: 2}" in sensor.diagnostics_line()


def test_a_mirrored_pose_counts_as_degenerate() -> None:
    sensor = _sensor([(8, np.array([0.0, 0.0, -0.8]), 0.0)])
    _run(sensor, [FRAME])
    d = sensor.diagnostics()
    assert d["degenerate"] == 1 and d["accepted"] == 0


def test_a_good_sighting_is_accepted_and_returned() -> None:
    sensor = _sensor([(8, np.array([0.1, 0.0, 0.8]), 0.0)])
    observation = sensor.observe(FRAME)
    assert observation is not None
    assert sensor.diagnostics()["accepted"] == 1


def test_a_detector_exception_is_counted_and_its_type_kept() -> None:
    sensor = _sensor([(None, None, None)], raises_on=(1,))
    assert sensor.observe(FRAME) is None
    d = sensor.diagnostics()
    assert d["errors"] == 1 and d["last_error"] == "RuntimeError"
    assert sensor.failures == 1


def test_the_line_summarises_every_counter() -> None:
    sensor = _sensor([(None, None, None)])
    _run(sensor, [None, FRAME, FRAME])
    line = sensor.diagnostics_line()
    for token in ("frames 3", "no frame 1", "nothing detected 2", "accepted 0", "errors 0"):
        assert token in line
