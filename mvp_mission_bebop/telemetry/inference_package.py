"""Inspection package handed to the visual inference agent, one per capture.

Contract: ``docs/PROJECT.md`` of ``agent-tech4ai-agent-drone-inferencia-visual``
(Agent Drone Inferencia Visual v1, Tech4AI API v2), sections 3 and 9.1.

* One session per occurrence: ``open_conversation`` carries
  ``data.occurrence_id`` (the agent's ``external_identifier_key``).
* One message of ``type: image`` (JPEG or PNG) whose caption is a JSON object
  with exactly four fields: ``occurrence_id``, ``latitude``, ``longitude`` and
  ``timestamp_utc``.
* The agent discards an invalid value and never corrects it, so a field this
  side cannot vouch for is left out of the caption and listed in ``pending``
  with the agent's own vocabulary (``campo``/``motivo``, ``ausente`` or
  ``invalido``) rather than sent as a placeholder. ``latitude`` and
  ``longitude`` travel together: the agent drops both when either is pending.
* The report comes back as ``relatorio_<occurrence_id>.html``, or
  ``relatorio_inspecao_sem_identificador.html`` without a valid identifier.
* ``latitude``/``longitude`` are the fixed site of the demonstration, the
  tactical map's anchor (``MAP_ANCHOR`` in
  ``bebop_mission_control/src/lib/siteAnchor.ts``, mirroring
  ``config/site-anchor.json``). Everything else is produced per capture.

Every function here is pure: the instant and the image path are inputs, so a
package can be rebuilt and checked byte for byte in a test.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Final, List, Optional, Pattern, Tuple

SCHEMA: Final[str] = "t4h.drone-inspection/1"

#: Site of the demonstration, WGS-84 degrees. Mirrors ``MAP_ANCHOR`` in
#: ``bebop_mission_control/src/lib/siteAnchor.ts``; ``test_forensic_telemetry``
#: holds the two equal to the digit.
SITE_NAME: Final[str] = "Transamerica Expo Center"
SITE_LATITUDE: Final[float] = -23.6488913
SITE_LONGITUDE: Final[float] = -46.7187124

#: ``OC_YYYYMMDD_HHMMSS`` in UTC, the identifier the drone generates when the
#: flight was launched without a Central call.
DRONE_OCCURRENCE_ID_PATTERN: Final[Pattern[str]] = re.compile(r"^OC_\d{8}_\d{6}$")
#: The agent's provisional acceptance for a Central identifier.
CENTRAL_OCCURRENCE_ID_PATTERN: Final[Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: ISO 8601 with the ``Z`` designator, whole seconds.
UTC_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"

CAPTION_FIELDS: Final[Tuple[str, ...]] = ("occurrence_id", "latitude", "longitude", "timestamp_utc")

IMAGE_MIME_TYPES: Final[Dict[str, str]] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

REPORT_WITHOUT_IDENTIFIER: Final[str] = "relatorio_inspecao_sem_identificador.html"

PENDING_ABSENT: Final[str] = "ausente"
PENDING_INVALID: Final[str] = "invalido"


def _require_aware(instant: datetime) -> datetime:
    if not isinstance(instant, datetime):
        raise TypeError(f"Expected datetime, got {type(instant).__name__}.")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Capture instant must be timezone-aware.")
    return instant.astimezone(timezone.utc)


def format_utc(instant: datetime) -> str:
    """``instant`` as ``YYYY-MM-DDTHH:MM:SSZ``.

    Raises
    ------
    TypeError
        When ``instant`` is not a ``datetime``.
    ValueError
        When ``instant`` is naive: a local wall clock is never published as UTC.
    """
    return _require_aware(instant).strftime(UTC_FORMAT)


def drone_occurrence_id(instant: datetime) -> str:
    """The drone-generated occurrence id for a capture at ``instant``.

    Raises
    ------
    TypeError, ValueError
        As :func:`format_utc`.
    """
    return f"OC_{_require_aware(instant).strftime('%Y%m%d_%H%M%S')}"


def is_valid_occurrence_id(value: object) -> bool:
    """Whether the agent accepts ``value`` as an occurrence id."""
    return isinstance(value, str) and bool(
        DRONE_OCCURRENCE_ID_PATTERN.match(value) or CENTRAL_OCCURRENCE_ID_PATTERN.match(value)
    )


def report_filename(occurrence_id: Optional[str]) -> str:
    """Name of the report the agent emits for ``occurrence_id``."""
    if is_valid_occurrence_id(occurrence_id):
        return f"relatorio_{occurrence_id}.html"
    return REPORT_WITHOUT_IDENTIFIER


def build_telemetry_caption(
    occurrence_id: Optional[str],
    captured_at: Optional[datetime],
) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """The caption object and the fields it could not carry.

    Parameters
    ----------
    occurrence_id : str, optional
        Identifier of the occurrence.
    captured_at : datetime, optional
        Timezone-aware capture instant.

    Returns
    -------
    caption : dict
        Only the valid fields, in :data:`CAPTION_FIELDS` order.
    pending : list of dict
        ``{"campo": ..., "motivo": "ausente" | "invalido"}`` per missing field.
    """
    caption: Dict[str, Any] = {}
    pending: List[Dict[str, str]] = []

    if occurrence_id is None:
        pending.append({"campo": "occurrence_id", "motivo": PENDING_ABSENT})
    elif is_valid_occurrence_id(occurrence_id):
        caption["occurrence_id"] = occurrence_id
    else:
        pending.append({"campo": "occurrence_id", "motivo": PENDING_INVALID})

    caption["latitude"] = SITE_LATITUDE
    caption["longitude"] = SITE_LONGITUDE

    if captured_at is None:
        pending.append({"campo": "timestamp_utc", "motivo": PENDING_ABSENT})
    else:
        try:
            caption["timestamp_utc"] = format_utc(captured_at)
        except (TypeError, ValueError):
            pending.append({"campo": "timestamp_utc", "motivo": PENDING_INVALID})

    return caption, pending


def select_inspection_image(
    raw_path: Optional[str], annotated_path: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The frame the agent analyses, or ``None`` when neither reached disk.

    The original is preferred: the annotated copy carries the detector's boxes,
    and a visual model shown them assesses the detector rather than the scene.
    The annotated copy is used only when the original was lost, and is marked.
    """
    for path, overlay in ((raw_path, False), (annotated_path, True)):
        if not path:
            continue
        extension = os.path.splitext(path)[1].lower()
        mime_type = IMAGE_MIME_TYPES.get(extension)
        if mime_type is None:
            continue
        return {
            "filename": os.path.basename(path),
            "mime_type": mime_type,
            "contains_detector_overlay": overlay,
        }
    return None


def build_inference_package(
    occurrence_id: Optional[str],
    captured_at: Optional[datetime],
    raw_path: Optional[str],
    annotated_path: Optional[str],
) -> Dict[str, Any]:
    """Everything the pipeline needs to submit one capture to the agent.

    Parameters
    ----------
    occurrence_id : str, optional
        Identifier of the occurrence.
    captured_at : datetime, optional
        Timezone-aware capture instant.
    raw_path, annotated_path : str, optional
        Evidence files written for the capture.

    Returns
    -------
    dict
        ``open_conversation``, ``message`` (image reference and the caption
        serialised exactly as it is sent), the caption as an object,
        ``pending``, the expected report name and the site the coordinates
        name.
    """
    caption, pending = build_telemetry_caption(occurrence_id, captured_at)
    image = select_inspection_image(raw_path, annotated_path)
    caption_valid_id = caption.get("occurrence_id")

    return {
        "schema": SCHEMA,
        "open_conversation": {
            "data": {"occurrence_id": caption_valid_id} if caption_valid_id else {},
        },
        "message": {
            "type": "image",
            "image": image,
            "caption": json.dumps(caption, ensure_ascii=False, separators=(",", ":")),
        },
        "caption": caption,
        "pending": pending,
        "ready": image is not None,
        "expected_report": report_filename(caption_valid_id),
        "position": {"source": "site", "site": SITE_NAME},
    }
