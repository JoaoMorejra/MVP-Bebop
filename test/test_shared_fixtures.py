"""Fixtures shared by the pytest and vitest suites stay true to the Python side."""

from __future__ import annotations

import json
import os

from mvp_mission_bebop.parameters import MissionParameters

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str):
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def test_the_factory_document_fixture_is_the_dataclass_defaults():
    # JSON has no tuple: compare through the same serialisation the store uses.
    expected = json.loads(json.dumps(MissionParameters.factory().to_dict()))
    assert _load("mission_factory_document.json") == expected


def test_every_marker_dictionary_the_sheet_offers_resolves_with_its_stated_size():
    import cv2
    from nectar.vision.algorithms.markers.aruco import resolve_aruco_dict

    for entry in _load("marker_dictionaries.json"):
        code = resolve_aruco_dict(entry["name"])
        assert code == getattr(cv2.aruco, entry["name"]), entry["name"]
        size = int(cv2.aruco.getPredefinedDictionary(code).bytesList.shape[0])
        assert size == entry["markers"], entry["name"]


def test_the_factory_marker_dictionary_is_offered_by_the_sheet():
    names = [entry["name"] for entry in _load("marker_dictionaries.json")]
    assert MissionParameters.factory().rtl.marker_dict in names


def test_the_backend_conflict_rules_hold_the_same_dictionary_table():
    from mvp_mission_bebop.parameter_conflicts import MARKER_DICTIONARIES

    assert [{"name": name, "markers": markers} for name, markers in MARKER_DICTIONARIES] == _load(
        "marker_dictionaries.json"
    )
