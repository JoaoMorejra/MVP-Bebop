"""Victim assessment of the inspection frame: the reduction and its wire form.

The report's victim line is decided from this tri-state, so the properties
pinned here are the ones the wording depends on: a ``person`` below the
threshold or too small is not a victim, an empty verdict is ``not_detected``,
and an unavailable inference is ``unknown`` whatever else its payload says.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from nectar.ai.detection.core.types import Detection, DetectionResult

from mvp_mission_bebop.perception.assessment import (
    ASSESSMENT_SCHEMA,
    MAX_VICTIM_BOXES,
    REASON_DETECTOR_ERROR,
    AssessmentStatus,
    InspectionAssessment,
    VictimStatus,
    assess_detections,
    unavailable,
)

SHAPE = (480, 856, 3)
STAMP = "20261020_101500"
UTC = "2026-10-20T13:15:01+00:00"


def _det(xyxy, confidence, name="person", class_id=0):
    return Detection(xyxy=np.array(xyxy, dtype=float), confidence=confidence, class_id=class_id, class_name=name)


def _assess(detections, **kwargs):
    return assess_detections(
        DetectionResult(detections=list(detections)),
        SHAPE,
        capture_stamp=STAMP,
        completed_at_utc=UTC,
        **kwargs,
    )


def test_a_person_above_the_threshold_is_a_detected_victim():
    assessment = _assess([_det([100, 100, 160, 220], 0.62)])

    assert assessment.status is AssessmentStatus.COMPLETE
    assert assessment.victim_status is VictimStatus.DETECTED
    assert assessment.victim_detected is True
    assert assessment.victim_count == 1
    assert assessment.victim_confidence == pytest.approx(0.62)
    assert assessment.victim_boxes == ((100, 100, 160, 220),)
    assert assessment.frame == (856, 480)


def test_a_person_below_the_threshold_is_not_a_victim():
    assessment = _assess([_det([100, 100, 160, 220], 0.34)])

    assert assessment.victim_status is VictimStatus.NOT_DETECTED
    assert assessment.victim_count == 0
    assert assessment.victim_confidence is None


def test_a_tiny_person_box_is_not_a_victim():
    # 10 x 10 px = 100 px^2, under 0.0005 of 856 x 480 (205 px^2).
    assessment = _assess([_det([10, 10, 20, 20], 0.95)])

    assert assessment.victim_status is VictimStatus.NOT_DETECTED


def test_an_empty_verdict_is_not_detected():
    assessment = _assess([])

    assert assessment.status is AssessmentStatus.COMPLETE
    assert assessment.victim_status is VictimStatus.NOT_DETECTED
    assert assessment.victim_boxes == ()


def test_other_classes_never_count_as_victims():
    assessment = _assess([_det([100, 100, 400, 300], 0.9, name="motorcycle", class_id=3)])

    assert assessment.victim_status is VictimStatus.NOT_DETECTED


def test_boxes_are_ordered_by_confidence_and_capped():
    detections = [_det([10 * i, 0, 10 * i + 60, 80], 0.40 + 0.01 * i) for i in range(12)]
    assessment = _assess(detections)

    assert assessment.victim_count == 12
    assert len(assessment.victim_boxes) == MAX_VICTIM_BOXES
    assert assessment.victim_boxes[0] == (110, 0, 170, 80)
    assert assessment.victim_confidence == pytest.approx(0.51)


def test_an_unavailable_assessment_is_unknown():
    assessment = unavailable(STAMP, REASON_DETECTOR_ERROR, SHAPE, completed_at_utc=UTC)

    assert assessment.victim_status is VictimStatus.UNKNOWN
    assert assessment.victim_detected is False
    assert assessment.reason == REASON_DETECTOR_ERROR


def test_the_payload_round_trips_through_json():
    assessment = _assess([_det([100, 100, 160, 220], 0.62)], inference_ms=212.3, model="yolov8n.pt", imgsz=640)
    payload = json.loads(json.dumps(assessment.to_payload()))

    assert payload["schema"] == ASSESSMENT_SCHEMA
    assert payload["victim_status"] == "detected"
    assert InspectionAssessment.from_payload(payload) == assessment


def test_a_malformed_unavailable_payload_with_a_victim_flag_is_still_unknown():
    payload = unavailable(STAMP, "timeout", SHAPE, completed_at_utc=UTC).to_payload()
    payload.update(victim_detected=True, victim_status="detected")

    assessment = InspectionAssessment.from_payload(payload)

    assert assessment.victim_detected is True
    assert assessment.victim_status is VictimStatus.UNKNOWN


@pytest.mark.parametrize(
    "mutate, error",
    [
        (lambda p: p.update(schema="bmg.inspection.v0"), ValueError),
        (lambda p: p.pop("frame"), ValueError),
        (lambda p: p.update(status="maybe"), ValueError),
        (lambda p: p.update(victim_count=-1), ValueError),
        (lambda p: p.update(victim_detected="yes"), TypeError),
        (lambda p: p.update(conf=1.5), ValueError),
        (lambda p: p.update(victim_boxes="[]"), TypeError),
        (lambda p: p.update(status="unavailable", reason=None), ValueError),
    ],
)
def test_from_payload_rejects_malformed_input(mutate, error):
    payload = _assess([]).to_payload()
    mutate(payload)

    with pytest.raises(error):
        InspectionAssessment.from_payload(payload)


def test_from_payload_rejects_a_non_mapping():
    with pytest.raises(TypeError):
        InspectionAssessment.from_payload([])


def test_assess_rejects_invalid_thresholds_and_results():
    with pytest.raises(ValueError):
        _assess([], conf_threshold=1.2)
    with pytest.raises(ValueError):
        _assess([], min_box_area_frac=1.0)
    with pytest.raises(TypeError):
        assess_detections(object(), SHAPE, capture_stamp=STAMP, completed_at_utc=UTC)
