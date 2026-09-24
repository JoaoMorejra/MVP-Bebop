"""Contract between the mission's detection overlay and the GCS MJPEG bridge.

The perception worker no longer republishes annotated frames; it publishes
``bmg.detections.v1`` JSON and the bridge draws it over the raw 33 Hz stream.
The two ends live in different processes and are written in different places
(``mvp_mission_bebop/perception/summary.py`` and
``bebop_mission_control/streamer/mjpeg_server.py``), so what the mission encodes
is decoded here by the bridge's own parser, and the bridge's source priority is
exercised directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

import numpy as np
import pytest

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.perception.summary import (
    DETECTION_SUMMARY_SCHEMA,
    encode_detection_summary,
)
from mvp_mission_bebop.perception.worker import PerceptionSample

_BRIDGE = os.path.join(
    os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "mjpeg_server.py"
)


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("mjpeg_server_under_test", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: dataclasses resolve their string
    # annotations through ``sys.modules[cls.__module__]``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class Detection:
    def __init__(self, bbox=(400, 220, 456, 260), confidence=0.78, class_name="bicycle"):
        self.class_name = class_name
        self.class_id = 1
        self.confidence = confidence
        self.bbox = bbox
        self.center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        self.area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def encoded(detections=(Detection(),), status="STEP 2: SEARCH", width=856, height=480):
    return encode_detection_summary(
        detections=MissionContext._describe_detections(list(detections)),
        frame_width=width,
        frame_height=height,
        status=status,
        stamp_sec=1790261307.12,
        inference_ms=81.4,
    )


# ------------------------------------------------------------------ schema


def test_both_ends_agree_on_the_schema_identifier(bridge):
    assert bridge.DETECTION_SUMMARY_SCHEMA == DETECTION_SUMMARY_SCHEMA


def test_what_the_mission_encodes_the_bridge_decodes(bridge):
    summary = bridge.parse_detection_summary(encoded(status="STEP 3: APPROACHING"))

    assert summary is not None
    assert (summary.width, summary.height) == (856, 480)
    assert summary.status == "STEP 3: APPROACHING"
    assert summary.inference_ms == pytest.approx(81.4)
    assert len(summary.detections) == 1
    box = summary.detections[0]
    assert box.class_name == "bicycle"
    assert box.confidence == pytest.approx(0.78)
    assert box.bbox == (400.0, 220.0, 456.0, 260.0)


def test_an_empty_result_is_still_a_valid_overlay(bridge):
    """No boxes is information: the caption still says inference is running."""
    summary = bridge.parse_detection_summary(encoded(detections=()))
    assert summary is not None and summary.detections == ()


def test_the_payload_is_small(bridge):
    """The point of the change: hundreds of bytes instead of a 1.2 MB frame."""
    assert len(encoded(detections=[Detection()] * 5).encode("utf-8")) < 2048


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps([1, 2]),
        json.dumps({"schema": "bmg.detections.v0", "frame": {"width": 856, "height": 480}}),
        json.dumps({"schema": DETECTION_SUMMARY_SCHEMA}),
        json.dumps({"schema": DETECTION_SUMMARY_SCHEMA, "frame": {"width": 0, "height": 480}}),
    ],
)
def test_the_bridge_rejects_anything_it_cannot_draw_correctly(bridge, text):
    assert bridge.parse_detection_summary(text) is None


def test_malformed_boxes_are_dropped_individually(bridge):
    payload = json.loads(encoded())
    payload["detections"].append({"class_name": "car", "bbox_xyxy": [10, 10, 5, 20]})
    payload["detections"].append({"class_name": "car", "bbox_xyxy": "garbage"})
    summary = bridge.parse_detection_summary(json.dumps(payload))
    assert [box.class_name for box in summary.detections] == ["bicycle"]


@pytest.mark.parametrize("width, height", [(0, 480), (856, -1)])
def test_the_mission_refuses_to_encode_a_degenerate_frame(width, height):
    with pytest.raises(ValueError):
        encoded(width=width, height=height)


# ----------------------------------------------------------------- drawing


def test_boxes_are_drawn_where_the_detector_saw_them(bridge):
    frame = np.zeros((480, 856, 3), dtype=np.uint8)
    summary = bridge.parse_detection_summary(encoded(status=""))
    drawn = bridge.draw_detection_summary(frame, summary)

    assert drawn[240, 400].any(), "no box edge on the left side of the detection"
    assert not drawn[240, 300].any(), "pixels far from the box were painted"


def test_boxes_follow_a_change_in_stream_geometry(bridge):
    """The raw stream may renegotiate resolution; boxes must scale with it."""
    frame = np.zeros((960, 1712, 3), dtype=np.uint8)
    summary = bridge.parse_detection_summary(encoded(status=""))
    drawn = bridge.draw_detection_summary(frame, summary)

    assert drawn[480, 800].any(), "the box was not rescaled to the doubled geometry"
    assert not drawn[480, 400].any(), "the box was drawn at the detector's geometry"


def test_a_read_only_frame_is_copied_not_mutated(bridge):
    frame = np.zeros((480, 856, 3), dtype=np.uint8)
    frame.flags.writeable = False
    drawn = bridge.draw_detection_summary(frame, bridge.parse_detection_summary(encoded()))

    assert drawn is not frame
    assert not frame.any()


def test_an_overlay_expires(bridge):
    state = bridge.OverlayState()
    summary = bridge.parse_detection_summary(encoded())
    state.update(summary, now=100.0)

    assert state.fresh(1.0, now=100.9) is summary
    assert state.fresh(1.0, now=101.1) is None


# ---------------------------------------------------------- source priority


class RawBridge:
    """The state ``MJPEGNode._on_raw`` reads, without a ROS node."""

    raw_topic = "/bebop/camera/image_raw"
    boxes_topic = "/bebop/camera/detection_boxes"

    def __init__(self, last_detection_at):
        self._last_detection_at = last_detection_at
        self.published = []

    def _decode(self, _msg, _source):
        return np.zeros((480, 856, 3), dtype=np.uint8)

    def _encode(self, msg, source):
        self._publish_frame(self._decode(msg, source), source, "bgr8")

    def _publish_frame(self, frame, source, _encoding):
        self.published.append((source, frame))


def raw_message():
    return types.SimpleNamespace(encoding="bgr8")


def test_raw_frames_carry_the_fresh_overlay(bridge, monkeypatch):
    overlay = bridge.OverlayState()
    overlay.update(bridge.parse_detection_summary(encoded()))
    monkeypatch.setattr(bridge, "OVERLAY", overlay)
    node = RawBridge(last_detection_at=0.0)

    bridge.MJPEGNode._on_raw(node, raw_message())

    assert [source for source, _ in node.published] == [node.boxes_topic]
    assert node.published[0][1].any(), "the overlay was not drawn"


def test_raw_frames_pass_through_once_the_overlay_is_stale(bridge, monkeypatch):
    overlay = bridge.OverlayState()
    overlay.update(bridge.parse_detection_summary(encoded()), now=0.0)
    monkeypatch.setattr(bridge, "OVERLAY", overlay)
    node = RawBridge(last_detection_at=0.0)

    bridge.MJPEGNode._on_raw(node, raw_message())

    assert [source for source, _ in node.published] == [node.raw_topic]


def test_annotated_frames_keep_priority_over_the_composited_stream(bridge, monkeypatch):
    """The RTL marker HUD and the evidence frame still arrive as images."""
    import time

    overlay = bridge.OverlayState()
    overlay.update(bridge.parse_detection_summary(encoded()))
    monkeypatch.setattr(bridge, "OVERLAY", overlay)
    node = RawBridge(last_detection_at=time.monotonic())

    bridge.MJPEGNode._on_raw(node, raw_message())

    assert node.published == []


# ---------------------------------------------------------- mission publisher


def test_the_mission_context_publishes_a_decodable_overlay(bridge):
    published = []
    ctx = MissionContext.__new__(MissionContext)
    ctx.boxes_pub = types.SimpleNamespace(publish=published.append)
    ctx._monotonic_to_ros_sec = 1000.0
    ctx.detection_reveal_enabled = True

    sample = PerceptionSample(
        frame=np.zeros((480, 856, 3), dtype=np.uint8),
        result=[Detection()],
        stamp=5.0,
        generation=3,
        inference_sec=0.0814,
    )
    ctx.publish_detection_summary(sample, "STEP 4: NADIR HOVER")

    assert len(published) == 1
    payload = json.loads(published[0].data)
    assert payload["stamp"] == pytest.approx(1005.0)
    summary = bridge.parse_detection_summary(published[0].data)
    assert summary.status == "STEP 4: NADIR HOVER"
    assert summary.detections[0].bbox == (400.0, 220.0, 456.0, 260.0)


def test_an_overlay_failure_never_reaches_the_worker():
    ctx = MissionContext.__new__(MissionContext)

    def explode(_message):
        raise RuntimeError("publisher gone")

    ctx.boxes_pub = types.SimpleNamespace(publish=explode)
    ctx._monotonic_to_ros_sec = 0.0
    ctx.detection_reveal_enabled = True
    sample = PerceptionSample(
        frame=np.zeros((480, 856, 3), dtype=np.uint8), result=[], stamp=0.0, generation=1
    )
    ctx.publish_detection_summary(sample, "")
