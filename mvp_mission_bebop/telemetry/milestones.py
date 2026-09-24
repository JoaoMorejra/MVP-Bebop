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

Alerts travel the same way on their own tag::

    [ALERT mission.failsafe] {"priority":"CRITICAL","text":"Alerta de voo: ..."}

When the ground station runs the mission it is the only voice in the system,
so the failures, aborts and failsafe landings this process would otherwise
speak itself are handed to it as alerts instead (see
``announcer.station_narrates``). ``text`` is the sentence to read; the station
speaks it ahead of any queued narration.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Final, Mapping, Optional, Tuple

logger = logging.getLogger("Milestone")

#: Line prefix the GCS matches. Part of the IPC contract with ``main.cjs``.
MILESTONE_TAG: Final[str] = "MILESTONE"

#: Line prefix of an alert. Part of the same contract.
ALERT_TAG: Final[str] = "ALERT"

#: Alerts this process raises. ``mission.alert`` is the catch-all for an
#: urgent announcement no specific key covers.
ALERT_KEYS: Final[Tuple[str, ...]] = (
    "mission.abort",
    "mission.step_failed",
    "mission.failsafe",
    "mission.init_failed",
    "mission.calibration_failed",
    "mission.takeoff_failed",
    "mission.alert",
)

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


def _encode(
    tag: str, keys: Tuple[str, ...], key: str, payload: Optional[Mapping[str, Any]]
) -> str:
    if not isinstance(key, str):
        raise TypeError(f"{tag.lower()} key must be a string, got {type(key).__name__}")
    if key not in keys:
        raise ValueError(f"unknown {tag.lower()} key {key!r}; expected one of {keys}")
    if payload is not None and not isinstance(payload, Mapping):
        raise TypeError(f"{tag.lower()} payload must be a mapping, got {type(payload).__name__}")

    try:
        body = json.dumps(
            _sanitize(dict(payload or {})),
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{tag.lower()} payload for {key!r} is not JSON-serializable: {exc}") from exc
    return f"[{tag} {key}] {body}"


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
    return _encode(MILESTONE_TAG, MILESTONE_KEYS, key, payload)


def encode_alert(key: str, payload: Optional[Mapping[str, Any]] = None) -> str:
    """Render one alert as the log message the GCS matches.

    Same contract as :func:`encode_milestone`, over :data:`ALERT_KEYS` and
    with the ``[ALERT <key>]`` prefix.
    """
    return _encode(ALERT_TAG, ALERT_KEYS, key, payload)


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


def emit_alert(key: str, payload: Optional[Mapping[str, Any]] = None) -> bool:
    """Log one alert at WARNING on the mission's stdout. Never raises.

    Returns
    -------
    bool
        Whether the alert was written; False when it could not be encoded.
    """
    try:
        message = encode_alert(key, payload)
    except (TypeError, ValueError) as exc:
        logger.warning("Alert dropped: %s", exc)
        return False
    logger.warning("%s", message)
    return True
