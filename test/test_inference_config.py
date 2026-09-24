"""Inference input size and the offline OpenVINO export.

Neither option is on by default: a reduced input size trades small-object
recall for speed, and that trade is validated in the field, not assumed. What
is tested here is that the configured value reaches the detector unchanged, that
a malformed value from the GCS parameter sheet is caught before flight, and that
the export tool refuses to do anything implicit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import threading

import pytest

from mvp_mission_bebop.parameters import MissionParameters, VisionConfig
from mvp_mission_bebop.perception.worker import (
    SynchronousPerception,
    detector_kwargs,
    normalize_imgsz,
)

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "export_openvino_model.py")


def load_export_script():
    spec = importlib.util.spec_from_file_location("export_openvino_model", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ normalization


@pytest.mark.parametrize(
    "value, expected",
    [(None, None), ("", None), ("  ", None), (480, 480), (480.0, 480), ("480", 480), ("640.0", 640)],
)
def test_integral_sizes_are_accepted_in_every_form_the_gcs_can_send(value, expected):
    assert normalize_imgsz(value) == expected


@pytest.mark.parametrize("value", [481, 0, -32, 480.5, "abc", float("nan")])
def test_a_size_off_the_feature_stride_is_refused(value):
    with pytest.raises(ValueError):
        normalize_imgsz(value)


@pytest.mark.parametrize("value", [True, [480], {"imgsz": 480}])
def test_a_non_numeric_size_is_a_type_error(value):
    with pytest.raises(TypeError):
        normalize_imgsz(value)


def test_unset_keywords_are_omitted_rather_than_passed_as_none():
    """An older Nectar ``Detector.detect`` has no ``imgsz`` parameter at all."""
    assert detector_kwargs(None, None) == {}
    assert detector_kwargs(0.5, None) == {"conf": 0.5}
    assert detector_kwargs(0.5, "480") == {"conf": 0.5, "imgsz": 480}


# ------------------------------------------------------------- configuration


def test_the_reduced_size_is_opt_in():
    assert VisionConfig().inference_imgsz is None


def test_the_new_fields_round_trip_through_the_config_file(tmp_path):
    params = MissionParameters()
    params.vision.inference_imgsz = 480
    params.vision.perception_max_age_sec = 0.4
    target = tmp_path / "mission_config.json"
    params.save_to_file(str(target))

    stored = json.loads(target.read_text())["vision"]
    assert stored["inference_imgsz"] == 480
    loaded = MissionParameters.load_from_file(str(target))
    assert loaded.vision.inference_imgsz == 480
    assert loaded.vision.perception_max_age_sec == 0.4


def test_a_config_written_before_the_fields_existed_still_loads(tmp_path):
    target = tmp_path / "mission_config.json"
    target.write_text(json.dumps({"vision": {"confidence_threshold": 0.6}}))
    loaded = MissionParameters.load_from_file(str(target))
    assert loaded.vision.inference_imgsz is None
    assert loaded.vision.confidence_threshold == 0.6


class RecordingDetector:
    def __init__(self):
        self.calls = []

    def detect(self, _frame, **kwargs):
        self.calls.append(kwargs)

        class Empty:
            @staticmethod
            def filter_by_class(_classes):
                return []

        return Empty()


def test_the_configured_size_reaches_the_detector_from_a_flight_stage():
    from mvp_mission_bebop.estimation.calibration import SpeedCalibration
    from mvp_mission_bebop.steps.search import ForwardSearchStep
    from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
    from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

    class Odometry:
        relative_altitude = 1.0
        speed = vx = vy = vz = x = y = 0.0

        def snapshot(self):
            return self

        telemetry_health = staticmethod(lambda: TelemetryHealth.HEALTHY)
        is_ceiling_breached = staticmethod(lambda: False)

    class Drone:
        no_fly = True

        def move_velocity(self, **_kwargs):
            pass

    class Ctx:
        def __init__(self):
            self.params = MissionParameters()
            self.params.vision.inference_imgsz = 480
            self.params.timeouts.search_timeout_sec = 0.05
            self.params.kinematics.control_loop_hz = 200.0
            self.drone = Drone()
            self.odom_supervisor = Odometry()
            self.governor = type("G", (), {"compute_vz": staticmethod(lambda _a, _d=None, vx_commanded=0.0: 0.0)})()
            self.failsafe = FailsafeSupervisor(
                drone_actuator=self.drone,
                odom_supervisor=self.odom_supervisor,
                timeouts_cfg=self.params.timeouts,
                kinematics_cfg=self.params.kinematics,
            )
            self.speed_calibration = SpeedCalibration(1.0)
            self.blackboard = type("B", (), {})()
            self.emergency_event = threading.Event()
            self.stage_jump_event = threading.Event()
            self.current_tilt_deg = -20.0
            self.detector = RecordingDetector()
            self.perception = SynchronousPerception(self)

        def interrupted(self):
            return False

        def grab_frame(self, timeout_sec=1.0):
            return object()

        def publish_annotated_stream(self, *_args):
            return None

    ctx = Ctx()
    ForwardSearchStep().execute(ctx)

    assert ctx.detector.calls, "the stage never ran the detector"
    assert all(call == {"conf": 0.5, "imgsz": 480} for call in ctx.detector.calls)


# ------------------------------------------------------------ export tool


def test_the_export_tool_resolves_relative_weights_against_the_config(tmp_path):
    script = load_export_script()
    (tmp_path / "yolov8n.pt").write_bytes(b"")
    config = tmp_path / "mission_config.json"
    config.write_text("{}")

    assert script.resolve_model_path("yolov8n.pt", str(config)) == str(tmp_path / "yolov8n.pt")


def test_the_export_tool_never_treats_a_missing_file_as_a_download(tmp_path):
    """Ultralytics would fetch ``yolov8n.pt`` by name; the tool must refuse."""
    script = load_export_script()
    with pytest.raises(FileNotFoundError):
        script.resolve_model_path("yolov8n.pt", str(tmp_path / "mission_config.json"))


def test_the_export_tool_refuses_an_artefact_that_is_not_pytorch_weights(tmp_path):
    script = load_export_script()
    with pytest.raises(ValueError):
        script.resolve_model_path("yolov8n_openvino_model", str(tmp_path / "c.json"))


@pytest.mark.parametrize("value, expected", [(None, 640), ("", 640), ("480", 480), ("480.0", 480)])
def test_the_export_size_follows_the_mission_config_convention(value, expected):
    assert load_export_script().parse_imgsz(value) == expected


@pytest.mark.parametrize("value", ["481", "0", "abc"])
def test_the_export_tool_refuses_a_size_off_the_stride(value):
    with pytest.raises(argparse.ArgumentTypeError):
        load_export_script().parse_imgsz(value)


def test_the_export_tool_reads_the_vision_section_and_tolerates_a_missing_file(tmp_path):
    script = load_export_script()
    assert script.load_vision_config(str(tmp_path / "absent.json")) == {}
    config = tmp_path / "mission_config.json"
    config.write_text(json.dumps({"vision": {"model_path": "m.pt", "inference_imgsz": 480}}))
    assert script.load_vision_config(str(config))["inference_imgsz"] == 480
