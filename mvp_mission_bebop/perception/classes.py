"""Single source of the detector class names that denote an accident.

``yolov8n.pt`` is a COCO model with no ``accident`` class: the mission targets
``motorcycle`` and ``bicycle`` (``vision.target_classes``) and the station shows
them as ``Acidente``; a ``person`` is shown as ``Vitima``. A future model trained with a real ``acidente`` class
falls in the same group without code changes. ``bycicle`` is kept because a
misspelt label existed in an earlier custom model.
"""

from __future__ import annotations

from typing import Final, Tuple

#: Display alias the station shows for every accident class. ASCII on purpose:
#: the SDK's ``draw_detections`` writes labels with OpenCV's Hershey fonts,
#: which carry no accented glyphs.
ACCIDENT_DISPLAY_NAME: Final[str] = "Acidente"

#: Display alias the station shows for a detected person, ASCII for the same reason.
VICTIM_DISPLAY_NAME: Final[str] = "Vitima"

#: Lower-case detector labels shown as a victim.
VICTIM_CLASS_NAMES: Final[Tuple[str, ...]] = ("person", "pessoa", "vitima")

#: Lower-case detector labels that belong to the accident group.
ACCIDENT_CLASS_NAMES: Final[Tuple[str, ...]] = ("acidente", "accident", "motorcycle", "bicycle", "bycicle")


def is_accident_class(name: str) -> bool:
    """Whether a detector label belongs to the accident group.

    Parameters
    ----------
    name : str
        Detector class name, any case. A non-string is never an accident class.

    Returns
    -------
    bool
        ``True`` when ``name`` (case-insensitive, stripped) is in
        :data:`ACCIDENT_CLASS_NAMES`.
    """
    if not isinstance(name, str):
        return False
    return name.strip().lower() in ACCIDENT_CLASS_NAMES


def is_victim_class(name: str) -> bool:
    """Whether a detector label denotes a person, shown as a victim.

    Parameters
    ----------
    name : str
        Detector class name, any case. A non-string is never a victim class.

    Returns
    -------
    bool
        ``True`` when ``name`` (case-insensitive, stripped) is in
        :data:`VICTIM_CLASS_NAMES`.
    """
    if not isinstance(name, str):
        return False
    return name.strip().lower() in VICTIM_CLASS_NAMES


def display_class_name(name: str) -> str:
    """The label the station draws for a detector class.

    Parameters
    ----------
    name : str
        Detector class name, any case.

    Returns
    -------
    str
        :data:`ACCIDENT_DISPLAY_NAME` for an accident class,
        :data:`VICTIM_DISPLAY_NAME` for a person, ``name`` unchanged otherwise
        (``""`` for a non-string).
    """
    if is_accident_class(name):
        return ACCIDENT_DISPLAY_NAME
    if is_victim_class(name):
        return VICTIM_DISPLAY_NAME
    return name if isinstance(name, str) else ""
