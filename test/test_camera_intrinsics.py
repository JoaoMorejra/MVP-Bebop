"""Stage 5 must pose the marker with the Bebop 2 calibration, not the SDK sample.

``nectar.vision.Aruco`` always loads ``camera_matrix.txt`` from the Nectar
calibration package, which ships a sample captured from another camera
(principal point near (298, 308)). On the 856x480 Bebop stream that biases every
marker pose by a fixed angle. The driver package installs the airframe's own
``camera_info`` calibration, and the RTL step now applies it to the detector.
"""

from __future__ import annotations

import sys
import textwrap
import types
from types import SimpleNamespace

import numpy as np
import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception import intrinsics
from mvp_mission_bebop.steps import rtl

BEBOP_YAML = textwrap.dedent(
    """\
    image_width: 856
    image_height: 480
    camera_name: bebop_front
    camera_matrix:
      rows: 3
      cols: 3
      data: [537.292878, 0.000000, 427.331854, 0.000000, 527.000348, 240.226888, 0.000000, 0.000000, 1.000000]
    distortion_model: plumb_bob
    distortion_coefficients:
      rows: 1
      cols: 5
      data: [0.004974, -0.000130, -0.001212, 0.002192, 0.000000]
    """
)

SDK_SAMPLE_MATRIX = np.array(
    [[871.33, 0.0, 298.57], [0.0, 856.81, 308.48], [0.0, 0.0, 1.0]], dtype=np.float64
)


@pytest.fixture
def bebop_yaml(tmp_path):
    path = tmp_path / "bebop2_camera_calib.yaml"
    path.write_text(BEBOP_YAML, encoding="utf-8")
    return str(path)


def test_camera_info_yaml_is_parsed_into_matrix_distortion_and_size(bebop_yaml):
    calibration = intrinsics.load_camera_info_yaml(bebop_yaml)

    assert calibration.camera_matrix.shape == (3, 3)
    assert calibration.camera_matrix[0, 2] == pytest.approx(427.331854)
    assert calibration.camera_matrix[1, 2] == pytest.approx(240.226888)
    assert calibration.distortion.shape == (5,)
    assert calibration.distortion[0] == pytest.approx(0.004974)
    assert (calibration.width, calibration.height) == (856, 480)
    assert calibration.source == bebop_yaml


def test_camera_info_yaml_with_a_short_matrix_is_rejected(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text(
        "image_width: 856\nimage_height: 480\n"
        "camera_matrix: {rows: 3, cols: 3, data: [1, 0, 0]}\n"
        "distortion_coefficients: {rows: 1, cols: 5, data: [0, 0, 0, 0, 0]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        intrinsics.load_camera_info_yaml(str(path))


def test_missing_driver_package_resolves_to_none(monkeypatch):
    def not_installed(_name):
        raise LookupError("ros2_bebop_driver")

    monkeypatch.setattr(intrinsics, "_package_share_directory", not_installed)
    assert intrinsics.resolve_driver_calibration_path() is None


def test_driver_calibration_is_applied_to_the_detector(monkeypatch, bebop_yaml):
    detector = SimpleNamespace(camera_matrix=SDK_SAMPLE_MATRIX.copy(), camera_distortion=np.zeros(5))
    monkeypatch.setattr(intrinsics, "resolve_driver_calibration_path", lambda: bebop_yaml)

    applied = intrinsics.apply_driver_calibration(detector)

    assert applied is not None and applied.source == bebop_yaml
    assert detector.camera_matrix[0, 2] == pytest.approx(427.331854)
    assert detector.camera_distortion[0] == pytest.approx(0.004974)


def test_detector_keeps_its_intrinsics_when_no_driver_calibration_exists(monkeypatch):
    detector = SimpleNamespace(camera_matrix=SDK_SAMPLE_MATRIX.copy(), camera_distortion=np.zeros(5))
    monkeypatch.setattr(intrinsics, "resolve_driver_calibration_path", lambda: None)

    assert intrinsics.apply_driver_calibration(detector) is None
    assert detector.camera_matrix[0, 2] == pytest.approx(298.57)


def test_rtl_sensor_poses_with_the_bebop_calibration(monkeypatch, bebop_yaml, caplog):
    class FakeAruco:
        def __init__(self, marker_dict, tag_size):
            self.marker_dict = marker_dict
            self.tag_size = tag_size
            self.camera_matrix = SDK_SAMPLE_MATRIX.copy()
            self.camera_distortion = np.zeros(5)

    fake_vision = types.ModuleType("nectar.vision")
    fake_vision.Aruco = FakeAruco
    monkeypatch.setitem(sys.modules, "nectar.vision", fake_vision)
    monkeypatch.setattr(intrinsics, "resolve_driver_calibration_path", lambda: bebop_yaml)

    ctx = SimpleNamespace(params=MissionParameters(), frame_width=856, frame_height=480)
    step = rtl.ClosedLoopRTLStep.__new__(rtl.ClosedLoopRTLStep)

    with caplog.at_level("WARNING"):
        sensor = step._build_sensor(ctx)

    assert sensor is not None
    assert "do not match the live frame" not in caplog.text
