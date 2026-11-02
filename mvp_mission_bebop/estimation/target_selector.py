"""Persistent target selection with an absolute class priority.

Every stage used to pick ``max(targets, key=confidence)`` afresh on each frame.
Two detections of close confidence then alternate as "the" target from one frame
to the next, and the visual servo chases the difference. The
``ConstantVelocityTracker`` only rejects centroid outliers; it cannot tell a
jump of identity from a jump of position.

:class:`TargetSelector` keeps one track across frames:

1. detections whose class is not in ``vision.selector_class_priority`` are
   discarded; the rank of a detection is the index of the first priority group
   it belongs to (the group ``acidente`` matches every label of
   :data:`~mvp_mission_bebop.perception.classes.ACCIDENT_CLASS_NAMES`);
2. with a track, the candidate of greatest IoU with the last raw box continues
   it (IoU at least ``selector_iou_min``), falling back to the nearest centroid
   inside ``selector_gate_px``;
3. a candidate of strictly better rank than the track is a challenger and takes
   over only after ``selector_switch_frames`` consecutive frames; a frame
   without it restarts the count;
4. a track unmatched for ``selector_lost_frames`` consecutive frames is
   released; while it is merely missing the selector returns ``None`` so the
   caller coasts its own tracker;
5. without a track the best candidate is chosen by rank, then by
   ``confidence * sqrt(area)``, then by the smaller box centre ``x``, so equal
   candidates resolve deterministically;
6. the returned box is an exponential moving average of the track's raw boxes
   with weight ``selector_box_alpha``, restarted on every change of track.

No equivalent exists in the Nectar SDK, which detects but does not associate.
"""

from __future__ import annotations

import copy
import math
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

from mvp_mission_bebop.perception.classes import is_accident_class

Box = Tuple[float, float, float, float]


def _box(detection: Any) -> Optional[Box]:
    try:
        x1, y1, x2, y2 = (float(v) for v in detection.xyxy[:4])
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def box_iou(a: Box, b: Box) -> float:
    """Intersection over union of two ``(x1, y1, x2, y2)`` boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0.0 else 0.0


def _centre(box: Box) -> Tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _centre_distance(a: Box, b: Box) -> float:
    ca, cb = _centre(a), _centre(b)
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1])


class TargetSelector:
    """Frame-to-frame target association with class priority and box smoothing.

    Parameters
    ----------
    vision_cfg : VisionConfig
        Source of ``selector_class_priority``, ``selector_iou_min``,
        ``selector_gate_px``, ``selector_lost_frames``,
        ``selector_switch_frames`` and ``selector_box_alpha``.

    Raises
    ------
    TypeError
        If the priority is not a sequence of strings or a frame count is not an
        integer.
    ValueError
        If the priority is empty, ``selector_iou_min`` is outside ``(0, 1]``,
        ``selector_gate_px`` is not positive, a frame count is below 1 or
        ``selector_box_alpha`` is outside ``(0, 1]``.
    """

    def __init__(self, vision_cfg: Any) -> None:
        priority = getattr(vision_cfg, "selector_class_priority", None)
        if isinstance(priority, str) or not isinstance(priority, (list, tuple)):
            raise TypeError("selector_class_priority must be a list of class names")
        if not priority or not all(isinstance(p, str) and p.strip() for p in priority):
            raise ValueError("selector_class_priority must be a non-empty list of non-empty names")
        self._priority: Tuple[str, ...] = tuple(p.strip().lower() for p in priority)

        iou_min = float(vision_cfg.selector_iou_min)
        if not math.isfinite(iou_min) or not 0.0 < iou_min <= 1.0:
            raise ValueError(f"selector_iou_min must be in (0, 1], got {iou_min!r}")
        gate = float(vision_cfg.selector_gate_px)
        if not math.isfinite(gate) or gate <= 0.0:
            raise ValueError(f"selector_gate_px must be positive, got {gate!r}")
        alpha = float(vision_cfg.selector_box_alpha)
        if not math.isfinite(alpha) or not 0.0 < alpha <= 1.0:
            raise ValueError(f"selector_box_alpha must be in (0, 1], got {alpha!r}")
        counts = []
        for name in ("selector_lost_frames", "selector_switch_frames"):
            value = getattr(vision_cfg, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 1:
                raise ValueError(f"{name} must be >= 1, got {value!r}")
            counts.append(value)

        self._iou_min: float = iou_min
        self._gate_px: float = gate
        self._alpha: float = alpha
        self._lost_frames: int = counts[0]
        self._switch_frames: int = counts[1]

        self.track_id: Optional[int] = None
        self.switches: int = 0
        self._next_id: int = 1
        self._raw: Optional[Box] = None
        self._smoothed: Optional[np.ndarray] = None
        self._rank: int = 0
        self._missing: int = 0
        self._challenger: Optional[Box] = None
        self._challenger_count: int = 0

    def reset(self) -> None:
        """Forget the track and any pending challenger."""
        self.track_id = None
        self._raw = None
        self._smoothed = None
        self._missing = 0
        self._challenger = None
        self._challenger_count = 0

    def rank(self, class_name: str) -> Optional[int]:
        """Priority rank of a class name, or ``None`` when it is not a target."""
        if not isinstance(class_name, str):
            return None
        name = class_name.strip().lower()
        for index, group in enumerate(self._priority):
            if is_accident_class(group):
                if is_accident_class(name):
                    return index
            elif name == group:
                return index
        return None

    @staticmethod
    def _merit(rank: int, detection: Any, box: Box) -> Tuple[float, float, float]:
        area = (box[2] - box[0]) * (box[3] - box[1])
        confidence = float(getattr(detection, "confidence", 0.0) or 0.0)
        return (float(rank), -confidence * math.sqrt(area), _centre(box)[0])

    def _associate(self, reference: Box, candidates: Sequence[Tuple[int, Any, Box]]) -> Optional[int]:
        best_iou, best_index = 0.0, None
        for index, (_, _, box) in enumerate(candidates):
            iou = box_iou(reference, box)
            if iou > best_iou:
                best_iou, best_index = iou, index
        if best_index is not None and best_iou >= self._iou_min:
            return best_index
        best_distance, best_index = math.inf, None
        for index, (_, _, box) in enumerate(candidates):
            distance = _centre_distance(reference, box)
            if distance < best_distance:
                best_distance, best_index = distance, index
        if best_index is not None and best_distance <= self._gate_px:
            return best_index
        return None

    def _start(self, rank: int, box: Box) -> None:
        self.track_id = self._next_id
        self._next_id += 1
        self._raw = box
        self._smoothed = np.array(box, dtype=float)
        self._rank = rank
        self._missing = 0
        self._challenger = None
        self._challenger_count = 0

    def _emit(self, detection: Any) -> Any:
        out = copy.copy(detection)
        assert self._smoothed is not None
        out.xyxy = self._smoothed.copy()
        return out

    def select(self, detections: Sequence[Any]) -> Optional[Any]:
        """Advance one frame and return the tracked target, smoothed.

        Parameters
        ----------
        detections : Sequence[Detection]
            Every detection of the frame, any class.

        Returns
        -------
        Optional[Detection]
            A copy of the tracked detection with the same class, id and
            confidence and a smoothed ``xyxy``; ``None`` when no target class
            is present or the track is momentarily missing.
        """
        candidates: List[Tuple[int, Any, Box]] = []
        for detection in detections or ():
            rank = self.rank(getattr(detection, "class_name", ""))
            box = _box(detection)
            if rank is not None and box is not None:
                candidates.append((rank, detection, box))

        if self.track_id is not None and self._raw is not None:
            matched = self._associate(self._raw, candidates)
            others = [c for i, c in enumerate(candidates) if i != matched]
            challengers = [c for c in others if c[0] < self._rank]
            if challengers:
                best = min(challengers, key=lambda c: self._merit(c[0], c[1], c[2]))
                same = self._challenger is not None and (
                    box_iou(self._challenger, best[2]) >= self._iou_min
                    or _centre_distance(self._challenger, best[2]) <= self._gate_px
                )
                self._challenger_count = self._challenger_count + 1 if same else 1
                self._challenger = best[2]
                if self._challenger_count >= self._switch_frames:
                    self.switches += 1
                    self._start(best[0], best[2])
                    return self._emit(best[1])
            else:
                self._challenger = None
                self._challenger_count = 0

            if matched is not None:
                rank, detection, box = candidates[matched]
                self._raw = box
                self._rank = rank
                self._missing = 0
                assert self._smoothed is not None
                self._smoothed = self._alpha * np.array(box, dtype=float) + (1.0 - self._alpha) * self._smoothed
                return self._emit(detection)

            self._missing += 1
            if self._missing < self._lost_frames:
                return None
            self.reset()

        if not candidates:
            return None
        rank, detection, box = min(candidates, key=lambda c: self._merit(c[0], c[1], c[2]))
        self._start(rank, box)
        return self._emit(detection)
