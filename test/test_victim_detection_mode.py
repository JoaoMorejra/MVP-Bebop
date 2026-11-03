"""``vision.victim_detection_enabled``: the normal flight and the mode without victims."""

from __future__ import annotations

import pytest

from mvp_mission_bebop.parameters import MissionParameters, ParameterTypeError
from mvp_mission_bebop.perception.classes import (
    PERSON_DISPLAY_NAME,
    VICTIM_DISPLAY_NAME,
    display_class_name,
)


def test_victim_detection_is_on_by_default():
    assert MissionParameters.factory().vision.victim_detection_enabled is True


def test_a_document_turns_victim_detection_off():
    params = MissionParameters()
    params.update_from_dict({"vision": {"victim_detection_enabled": False}})
    assert params.vision.victim_detection_enabled is False


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_victim_detection_accepts_only_a_boolean(value):
    params = MissionParameters()
    with pytest.raises(ParameterTypeError) as refused:
        params.update_from_dict({"vision": {"victim_detection_enabled": value}})
    assert refused.value.field == "vision.victim_detection_enabled"
    assert params.vision.victim_detection_enabled is True


@pytest.mark.parametrize("name", ["person", "Person", " pessoa ", "vitima"])
def test_a_person_reads_vitima_on_and_person_off(name):
    assert display_class_name(name) == VICTIM_DISPLAY_NAME
    assert display_class_name(name, True) == VICTIM_DISPLAY_NAME
    assert display_class_name(name, False) == PERSON_DISPLAY_NAME == "Person"


def test_accident_and_other_classes_ignore_the_victim_mode():
    assert display_class_name("bicycle", False) == "Acidente"
    assert display_class_name("car", False) == "car"


def test_the_victim_mode_must_be_a_bool():
    with pytest.raises(TypeError):
        display_class_name("person", "false")  # type: ignore[arg-type]
