"""Compressed camera transport into the GCS MJPEG bridge (4.2).

The driver's ``image_transport`` camera publisher also offers
``/bebop/camera/image_raw/compressed``. The bridge prefers it: a JPEG passes
straight through to the cockpit when there is nothing to draw, instead of
2.76 MB of raw BGR crossing DDS to be decoded and re-encoded.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types

import cv2
import numpy as np
import pytest

from mvp_mission_bebop.perception.summary import encode_detection_summary

_BRIDGE = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control", "streamer", "mjpeg_server.py")


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("mjpeg_server_compressed", _BRIDGE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def jpeg(width=856, height=480, colour=(40, 90, 160)):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = colour
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return data.tobytes()


class Recorder:
    def __init__(self):
        self.frames = []

    def publish(self, data, source, size):
        self.frames.append((data, source, size))


class CompressedBridge:
    """The state the compressed callback reads, without a ROS node."""

    raw_topic = "/bebop/camera/image_raw"
    compressed_topic = "/bebop/camera/image_raw/compressed"
    boxes_topic = "/bebop/camera/detection_boxes"

    def __init__(self, bridge):
        self._last_detection_at = 0.0
        self._last_compressed_at = 0.0
        self._announced = set()
        self._bridge = bridge
        self.logged = []

    def get_logger(self):
        return types.SimpleNamespace(info=self.logged.append, warning=self.logged.append)


def message(data, fmt="bgr8; jpeg compressed bgr8"):
    return types.SimpleNamespace(data=data, format=fmt)


def test_the_jpeg_frame_size_is_read_from_its_header(bridge):
    assert bridge.jpeg_size(jpeg(856, 480)) == (856, 480)
    assert bridge.jpeg_size(jpeg(1280, 720)) == (1280, 720)
    assert bridge.jpeg_size(b"not a jpeg") is None


def test_without_an_overlay_the_jpeg_passes_through_unchanged(bridge, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(bridge, "BUFFER", recorder)
    monkeypatch.setattr(bridge, "OVERLAY", bridge.OverlayState())
    node = CompressedBridge(bridge)
    data = jpeg()

    bridge.MJPEGNode._on_compressed(node, message(data))

    assert recorder.frames == [(data, node.compressed_topic, (856, 480))]
    assert node._last_compressed_at > 0.0


def test_with_a_fresh_overlay_the_frame_is_drawn_and_reencoded(bridge, monkeypatch):
    recorder = Recorder()
    overlay = bridge.OverlayState()
    overlay.update(
        bridge.parse_detection_summary(
            encode_detection_summary(
                detections=[{"class_name": "bicycle", "confidence": 0.9, "bbox_xyxy": [400, 220, 456, 260]}],
                frame_width=856, frame_height=480, status="STEP 2", stamp_sec=1.0, inference_ms=40.0,
            )
        )
    )
    monkeypatch.setattr(bridge, "BUFFER", recorder)
    monkeypatch.setattr(bridge, "OVERLAY", overlay)
    node = CompressedBridge(bridge)
    data = jpeg()

    bridge.MJPEGNode._on_compressed(node, message(data))

    [(encoded, source, size)] = recorder.frames
    assert source == node.boxes_topic and size == (856, 480)
    assert encoded != data
    assert cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR) is not None


def test_a_non_jpeg_compressed_frame_is_reencoded(bridge, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(bridge, "BUFFER", recorder)
    monkeypatch.setattr(bridge, "OVERLAY", bridge.OverlayState())
    frame = np.zeros((480, 856, 3), dtype=np.uint8)
    ok, png = cv2.imencode(".png", frame)
    node = CompressedBridge(bridge)

    bridge.MJPEGNode._on_compressed(node, message(png.tobytes(), fmt="bgr8; png compressed bgr8"))

    [(encoded, _source, size)] = recorder.frames
    assert size == (856, 480) and encoded[:2] == b"\xff\xd8"


def test_raw_frames_are_ignored_while_the_compressed_stream_is_live(bridge, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(bridge, "BUFFER", recorder)
    node = CompressedBridge(bridge)
    node._last_compressed_at = time.monotonic()
    node._encode = lambda *_a: recorder.frames.append("raw")

    bridge.MJPEGNode._on_raw(node, types.SimpleNamespace(encoding="bgr8"))

    assert recorder.frames == []


def test_the_raw_stream_takes_over_when_the_compressed_one_goes_quiet(bridge, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(bridge, "BUFFER", recorder)
    monkeypatch.setattr(bridge, "OVERLAY", bridge.OverlayState())
    node = CompressedBridge(bridge)
    node._last_compressed_at = time.monotonic() - 5.0
    node._encode = lambda *_a: recorder.frames.append("raw")

    bridge.MJPEGNode._on_raw(node, types.SimpleNamespace(encoding="bgr8"))

    assert recorder.frames == ["raw"]


def test_each_stream_frame_is_one_write_on_a_nodelay_socket(bridge, monkeypatch):
    frames = iter([(b"\xff\xd8jpegdata", 1)])

    class Buffer:
        def wait_for(self, generation, timeout):
            try:
                return next(frames)
            except StopIteration:
                raise BrokenPipeError()

    writes = []
    options = []
    handler = bridge.StreamHandler.__new__(bridge.StreamHandler)
    handler.wfile = types.SimpleNamespace(write=writes.append)
    handler.connection = types.SimpleNamespace(setsockopt=lambda *a: options.append(a))
    handler.send_response = lambda *_a: None
    handler.send_header = lambda *_a: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(bridge, "BUFFER", Buffer())

    handler._serve_stream()

    assert len(writes) == 1 and writes[0].startswith(b"--FRAME\r\n") and writes[0].endswith(b"\r\n")
    import socket

    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in options
