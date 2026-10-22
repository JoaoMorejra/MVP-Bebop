"""Inspection package in the forensic sidecar, against the contract of the
visual inference agent (``agent-tech4ai-agent-drone-inferencia-visual``,
``docs/PROJECT.md`` sections 3 and 9.1)."""

from __future__ import annotations

import json
import os
import re
import time
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from mvp_mission_bebop.blackboard import EvidenceRecord
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry.inference_package import (
    CAPTION_FIELDS,
    REPORT_WITHOUT_IDENTIFIER,
    SITE_LATITUDE,
    SITE_LONGITUDE,
    SITE_NAME,
    build_inference_package,
    build_telemetry_caption,
    drone_occurrence_id,
    format_utc,
    report_filename,
    select_inspection_image,
)

STATION_DIR = os.path.join(os.path.dirname(__file__), "..", "bebop_mission_control")
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
INSTANT = datetime(2026, 10, 20, 14, 35, 0, tzinfo=timezone.utc)


def fake_context(output_dir: str) -> types.SimpleNamespace:
    params = MissionParameters()
    params.output_dir = output_dir
    fake = types.SimpleNamespace(
        frame_width=856, frame_height=480, current_tilt_deg=-90.0,
        params=params, start_time=time.time(),
    )
    fake._write_atomic = types.MethodType(MissionContext._write_atomic, fake)
    fake._write_metadata = types.MethodType(MissionContext._write_metadata, fake)
    fake._describe_detections = MissionContext._describe_detections
    fake._discard = MissionContext._discard
    return fake


def test_the_site_is_the_front_map_anchor_to_the_digit():
    with open(os.path.join(STATION_DIR, "src", "lib", "siteAnchor.ts"), encoding="utf-8") as handle:
        source = handle.read()
    anchor = re.search(
        r"MAP_ANCHOR\s*=\s*\{\s*latitude:\s*(-?[\d.]+),\s*longitude:\s*(-?[\d.]+),\s*name:\s*'([^']+)'",
        source,
    )
    assert anchor is not None
    assert (float(anchor.group(1)), float(anchor.group(2)), anchor.group(3)) == (
        SITE_LATITUDE,
        SITE_LONGITUDE,
        SITE_NAME,
    )
    with open(os.path.join(STATION_DIR, "config", "site-anchor.json"), encoding="utf-8") as handle:
        shipped = json.load(handle)
    assert (shipped["latitude"], shipped["longitude"]) == (SITE_LATITUDE, SITE_LONGITUDE)


def test_the_occurrence_id_and_timestamp_follow_the_official_formats():
    assert drone_occurrence_id(INSTANT) == "OC_20261020_143500"
    assert format_utc(INSTANT) == "2026-10-20T14:35:00Z"
    local = INSTANT.astimezone(timezone(timedelta(hours=-3)))
    assert drone_occurrence_id(local) == "OC_20261020_143500"
    with pytest.raises(ValueError):
        format_utc(datetime(2026, 10, 20, 14, 35, 0))


def test_a_complete_caption_has_exactly_the_four_agent_fields():
    caption, pending = build_telemetry_caption("OC_20261020_143500", INSTANT)
    assert tuple(caption) == CAPTION_FIELDS
    assert caption == {
        "occurrence_id": "OC_20261020_143500",
        "latitude": -23.6488913,
        "longitude": -46.7187124,
        "timestamp_utc": "2026-10-20T14:35:00Z",
    }
    assert pending == []


def test_missing_values_are_left_out_and_listed_as_pending_never_filled():
    caption, pending = build_telemetry_caption("bad id with spaces", None)
    assert caption == {"latitude": SITE_LATITUDE, "longitude": SITE_LONGITUDE}
    assert pending == [
        {"campo": "occurrence_id", "motivo": "invalido"},
        {"campo": "timestamp_utc", "motivo": "ausente"},
    ]


def test_the_original_frame_is_preferred_over_the_annotated_copy():
    image = select_inspection_image("/x/accident_raw_1.png", "/x/accident_inspected_1.jpg")
    assert image == {"filename": "accident_raw_1.png", "mime_type": "image/png", "contains_detector_overlay": False}
    fallback = select_inspection_image(None, "/x/accident_inspected_1.jpg")
    assert fallback["mime_type"] == "image/jpeg"
    assert fallback["contains_detector_overlay"] is True
    assert select_inspection_image(None, None) is None


def test_the_package_serialises_the_caption_exactly_as_sent():
    package = build_inference_package("OC_20261020_143500", INSTANT, "/x/accident_raw_1.png", None)
    assert json.loads(package["message"]["caption"]) == package["caption"]
    assert package["message"]["type"] == "image"
    assert package["open_conversation"] == {"data": {"occurrence_id": "OC_20261020_143500"}}
    assert package["expected_report"] == "relatorio_OC_20261020_143500.html"
    assert package["ready"] is True
    assert package["position"] == {"source": "site", "site": SITE_NAME}


def test_without_an_identifier_the_report_name_is_the_agents_fallback():
    assert report_filename(None) == REPORT_WITHOUT_IDENTIFIER
    package = build_inference_package(None, INSTANT, None, None)
    assert package["open_conversation"] == {"data": {}}
    assert package["expected_report"] == REPORT_WITHOUT_IDENTIFIER
    assert package["ready"] is False


def test_a_capture_writes_the_complete_package(tmp_path):
    fake = fake_context(str(tmp_path))
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    record = MissionContext.record_photographic_evidence(fake, frame, frame)

    with open(record.metadata_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    package = payload["inference_package"]
    caption = package["caption"]

    assert re.match(r"^OC_\d{8}_\d{6}$", record.occurrence_id)
    assert payload["occurrence_id"] == caption["occurrence_id"] == record.occurrence_id
    assert UTC_PATTERN.match(caption["timestamp_utc"])
    stamp = caption["timestamp_utc"].replace("-", "").replace(":", "")
    assert record.occurrence_id == f"OC_{stamp[:8]}_{stamp[9:15]}"
    assert (payload["latitude"], payload["longitude"]) == (SITE_LATITUDE, SITE_LONGITUDE)
    assert payload["telemetry_caption"] == caption
    assert package["pending"] == []
    assert package["message"]["image"]["filename"] == os.path.basename(record.raw_path)


def test_the_sidecar_derives_the_identifier_from_the_capture_instant(tmp_path):
    fake = fake_context(str(tmp_path))
    path = os.path.join(str(tmp_path), "accident_metadata_test.json")

    MissionContext._write_metadata(fake, path, EvidenceRecord(), None, None, INSTANT)

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["occurrence_id"] == "OC_20261020_143500"
    assert payload["timestamp_utc"] == "2026-10-20T14:35:00Z"
    assert payload["inference_package"]["expected_report"] == "relatorio_OC_20261020_143500.html"
