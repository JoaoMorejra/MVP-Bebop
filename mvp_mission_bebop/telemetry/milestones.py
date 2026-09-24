"""Fine-grained mission milestones on stdout, for the GCS narration queue.

The five ``[STEP N: ...]`` markers are coarser than the flight script the
copilot narrates (spec ``2026-09-24-bmg-mission-experience-design.md``, section
4): Stage 2 alone holds both "scan started" and "target found", and Stage 5
holds both "returning" and "landing". Each step therefore also logs one line per
milestone it crosses, on the same stdout pipe the step matcher in
``electron/main.cjs`` already reads::

    [MILESTONE mission.target_found] {"bearing_deg":-4.2,"class_name":"bicycle"}

The key is one of :data:`MILESTONE_KEYS`, taken verbatim from the "pool key"
column of the spec's synchronisation table; the GCS resolves it to a phrase
pool. The payload is always present, always a single-line JSON object (``{}``
when empty), with non-finite floats replaced by ``null`` so ``JSON.parse`` in
the renderer never rejects it. ``mission.start`` and ``mission.countdown_3``
are raised by the Electron process before this one exists and are
deliberately absent here.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Final, Mapping, Optional, Tuple

logger = logging.getLogger("Milestone")

#: Line prefix the GCS matches. Part of the IPC contract with ``main.cjs``.
MILESTONE_TAG: Final[str] = "MILESTONE"

#: Milestones this process emits, in flight order.
MILESTONE_KEYS: Final[Tuple[str, ...]] = (
    "mission.takeoff",
    "mission.scan_start",
    "mission.target_found",
    "mission.approaching",
    "mission.capture_done",
    "mission.rtl_start",
    "mission.landing",
)


def _sanitize(value: Any) -> Any:
    """Recursively replace non-finite floats, which JSON cannot represent."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return value


def encode_milestone(key: str, payload: Optional[Mapping[str, Any]] = None) -> str:
    """Render one milestone as the log message the GCS matches.

    Parameters
    ----------
    key : str
        Milestone key, one of :data:`MILESTONE_KEYS`.
    payload : Optional[Mapping[str, Any]]
        JSON-serializable detail for the phrase template (for example the
        configured altitude for ``mission.takeoff``). ``None`` encodes as
        ``{}``.

    Returns
    -------
    str
        ``[MILESTONE <key>] <json>``, on a single line.

    Raises
    ------
    TypeError
        If ``key`` is not a string or ``payload`` is not a mapping.
    ValueError
        If ``key`` is not a known milestone, or ``payload`` holds a value
        JSON cannot serialize.
    """
    if not isinstance(key, str):
        raise TypeError(f"milestone key must be a string, got {type(key).__name__}")
    if key not in MILESTONE_KEYS:
        raise ValueError(f"unknown milestone key {key!r}; expected one of {MILESTONE_KEYS}")
    if payload is not None and not isinstance(payload, Mapping):
        raise TypeError(f"milestone payload must be a mapping, got {type(payload).__name__}")

    try:
        body = json.dumps(
            _sanitize(dict(payload or {})),
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"milestone payload for {key!r} is not JSON-serializable: {exc}") from exc
    return f"[{MILESTONE_TAG} {key}] {body}"


def emit_milestone(key: str, payload: Optional[Mapping[str, Any]] = None) -> None:
    """Log one milestone at INFO on the mission's stdout.

    Never raises: narration is never flight-critical, so an encoding fault is
    logged as a warning and the milestone is dropped rather than allowed to
    unwind the step that crossed it.
    """
    try:
        message = encode_milestone(key, payload)
    except (TypeError, ValueError) as exc:
        logger.warning("Milestone dropped: %s", exc)
        return
    logger.info("%s", message)
