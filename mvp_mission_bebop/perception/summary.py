"""Wire format for the lightweight detection overlay consumed by the GCS streamer.

The perception worker used to republish every inferred frame as a full
``sensor_msgs/Image`` with the boxes burned in: 856x480x3 bytes, about 1.2 MB,
at the inference rate. The GCS bridge then displayed *that* stream in preference
to the 33 Hz raw feed, so the cockpit video ran at 4-8 Hz whenever the mission
was flying a perception stage.

The worker now publishes only what the detector found, as JSON in a
``std_msgs/String`` on ``NetworkConfig.detection_boxes_topic``, and the bridge
(``bebop_mission_control/streamer/mjpeg_server.py``) draws the newest boxes over
every raw frame it receives. ``std_msgs/String`` is used rather than
``vision_msgs/Detection2DArray`` because ``vision_msgs`` is not installed on the
ground station and the overlay also carries the stage caption, which that
message has no field for.

Schema ``bmg.detections.v1`` -- a hard IPC contract with the bridge, mirrored by
``parse_detection_summary`` there and pinned by ``test_detection_overlay``::

    {
      "schema": "bmg.detections.v1",
      "stamp": 1790261307.12,         # source frame acquisition, ROS time, s
      "frame": {"width": 856, "height": 480},
      "status": "STEP 2: SEARCH (...)",
      "inference_ms": 81.4,
      "detections": [
        {"class_name": "bicycle", "class_id": 1, "confidence": 0.78,
         "bbox_xyxy": [x1, y1, x2, y2], "center_px": [cx, cy], "area_px": 2240}
      ]
    }

Box coordinates are pixels of the frame described by ``frame``. The bridge
rescales them when the raw stream it draws on has a different geometry.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Final, Mapping, Sequence

#: Schema identifier. Bump it on any incompatible change; the bridge ignores
#: messages carrying a schema it does not know rather than misdrawing them.
DETECTION_SUMMARY_SCHEMA: Final[str] = "bmg.detections.v1"

#: ``v1`` plus an optional ``markers`` list: the landing marker's corners and
#: projected pose axes, in pixels of ``frame``, so the return leg's HUD travels
#: as JSON over the raw stream instead of as a re-encoded annotated frame::
#:
#:     "markers": [{"id": 7,
#:                  "corners_px": [[x, y], [x, y], [x, y], [x, y]],
#:                  "axes_px": {"origin": [x, y], "x": [x, y], "y": [x, y], "z": [x, y]}}]
#:
#: ``axes_px`` is optional. A message without markers is encoded as ``v1``.
DETECTION_SUMMARY_SCHEMA_V2: Final[str] = "bmg.detections.v2"

_AXIS_KEYS: Final = ("origin", "x", "y", "z")


def _point(value: Any, label: str) -> list:
    try:
        x, y = (float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not an (x, y) pair: {value!r}") from exc
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"{label} is not finite: {value!r}")
    return [round(x, 1), round(y, 1)]


def _validate_marker(marker: Mapping[str, Any]) -> Dict[str, Any]:
    """Canonical form of one marker entry. Raises ValueError when malformed."""
    if "id" not in marker:
        raise ValueError("marker has no id")
    corners = marker.get("corners_px")
    if not isinstance(corners, (list, tuple)) or len(corners) != 4:
        raise ValueError("marker corners_px must hold exactly four points")
    entry: Dict[str, Any] = {
        "id": int(marker["id"]),
        "corners_px": [_point(corner, "corner") for corner in corners],
    }
    axes = marker.get("axes_px")
    if axes is not None:
        entry["axes_px"] = {key: _point(axes[key], f"axis {key}") for key in _AXIS_KEYS}
    return entry


def encode_detection_summary(
    *,
    detections: Sequence[Mapping[str, Any]],
    frame_width: int,
    frame_height: int,
    status: str,
    stamp_sec: float,
    inference_ms: float,
    markers: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Serialize one inference result to the ``bmg.detections.v1`` JSON payload.

    With ``markers`` the payload is ``bmg.detections.v2`` (see
    :data:`DETECTION_SUMMARY_SCHEMA_V2`).

    Parameters
    ----------
    detections : Sequence[Mapping[str, Any]]
        Detections as produced by ``MissionContext._describe_detections``.
    frame_width, frame_height : int
        Geometry of the frame the detector ran on, in pixels.
    status : str
        Stage caption to draw in the overlay banner.
    stamp_sec : float
        Acquisition time of the source frame, in seconds of ROS time.
    inference_ms : float
        Duration of the detector call, in milliseconds.
    markers : Sequence[Mapping[str, Any]], optional
        Landing-marker geometry to draw; each entry needs ``id`` and four
        ``corners_px``, and may carry ``axes_px``.

    Returns
    -------
    str
        Compact JSON text.

    Raises
    ------
    ValueError
        If the frame geometry is not strictly positive, a timing value is not
        finite, or a marker entry is malformed.
    """
    width, height = int(frame_width), int(frame_height)
    if width <= 0 or height <= 0:
        raise ValueError(f"frame geometry must be positive, got {width}x{height}")
    if not math.isfinite(float(stamp_sec)) or not math.isfinite(float(inference_ms)):
        raise ValueError("stamp_sec and inference_ms must be finite")

    marker_entries = [_validate_marker(marker) for marker in markers]
    payload: Dict[str, Any] = {
        "schema": DETECTION_SUMMARY_SCHEMA_V2 if marker_entries else DETECTION_SUMMARY_SCHEMA,
        "stamp": round(float(stamp_sec), 6),
        "frame": {"width": width, "height": height},
        "status": str(status),
        "inference_ms": round(float(inference_ms), 1),
        "detections": [dict(detection) for detection in detections],
    }
    if marker_entries:
        payload["markers"] = marker_entries
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

