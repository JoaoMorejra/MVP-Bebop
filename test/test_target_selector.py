"""Persistent target selection with absolute priority for the accident class."""

from __future__ import annotations

import statistics
from dataclasses import replace
from typing import List, Optional

import numpy as np
import pytest
from nectar.ai.detection.core.types import Detection

from mvp_mission_bebop.estimation.target_selector import TargetSelector
from mvp_mission_bebop.parameters import VisionConfig
from mvp_mission_bebop.perception.classes import ACCIDENT_CLASS_NAMES, is_accident_class

_IDS = {"person": 0, "bicycle": 1, "car": 2, "motorcycle": 3}


def det(name: str, cx: float, cy: float, conf: float, size: float = 80.0) -> Detection:
    half = size / 2.0
    return Detection(
        xyxy=np.array([cx - half, cy - half, cx + half, cy + half], dtype=float),
        confidence=conf,
        class_id=_IDS.get(name, 9),
        class_name=name,
    )


def centre(d: Optional[Detection]) -> float:
    assert d is not None
    return float((d.xyxy[0] + d.xyxy[2]) / 2.0)


def test_accident_aliases_are_case_insensitive() -> None:
    assert "acidente" in ACCIDENT_CLASS_NAMES
    assert is_accident_class("Motorcycle")
    assert not is_accident_class("person")


def test_accident_class_outranks_every_other_class() -> None:
    selector = TargetSelector(VisionConfig())
    out = selector.select([det("person", 100, 100, 0.95), det("car", 300, 100, 0.9), det("motorcycle", 500, 100, 0.5)])
    assert out is not None and out.class_name == "motorcycle"
    fresh = TargetSelector(VisionConfig())
    assert fresh.select([det("person", 100, 100, 0.95), det("car", 300, 100, 0.9)]) is None


def test_target_identity_does_not_flip_between_frames() -> None:
    selector = TargetSelector(VisionConfig())
    first = selector.select([det("bicycle", 200, 240, 0.60), det("bicycle", 600, 240, 0.62)])
    chosen = centre(first)
    for _ in range(3):
        out = selector.select([det("bicycle", 200, 240, 0.70), det("bicycle", 600, 240, 0.55)])
        assert centre(out) == pytest.approx(chosen, abs=1.0)
    assert selector.switches == 0


def test_switch_requires_consecutive_advantage() -> None:
    cfg = replace(VisionConfig(), selector_class_priority=["acidente", "person"], selector_switch_frames=4)
    selector = TargetSelector(cfg)
    assert selector.select([det("person", 100, 240, 0.9)]).class_name == "person"
    both = [det("person", 100, 240, 0.9), det("motorcycle", 600, 240, 0.5)]
    for _ in range(3):
        assert selector.select(both).class_name == "person"
    assert selector.select([det("person", 100, 240, 0.9)]).class_name == "person"
    for _ in range(3):
        assert selector.select(both).class_name == "person"
    assert selector.select(both).class_name == "motorcycle"
    assert selector.switches == 1


def test_track_survives_short_dropouts_and_releases_after_lost_frames() -> None:
    cfg = VisionConfig()
    selector = TargetSelector(cfg)
    selector.select([det("bicycle", 300, 240, 0.8)])
    track = selector.track_id
    for _ in range(cfg.selector_lost_frames - 1):
        assert selector.select([]) is None
    selector.select([det("bicycle", 300, 240, 0.8)])
    assert selector.track_id == track
    for _ in range(cfg.selector_lost_frames):
        selector.select([])
    assert selector.track_id is None
    selector.select([det("bicycle", 300, 240, 0.8)])
    assert selector.track_id is not None and selector.track_id != track


def test_association_uses_iou_then_centroid_gate() -> None:
    selector = TargetSelector(VisionConfig())
    selector.select([det("bicycle", 300, 240, 0.8, size=60)])
    track = selector.track_id
    out = selector.select([det("bicycle", 370, 240, 0.8, size=60)])
    assert out is not None and selector.track_id == track
    out = selector.select([det("bicycle", 470, 240, 0.8, size=60)])
    assert out is None and selector.track_id == track


def test_box_is_smoothed_without_lagging_a_real_move() -> None:
    selector = TargetSelector(VisionConfig())
    raw: List[float] = []
    out: List[float] = []
    for k in range(40):
        x = 400.0 + (6.0 if k % 2 else -6.0)
        raw.append(x)
        out.append(centre(selector.select([det("bicycle", x, 240, 0.8, size=240)])))
    assert statistics.pstdev(out[10:]) <= 0.5 * statistics.pstdev(raw[10:])
    for k in range(5):
        last = centre(selector.select([det("bicycle", 520, 240, 0.8, size=240)]))
    assert last == pytest.approx(520.0, abs=6.0)


def test_equal_candidates_pick_deterministically() -> None:
    picks = set()
    for _ in range(5):
        selector = TargetSelector(VisionConfig())
        out = selector.select(
            [det("bicycle", 600, 240, 0.7, 80), det("bicycle", 200, 240, 0.7, 80), det("bicycle", 400, 240, 0.7, 100)]
        )
        picks.add(centre(out))
    assert picks == {400.0}
    selector = TargetSelector(VisionConfig())
    assert centre(selector.select([det("bicycle", 600, 240, 0.7), det("bicycle", 200, 240, 0.7)])) == 200.0


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError):
        TargetSelector(replace(VisionConfig(), selector_box_alpha=0.0))
    with pytest.raises(ValueError):
        TargetSelector(replace(VisionConfig(), selector_iou_min=1.5))
    with pytest.raises(TypeError):
        TargetSelector(replace(VisionConfig(), selector_lost_frames=2.5))
