#!/usr/bin/env python3
"""
ROS 2 to MJPEG bridge for the cockpit video panel.

Subscribes to the annotated mission stream (`/bebop/camera/detections`) and to
the raw driver feed (`/bebop/camera/image_raw`), republishing whichever is
currently arriving as `multipart/x-mixed-replace` on port 9090.

Two properties the cockpit depends on:

* The HTTP endpoint always answers, even before a single frame has decoded. A
  placeholder is served instead of an empty body, so the `<img>` element stays
  connected and recovers on its own the moment frames start flowing — the panel
  no longer has to gate itself on a non-zero frame rate and get stuck.
* `/status` reports measured facts only: the age of the last frame, the source
  topic, the frame geometry and a rate that decays to zero when the feed stops,
  instead of holding the last value forever.
"""

from __future__ import annotations

import argparse
import errno
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Optional, Tuple

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

DEFAULT_PORT = 9090
DEFAULT_DETECTION_TOPIC = "/bebop/camera/detections"
DEFAULT_RAW_TOPIC = "/bebop/camera/image_raw"

#: How long the annotated stream keeps priority after its last frame.
DETECTION_PRIORITY_SEC = 1.5
#: A feed silent for longer than this is reported as dead.
FRAME_STALE_SEC = 2.0
#: Upper bound on the rate at which frames are pushed to a connected client.
STREAM_MAX_FPS = 30.0
JPEG_QUALITY = 80


class FrameBuffer:
    """Single-slot latest-frame buffer shared by the ROS and HTTP threads.

    A slow HTTP client must never back-pressure the ROS callback, so frames are
    overwritten rather than queued; `generation` lets a streaming client block
    until something new actually exists instead of re-sending the same JPEG.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._generation = 0
        self._total = 0
        self._last_at: Optional[float] = None
        self._source = ""
        self._size: Tuple[int, int] = (0, 0)

        # Rate over a one-second sliding window, recomputed on read so it
        # decays to zero instead of freezing at the last measured value.
        self._window_start = time.monotonic()
        self._window_count = 0
        self._rate = 0.0

    def publish(self, jpeg: bytes, source: str, size: Tuple[int, int]) -> None:
        now = time.monotonic()
        with self._condition:
            self._jpeg = jpeg
            self._generation += 1
            self._total += 1
            self._last_at = now
            self._source = source
            self._size = size

            self._window_count += 1
            elapsed = now - self._window_start
            if elapsed >= 1.0:
                self._rate = self._window_count / elapsed
                self._window_count = 0
                self._window_start = now

            self._condition.notify_all()

    def wait_for(self, generation: int, timeout: float) -> Tuple[Optional[bytes], int]:
        """Return the newest frame once it differs from `generation`."""
        with self._condition:
            if self._generation == generation:
                self._condition.wait(timeout)
            return self._jpeg, self._generation

    def status(self) -> dict:
        now = time.monotonic()
        with self._condition:
            age = None if self._last_at is None else now - self._last_at
            live = age is not None and age < FRAME_STALE_SEC

            # Decay: no frame inside the current window means the rate is zero.
            if not live:
                rate = 0.0
            elif now - self._window_start > 2.0:
                rate = 0.0
            else:
                rate = self._rate

            return {
                "running": True,
                "live": live,
                "frames": self._total,
                "fps": round(rate, 1),
                "age_sec": round(age, 2) if age is not None else None,
                "source": self._source,
                "width": self._size[0],
                "height": self._size[1],
            }


BUFFER = FrameBuffer()


def placeholder_jpeg() -> bytes:
    """A dark 16:9 frame served while no video has arrived.

    Sending valid JPEG rather than nothing keeps the multipart response and the
    browser's decoder in a healthy state, which is what lets the panel recover
    without a manual reconnect.
    """
    canvas = np.zeros((360, 640, 3), dtype=np.uint8)
    canvas[:] = (18, 20, 23)
    cv2.putText(
        canvas,
        "AGUARDANDO VIDEO",
        (150, 190),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (110, 118, 128),
        2,
        cv2.LINE_AA,
    )
    ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return encoded.tobytes() if ok else b""


PLACEHOLDER = b""


class MJPEGNode(Node):
    """Consumes both camera topics and feeds the shared frame buffer."""

    def __init__(self, detection_topic: str, raw_topic: str) -> None:
        super().__init__("bmg_mjpeg_streamer")
        self.bridge = CvBridge()
        self.detection_topic = detection_topic
        self.raw_topic = raw_topic
        self._last_detection_at = 0.0
        self._decode_failures = 0
        #: Sources that have already announced their first good frame, so the
        #: log records the moment video started rather than every frame after.
        self._announced: set = set()

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(Image, detection_topic, self._on_detection, qos)
        self.create_subscription(Image, raw_topic, self._on_raw, qos)

        self.get_logger().info(
            f"MJPEG bridge subscribing to {detection_topic} (primary) "
            f"and {raw_topic} (fallback)"
        )

    def _on_detection(self, msg: Image) -> None:
        self._last_detection_at = time.monotonic()
        self._encode(msg, self.detection_topic)

    def _on_raw(self, msg: Image) -> None:
        # The annotated stream wins while it is alive: those are the boxes the
        # mission actually acted on.
        if time.monotonic() - self._last_detection_at <= DETECTION_PRIORITY_SEC:
            return
        self._encode(msg, self.raw_topic)

    def _decode(self, msg: Image, source: str):
        """Get a BGR array out of the message, by whichever route works.

        ``cv_bridge`` is the right answer and is tried first. It is also the
        route that fails hardest: it raises on an encoding string it does not
        recognise, and it has raised across NumPy and OpenCV version boundaries
        for messages that were perfectly well formed. Every one of those frames
        used to be dropped on the floor, which looks from the cockpit exactly
        like a camera that is not publishing.

        So there are two fallbacks. The first reinterprets the buffer directly,
        which is valid whenever the message really is packed 8-bit colour and
        the stride agrees. The second hands the bytes to ``imdecode``, which
        covers a compressed payload arriving on a topic typed as raw.
        """
        try:
            return self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as error:  # noqa: BLE001 - the fallbacks below are the point
            self._decode_failures += 1
            if self._decode_failures % 30 == 1:
                self.get_logger().warning(
                    f"cv_bridge conversion failed on {source} "
                    f"({self._decode_failures} so far): {error}. Trying raw buffer."
                )

        buffer = np.frombuffer(msg.data, dtype=np.uint8)

        # Straight reinterpretation, when the geometry the header declares
        # matches the bytes that arrived.
        try:
            height, width, step = int(msg.height), int(msg.width), int(msg.step)
            if height and width and buffer.size == height * step:
                channels = step // width if width else 0
                if channels in (1, 3, 4):
                    frame = buffer.reshape((height, width, channels))
                    if channels == 1:
                        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    if channels == 4:
                        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    # RGB and BGR are indistinguishable from the buffer alone;
                    # the encoding string is the only thing that tells them
                    # apart, and it is the field that just failed to parse.
                    if "rgb" in (msg.encoding or "").lower():
                        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    return frame
        except Exception:  # noqa: BLE001
            pass

        # A compressed payload on a topic typed as raw.
        try:
            decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            if decoded is not None and decoded.size:
                return decoded
        except Exception:  # noqa: BLE001
            pass

        return None

    def _encode(self, msg: Image, source: str) -> None:
        frame = self._decode(msg, source)

        if frame is None or frame.size == 0:
            return

        ok, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not ok:
            return

        # Said once per source. "The camera started publishing" is the single
        # most useful line in this log when an operator is staring at a black
        # panel, and it is worth nothing if it scrolls past thirty times a
        # second.
        if source not in self._announced:
            self._announced.add(source)
            self.get_logger().info(
                f"First frame decoded from {source}: "
                f"{int(frame.shape[1])}x{int(frame.shape[0])}, "
                f"encoding={msg.encoding or 'unset'}"
            )

        BUFFER.publish(
            encoded.tobytes(), source, (int(frame.shape[1]), int(frame.shape[0]))
        )


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # Without this a restart within TIME_WAIT fails with EADDRINUSE and the
    # cockpit silently loses video for a minute.
    allow_reuse_address = True


class StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # The GCS appends a cache-busting query when it reconnects a dropped
        # stream; matching on the full path would 404 every reconnect.
        route = self.path.split("?", 1)[0]
        if route == "/stream":
            self._serve_stream()
        elif route == "/status":
            self._serve_status()
        elif route == "/snapshot":
            self._serve_snapshot()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    # -- routes --------------------------------------------------------------

    def _serve_stream(self) -> None:
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        generation = -1
        min_period = 1.0 / STREAM_MAX_FPS
        last_sent = 0.0

        try:
            while True:
                frame, generation = BUFFER.wait_for(generation, timeout=1.0)
                payload = frame if frame is not None else PLACEHOLDER
                if not payload:
                    continue

                now = time.monotonic()
                if now - last_sent < min_period:
                    time.sleep(min_period - (now - last_sent))
                last_sent = time.monotonic()

                header = (
                    b"--FRAME\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n"
                )
                self.wfile.write(header)
                self.wfile.write(payload)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the cockpit navigated away or reloaded
        except OSError:
            pass

    def _serve_status(self) -> None:
        import json

        body = json.dumps(BUFFER.status()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # The GCS page is served from an ephemeral localhost port, so this is a
        # cross-origin request from its point of view.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_snapshot(self) -> None:
        frame, _ = BUFFER.wait_for(-1, timeout=0.0)
        payload = frame if frame is not None else PLACEHOLDER
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return  # suppressed; failures are reported on stderr by the server


def build_server(port: int) -> ThreadedHTTPServer:
    """Bind the HTTP server, failing loudly rather than in a dead thread."""
    try:
        return ThreadedHTTPServer(("127.0.0.1", port), StreamHandler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"[mjpeg] port {port} is already in use — another MJPEG bridge "
                f"is still running. Stop it and retry."
            ) from error
        raise SystemExit(f"[mjpeg] cannot bind 127.0.0.1:{port}: {error}") from error


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROS 2 to MJPEG bridge")
    parser.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    parser.add_argument("--detection-topic", default=DEFAULT_DETECTION_TOPIC)
    parser.add_argument("--raw-topic", default=DEFAULT_RAW_TOPIC)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    global PLACEHOLDER

    args = parse_args(sys.argv[1:] if argv is None else argv)
    PLACEHOLDER = placeholder_jpeg()

    # Bind before touching ROS: a port clash must be reported immediately, not
    # after the node has advertised itself.
    server = build_server(args.port)
    socket.setdefaulttimeout(None)

    rclpy.init()
    node = MJPEGNode(args.detection_topic, args.raw_topic)

    server_thread = threading.Thread(
        target=server.serve_forever, name="mjpeg-http", daemon=True
    )
    server_thread.start()
    print(
        f"[mjpeg] serving http://127.0.0.1:{args.port}/stream",
        file=sys.stderr,
        flush=True,
    )

    def _terminate(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except rclpy.executors.ExternalShutdownException:
        pass
    finally:
        server.shutdown()
        server.server_close()
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
