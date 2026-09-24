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


def encode_detection_summary(
    *,
    detections: Sequence[Mapping[str, Any]],
    frame_width: int,
    frame_height: int,
    status: str,
    stamp_sec: float,
    inference_ms: float,
) -> str:
    """Serialize one inference result to the ``bmg.detections.v1`` JSON payload.

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

    Returns
    -------
    str
        Compact JSON text.

    Raises
    ------
    ValueError
        If the frame geometry is not strictly positive, or a timing value is
        not finite.
    """
    width, height = int(frame_width), int(frame_height)
    if width <= 0 or height <= 0:
        raise ValueError(f"frame geometry must be positive, got {width}x{height}")
    if not math.isfinite(float(stamp_sec)) or not math.isfinite(float(inference_ms)):
        raise ValueError("stamp_sec and inference_ms must be finite")

    payload: Dict[str, Any] = {
        "schema": DETECTION_SUMMARY_SCHEMA,
        "stamp": round(float(stamp_sec), 6),
        "frame": {"width": width, "height": height},
        "status": str(status),
        "inference_ms": round(float(inference_ms), 1),
        "detections": [dict(detection) for detection in detections],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

