"""Victim assessment of the Stage 4 inspection frame.

The forensic report the station reads out after touchdown carries one line on
the victim. That line is only as honest as the inference behind it, so this
module reduces one detector verdict on the inspection frame to a tri-state the
report can rely on:

* ``detected``: inference completed and at least one ``person`` cleared both the
  confidence threshold and the minimum box area;
* ``not_detected``: inference completed and no ``person`` cleared them;
* ``unknown``: inference did not complete (detector absent, detector error,
  timeout). Nothing is said about the victim in that case.

With ``vision.victim_detection_enabled`` off no inference runs and every capture
settles through :func:`disabled` as a completed assessment without a victim,
``reason`` :data:`REASON_VICTIM_DETECTION_DISABLED`: the report then always
takes the ``not_detected`` branch.

The tri-state is derived from :attr:`InspectionAssessment.status` first and from
the detections second: a payload marked ``unavailable`` never reads as a
detection, whatever its ``victim_detected`` field says. The absence of a
``person`` is not proof of the absence of a victim -- YOLOv8n trained on COCO is
weak on a person lying down seen from above -- which is why the report words the
negative branch as "no visible victim".

Nectar SDK review, performed before this module was written:
``DetectionResult.filter_by_class`` (``nectar/ai/detection/core/types.py``) is
the class filter used here; ``Detection.xyxy`` and ``Detection.confidence`` are
the fields read. The SDK has no notion of an assessment or of a victim, so the
reduction itself is mission-side.

Pure logic: no ROS, no clock. Timestamps are passed in by the caller.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Final, Mapping, Optional, Sequence, Tuple

#: Wire schema of an assessment payload: sidecar key ``inspection``, milestone
#: ``mission.inspection_ready`` and ``/bebop/inspection/assessment``. Mirrored by
#: ``InspectionAssessment`` in ``bebop_mission_control/src/types/bmg.ts``.
ASSESSMENT_SCHEMA: Final[str] = "bmg.inspection.v1"

#: COCO class name the victim is inferred from.
VICTIM_CLASS: Final[str] = "person"

#: Minimum detector confidence of a victim, dimensionless in [0, 1].
DEFAULT_VICTIM_CONF: Final[float] = 0.35

#: Minimum victim box area as a fraction of the frame area, dimensionless.
#: 0.0005 of the 856x480 stream frame is about 205 px^2, a 14x14 px box: below
#: it the box is sensor noise or a far bystander, not the inspected scene.
DEFAULT_MIN_BOX_AREA_FRAC: Final[float] = 0.0005

#: Largest number of victim boxes a payload carries, highest confidence first.
MAX_VICTIM_BOXES: Final[int] = 8

#: ``reason`` of an assessment whose detector could not be built or loaded.
REASON_DETECTOR_UNAVAILABLE: Final[str] = "detector_unavailable"
#: ``reason`` of an assessment whose detector raised during inference.
REASON_DETECTOR_ERROR: Final[str] = "detector_error"
#: ``reason`` of an assessment that was still pending when the wait ran out.
REASON_TIMEOUT: Final[str] = "timeout"
#: ``reason`` of a completed assessment settled without inference because
#: ``vision.victim_detection_enabled`` is off; it always reads ``not_detected``.
REASON_VICTIM_DETECTION_DISABLED: Final[str] = "victim_detection_disabled"

BoxXYXY = Tuple[int, int, int, int]


class AssessmentStatus(str, Enum):
    """Whether the inference on the inspection frame completed."""

    COMPLETE = "complete"
    UNAVAILABLE = "unavailable"


class VictimStatus(str, Enum):
    """What the report may state about the victim."""

    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


def _require_str(name: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _require_int(name: str, value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _require_real(name: str, value: Any, *, low: float, high: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} must be finite and within [{low}, {high}], got {value!r}")
    return number


def _frame_size(frame_shape: Sequence[int]) -> Tuple[int, int]:
    """``(width, height)`` from a NumPy ``(height, width[, channels])`` shape."""
    if isinstance(frame_shape, (str, bytes)) or not isinstance(frame_shape, Sequence):
        raise TypeError(f"frame_shape must be a sequence, got {type(frame_shape).__name__}")
    if len(frame_shape) < 2:
        raise ValueError(f"frame_shape must hold height and width, got {tuple(frame_shape)!r}")
    height = _require_int("frame height", frame_shape[0], minimum=1)
    width = _require_int("frame width", frame_shape[1], minimum=1)
    return width, height


def _box(value: Any) -> BoxXYXY:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 4:
        raise TypeError(f"a victim box must be four integers [x1, y1, x2, y2], got {value!r}")
    x1, y1, x2, y2 = (_require_int("victim box coordinate", item) for item in value)
    return (x1, y1, x2, y2)


@dataclass(frozen=True)
class InspectionAssessment:
    """Verdict of the victim inference on one inspection frame.

    Attributes
    ----------
    capture_stamp : str
        ``%Y%m%d_%H%M%S`` timestamp of the capture, the one shared by
        ``accident_raw_<ts>.png`` and its sidecar. The station matches the
        assessment to the capture by it.
    status : AssessmentStatus
        Whether the inference completed.
    reason : Optional[str]
        Why the inference is unavailable; ``None`` when it completed.
    victim_detected : bool
        At least one victim box cleared the thresholds. Never read on its own:
        :attr:`victim_status` is what decides the report text.
    victim_count : int
        Victim boxes that cleared the thresholds, before the
        :data:`MAX_VICTIM_BOXES` cap.
    victim_confidence : Optional[float]
        Highest victim confidence, or ``None`` without a victim.
    victim_boxes : Tuple[Tuple[int, int, int, int], ...]
        ``[x1, y1, x2, y2]`` pixel boxes, highest confidence first, at most
        :data:`MAX_VICTIM_BOXES`.
    frame : Tuple[int, int]
        ``(width, height)`` of the frame the inference ran on, in pixels.
    inference_ms : float
        Wall time of the detector call, in milliseconds.
    model : str
        Weights the detector ran.
    imgsz : Optional[int]
        Inference input size in pixels, ``None`` for the model's native size.
    conf : float
        Victim confidence threshold applied, dimensionless.
    completed_at_utc : str
        ISO 8601 UTC instant at which the assessment was settled.

    Raises
    ------
    TypeError, ValueError
        On construction, if a field has the wrong type or an invalid value.
    """

    capture_stamp: str
    status: AssessmentStatus
    reason: Optional[str]
    victim_detected: bool
    victim_count: int
    victim_confidence: Optional[float]
    victim_boxes: Tuple[BoxXYXY, ...]
    frame: Tuple[int, int]
    inference_ms: float
    model: str
    imgsz: Optional[int]
    conf: float
    completed_at_utc: str

    def __post_init__(self) -> None:
        _require_str("capture_stamp", self.capture_stamp)
        if isinstance(self.status, str) and not isinstance(self.status, AssessmentStatus):
            try:
                object.__setattr__(self, "status", AssessmentStatus(self.status))
            except ValueError as exc:
                raise ValueError(f"unknown assessment status {self.status!r}") from exc
        if not isinstance(self.status, AssessmentStatus):
            raise TypeError(f"status must be an AssessmentStatus, got {type(self.status).__name__}")
        if self.reason is not None:
            _require_str("reason", self.reason)
        if self.status is AssessmentStatus.UNAVAILABLE and self.reason is None:
            raise ValueError("an unavailable assessment must carry a reason")
        if not isinstance(self.victim_detected, bool):
            raise TypeError(f"victim_detected must be a bool, got {type(self.victim_detected).__name__}")
        _require_int("victim_count", self.victim_count)
        if self.victim_confidence is not None:
            _require_real("victim_confidence", self.victim_confidence, low=0.0, high=1.0)
        if not isinstance(self.victim_boxes, tuple):
            raise TypeError(f"victim_boxes must be a tuple, got {type(self.victim_boxes).__name__}")
        if len(self.victim_boxes) > MAX_VICTIM_BOXES:
            raise ValueError(f"at most {MAX_VICTIM_BOXES} victim boxes, got {len(self.victim_boxes)}")
        object.__setattr__(self, "victim_boxes", tuple(_box(box) for box in self.victim_boxes))
        if self.victim_count < len(self.victim_boxes):
            raise ValueError(f"victim_count {self.victim_count} is below the {len(self.victim_boxes)} boxes carried")
        if not isinstance(self.frame, tuple) or len(self.frame) != 2:
            raise TypeError(f"frame must be a (width, height) tuple, got {self.frame!r}")
        _require_int("frame width", self.frame[0], minimum=1)
        _require_int("frame height", self.frame[1], minimum=1)
        _require_real("inference_ms", self.inference_ms, low=0.0)
        _require_str("model", self.model, allow_empty=True)
        if self.imgsz is not None:
            _require_int("imgsz", self.imgsz, minimum=1)
        _require_real("conf", self.conf, low=0.0, high=1.0)
        _require_str("completed_at_utc", self.completed_at_utc)

    @property
    def victim_status(self) -> VictimStatus:
        """What the report may state: ``unknown`` whenever the inference is unavailable."""
        if self.status is not AssessmentStatus.COMPLETE:
            return VictimStatus.UNKNOWN
        return VictimStatus.DETECTED if self.victim_count > 0 else VictimStatus.NOT_DETECTED

    def to_payload(self) -> Dict[str, Any]:
        """Compact JSON-compatible mapping, ``schema`` included.

        Returns
        -------
        Dict[str, Any]
            Plain ``dict``/``list``/scalar tree. ``victim_status`` is included
            for readers of the sidecar; :meth:`from_payload` ignores it and
            derives the status again.
        """
        return {
            "schema": ASSESSMENT_SCHEMA,
            "capture_stamp": self.capture_stamp,
            "status": self.status.value,
            "reason": self.reason,
            "victim_status": self.victim_status.value,
            "victim_detected": self.victim_detected,
            "victim_count": self.victim_count,
            "victim_confidence": (
                None if self.victim_confidence is None else round(self.victim_confidence, 4)
            ),
            "victim_boxes": [list(box) for box in self.victim_boxes],
            "frame": [self.frame[0], self.frame[1]],
            "inference_ms": round(self.inference_ms, 1),
            "model": self.model,
            "imgsz": self.imgsz,
            "conf": round(self.conf, 4),
            "completed_at_utc": self.completed_at_utc,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "InspectionAssessment":
        """Rebuild an assessment from :meth:`to_payload` output.

        Parameters
        ----------
        payload : Mapping[str, Any]
            Decoded JSON object.

        Returns
        -------
        InspectionAssessment

        Raises
        ------
        TypeError
            If ``payload`` is not a mapping or a field has the wrong type.
        ValueError
            On an unknown schema, a missing field or an invalid value.
        """
        if not isinstance(payload, Mapping):
            raise TypeError(f"payload must be a mapping, got {type(payload).__name__}")
        schema = payload.get("schema")
        if schema != ASSESSMENT_SCHEMA:
            raise ValueError(f"unknown assessment schema {schema!r}; expected {ASSESSMENT_SCHEMA!r}")
        required = (
            "capture_stamp",
            "status",
            "reason",
            "victim_detected",
            "victim_count",
            "victim_confidence",
            "victim_boxes",
            "frame",
            "inference_ms",
            "model",
            "imgsz",
            "conf",
            "completed_at_utc",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"assessment payload is missing {missing}")
        boxes = payload["victim_boxes"]
        if isinstance(boxes, (str, bytes)) or not isinstance(boxes, Sequence):
            raise TypeError(f"victim_boxes must be a list, got {type(boxes).__name__}")
        frame = payload["frame"]
        if isinstance(frame, (str, bytes)) or not isinstance(frame, Sequence) or len(frame) != 2:
            raise TypeError(f"frame must be [width, height], got {frame!r}")
        return cls(
            capture_stamp=payload["capture_stamp"],
            status=_require_str("status", payload["status"]),  # type: ignore[arg-type]
            reason=payload["reason"],
            victim_detected=payload["victim_detected"],
            victim_count=payload["victim_count"],
            victim_confidence=payload["victim_confidence"],
            victim_boxes=tuple(_box(box) for box in boxes),
            frame=(frame[0], frame[1]),
            inference_ms=payload["inference_ms"],
            model=payload["model"],
            imgsz=payload["imgsz"],
            conf=payload["conf"],
            completed_at_utc=payload["completed_at_utc"],
        )


def assess_detections(
    result: Any,
    frame_shape: Sequence[int],
    *,
    capture_stamp: str,
    completed_at_utc: str,
    inference_ms: float = 0.0,
    model: str = "",
    imgsz: Optional[int] = None,
    conf_threshold: float = DEFAULT_VICTIM_CONF,
    min_box_area_frac: float = DEFAULT_MIN_BOX_AREA_FRAC,
) -> InspectionAssessment:
    """Reduce a completed detector verdict to a victim assessment.

    A victim is a :data:`VICTIM_CLASS` detection whose confidence is at least
    ``conf_threshold`` and whose box covers at least ``min_box_area_frac`` of
    the frame. An empty result is a completed inference without a victim.

    Parameters
    ----------
    result : DetectionResult
        SDK result of the detector call on the inspection frame.
    frame_shape : Sequence[int]
        NumPy shape of that frame, ``(height, width[, channels])``.
    capture_stamp : str
        Capture timestamp the assessment belongs to.
    completed_at_utc : str
        ISO 8601 UTC instant at which inference returned.
    inference_ms : float
        Wall time of the detector call, in milliseconds.
    model : str
        Weights the detector ran.
    imgsz : Optional[int]
        Inference input size, ``None`` for native.
    conf_threshold : float
        Minimum victim confidence in [0, 1].
    min_box_area_frac : float
        Minimum box area as a fraction of the frame area, in [0, 1).

    Returns
    -------
    InspectionAssessment
        ``status`` is always :attr:`AssessmentStatus.COMPLETE`.

    Raises
    ------
    TypeError
        If ``result`` has no ``filter_by_class`` or an argument has the wrong
        type.
    ValueError
        If a threshold or the frame shape is out of range.
    """
    width, height = _frame_size(frame_shape)
    threshold = _require_real("conf_threshold", conf_threshold, low=0.0, high=1.0)
    area_frac = _require_real("min_box_area_frac", min_box_area_frac, low=0.0, high=1.0)
    if area_frac >= 1.0:
        raise ValueError("min_box_area_frac must be below 1")
    filter_by_class = getattr(result, "filter_by_class", None)
    if not callable(filter_by_class):
        raise TypeError(f"result must be a DetectionResult, got {type(result).__name__}")

    min_area_px = area_frac * width * height
    victims = []
    for detection in filter_by_class([VICTIM_CLASS]):
        confidence = float(detection.confidence)
        if not math.isfinite(confidence) or confidence < threshold:
            continue
        x1, y1, x2, y2 = (float(value) for value in detection.xyxy)
        area_px = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area_px < min_area_px:
            continue
        victims.append((min(confidence, 1.0), (int(max(0.0, x1)), int(max(0.0, y1)), int(max(0.0, x2)), int(max(0.0, y2)))))
    victims.sort(key=lambda item: item[0], reverse=True)

    return InspectionAssessment(
        capture_stamp=capture_stamp,
        status=AssessmentStatus.COMPLETE,
        reason=None,
        victim_detected=bool(victims),
        victim_count=len(victims),
        victim_confidence=victims[0][0] if victims else None,
        victim_boxes=tuple(box for _, box in victims[:MAX_VICTIM_BOXES]),
        frame=(width, height),
        inference_ms=inference_ms,
        model=model,
        imgsz=imgsz,
        conf=threshold,
        completed_at_utc=completed_at_utc,
    )


def unavailable(
    capture_stamp: str,
    reason: str,
    frame_shape: Sequence[int],
    *,
    completed_at_utc: str,
    inference_ms: float = 0.0,
    model: str = "",
    imgsz: Optional[int] = None,
    conf: float = DEFAULT_VICTIM_CONF,
) -> InspectionAssessment:
    """Assessment of an inference that did not complete.

    Carries no detection: ``victim_detected`` is ``False``, the count zero and
    the box list empty, and :attr:`InspectionAssessment.victim_status` is
    ``unknown``.

    Parameters
    ----------
    capture_stamp : str
        Capture timestamp the assessment belongs to.
    reason : str
        One of :data:`REASON_DETECTOR_UNAVAILABLE`,
        :data:`REASON_DETECTOR_ERROR`, :data:`REASON_TIMEOUT`, or another
        short machine-readable cause.
    frame_shape : Sequence[int]
        NumPy shape of the inspection frame, ``(height, width[, channels])``.
    completed_at_utc : str
        ISO 8601 UTC instant at which the outcome was settled.
    inference_ms : float
        Time spent before the failure, in milliseconds.
    model, imgsz, conf
        As in :func:`assess_detections`.

    Returns
    -------
    InspectionAssessment

    Raises
    ------
    TypeError, ValueError
        On an invalid argument.
    """
    width, height = _frame_size(frame_shape)
    return InspectionAssessment(
        capture_stamp=capture_stamp,
        status=AssessmentStatus.UNAVAILABLE,
        reason=_require_str("reason", reason),
        victim_detected=False,
        victim_count=0,
        victim_confidence=None,
        victim_boxes=(),
        frame=(width, height),
        inference_ms=inference_ms,
        model=model,
        imgsz=imgsz,
        conf=conf,
        completed_at_utc=completed_at_utc,
    )


def disabled(
    capture_stamp: str,
    frame_shape: Sequence[int],
    *,
    completed_at_utc: str,
    model: str = "",
    imgsz: Optional[int] = None,
    conf: float = DEFAULT_VICTIM_CONF,
) -> InspectionAssessment:
    """Assessment of a capture flown with victim detection off.

    Completed by construction and without a victim, so
    :attr:`InspectionAssessment.victim_status` is ``not_detected`` whatever the
    frame shows; ``reason`` is :data:`REASON_VICTIM_DETECTION_DISABLED` so the
    sidecar records that no inference was run.

    Parameters
    ----------
    capture_stamp : str
        Capture timestamp the assessment belongs to.
    frame_shape : Sequence[int]
        NumPy shape of the inspection frame, ``(height, width[, channels])``.
    completed_at_utc : str
        ISO 8601 UTC instant at which the outcome was settled.
    model, imgsz, conf
        As in :func:`assess_detections`.

    Returns
    -------
    InspectionAssessment

    Raises
    ------
    TypeError, ValueError
        On an invalid argument.
    """
    width, height = _frame_size(frame_shape)
    return InspectionAssessment(
        capture_stamp=capture_stamp,
        status=AssessmentStatus.COMPLETE,
        reason=REASON_VICTIM_DETECTION_DISABLED,
        victim_detected=False,
        victim_count=0,
        victim_confidence=None,
        victim_boxes=(),
        frame=(width, height),
        inference_ms=0.0,
        model=model,
        imgsz=imgsz,
        conf=conf,
        completed_at_utc=completed_at_utc,
    )
