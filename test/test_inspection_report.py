"""Forensic report engine: order rules, cross-flight memory and the victim line.

Police, SAMU and the vehicle are clearances (the vehicle line always names the
tow truck); only the victim line follows the inference on the inspection frame.
The properties below pin the order rules (the report opens with the victim when
one was detected and with the vehicle otherwise, SAMU and police alternating
across flights), the memory (no wording repeats the previous flight's) and the
rule the victim line exists for: without a completed inference, nothing is said
about the victim.
"""

from __future__ import annotations

import json
import random
import re
import unicodedata

import pytest

from mvp_mission_bebop.perception.assessment import VictimStatus, unavailable
from mvp_mission_bebop.telemetry import announcer
from mvp_mission_bebop.telemetry.inspection_report import (
    FIXED_FINDINGS,
    HISTORY_FILENAME,
    TOPICS,
    VICTIM_FINDINGS,
    _decapitalise,
    build_inspection_report,
    check_topic_order,
    history_path_for,
    lead_topics_for,
    load_history,
)

STATUSES = [status.value for status in VictimStatus]

#: Stems the neutral line must never contain, compared without case or accents.
FORBIDDEN_IN_UNKNOWN = (
    "conscien",
    "ferim",
    "lesã",
    "nenhum",
    "sem vítimas",
    "ausência",
    "identificad",
    "visível",
    "detect",
)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _by_topic(report):
    return {finding["topic"]: finding["card"] for finding in report}


@pytest.mark.parametrize("status", STATUSES)
def test_the_report_never_opens_with_a_dispatch_topic(status):
    for seed in range(1000):
        report = build_inspection_report(status, rng=random.Random(seed))
        topics = [finding["topic"] for finding in report]
        assert topics[0] in ("vehicle", "victim"), (seed, topics)
        assert sorted(topics) == sorted(TOPICS)
        assert set(topics[2:]) == {"samu", "police"}


def test_the_report_opens_with_the_victim_only_when_one_was_detected():
    for seed in range(50):
        detected = build_inspection_report("detected", rng=random.Random(seed))
        assert [finding["topic"] for finding in detected[:2]] == ["victim", "vehicle"]
        for status in ("not_detected", "unknown"):
            report = build_inspection_report(status, rng=random.Random(seed))
            assert [finding["topic"] for finding in report[:2]] == ["vehicle", "victim"]


def test_lead_topics_for_follows_the_victim_status():
    assert lead_topics_for(VictimStatus.DETECTED) == ("victim", "vehicle")
    assert lead_topics_for(VictimStatus.NOT_DETECTED) == ("vehicle", "victim")
    assert lead_topics_for(VictimStatus.UNKNOWN) == ("vehicle", "victim")
    with pytest.raises(TypeError):
        lead_topics_for("detected")


def test_the_dispatch_order_alternates_across_flights_with_persisted_history(tmp_path):
    path = history_path_for(str(tmp_path))
    dispatch = []
    for flight in range(8):
        report = build_inspection_report("detected", history_path=path, rng=random.Random(flight))
        tail = tuple(finding["topic"] for finding in report[2:])
        assert set(tail) == {"samu", "police"}
        dispatch.append(tail)

    assert all(a != b for a, b in zip(dispatch, dispatch[1:])), dispatch
    assert (tmp_path / HISTORY_FILENAME).exists()


@pytest.mark.parametrize("status", STATUSES)
def test_every_vehicle_card_names_the_tow_truck(status):
    for seed in range(100):
        vehicle = next(
            finding for finding in build_inspection_report(status, rng=random.Random(seed)) if finding["topic"] == "vehicle"
        )
        assert re.search(r"guincho", vehicle["card"], re.IGNORECASE)
        assert re.search(r"(danos|avarias) (estruturais )?aparentes|Integridade", vehicle["card"])
        assert re.search(r"guincho", vehicle["speech"], re.IGNORECASE)
        assert ".." not in vehicle["speech"]


def test_a_stale_history_document_is_tolerated(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    path.write_text(
        json.dumps(
            {
                "schema": "bmg.inspection_history.v1",
                "history": {"finding.lead": [0, 1], "finding.vehicle": [9], "finding.victim.not_detected": [5, 7]},
            }
        ),
        encoding="utf-8",
    )
    previous = None
    for flight in range(10):
        report = build_inspection_report("not_detected", history_path=str(path), rng=random.Random(flight))
        assert len(report) == 4
        cards = _by_topic(report)
        if previous is not None:
            for topic in TOPICS:
                assert cards[topic] != previous[topic], (flight, topic)
        previous = cards


@pytest.mark.parametrize("status", STATUSES)
def test_no_wording_repeats_the_previous_flight(tmp_path, status):
    path = history_path_for(str(tmp_path))
    previous = None
    for flight in range(30):
        cards = _by_topic(build_inspection_report(status, history_path=path, rng=random.Random(flight)))
        if previous is not None:
            for topic in TOPICS:
                assert cards[topic] != previous[topic], (flight, topic)
        previous = cards


def test_the_fixed_topics_are_identical_in_the_three_branches(tmp_path):
    for seed in range(50):
        reports = [build_inspection_report(status, rng=random.Random(seed)) for status in STATUSES]
        fixed = [{t: c for t, c in _by_topic(report).items() if t != "victim"} for report in reports]
        orders = [[finding["topic"] for finding in report] for report in reports]
        assert fixed[0] == fixed[1] == fixed[2]
        assert orders[1] == orders[2]
        assert orders[0][2:] == orders[1][2:]
    for topic in ("police", "samu", "vehicle"):
        assert len(FIXED_FINDINGS[topic]) == (5 if topic == "vehicle" else 4)


@pytest.mark.parametrize("status", STATUSES)
def test_the_victim_line_comes_from_its_branch_pool(status):
    for seed in range(200):
        cards = _by_topic(build_inspection_report(status, rng=random.Random(seed)))
        assert cards["victim"] in VICTIM_FINDINGS[status]


def test_the_neutral_pool_states_nothing_about_the_victim():
    for line in VICTIM_FINDINGS["unknown"]:
        folded = _fold(line)
        for stem in FORBIDDEN_IN_UNKNOWN:
            assert _fold(stem) not in folded, (line, stem)


@pytest.mark.parametrize("reason", ["detector_error", "detector_unavailable", "timeout"])
def test_an_unavailable_inference_always_reads_the_neutral_line(reason):
    payload = unavailable("20261020_101500", reason, (480, 856, 3), completed_at_utc="2026-10-20T13:15:01+00:00")
    from mvp_mission_bebop.perception.assessment import InspectionAssessment

    malformed = payload.to_payload()
    malformed["victim_detected"] = True
    malformed["victim_count"] = 1
    malformed["victim_boxes"] = [[100, 100, 200, 200]]
    assessment = InspectionAssessment.from_payload(json.loads(json.dumps(malformed)))

    for seed in range(50):
        cards = _by_topic(announcer.build_forensic_report(seed=seed, assessment=assessment))
        assert cards["victim"] in VICTIM_FINDINGS["unknown"]


def test_the_wrapper_without_assessment_reads_the_neutral_line():
    for seed in range(50):
        cards = _by_topic(announcer.build_forensic_report(seed=seed))
        assert cards["victim"] in VICTIM_FINDINGS["unknown"]


@pytest.mark.parametrize("status", STATUSES)
def test_card_and_speech_are_the_same_line(status):
    for seed in range(100):
        for finding in build_inspection_report(status, rng=random.Random(seed)):
            speech = finding["speech"]
            assert speech.endswith(f" {_decapitalise(finding['card'])}.")
            connector = speech[: -len(_decapitalise(finding["card"])) - 2]
            assert connector in (
                *announcer.FORENSIC_OPENERS,
                *announcer.FORENSIC_LINKERS,
                *announcer.FORENSIC_CLOSERS,
            )


def test_order_invariants_are_enforced():
    check_topic_order(["vehicle", "victim", "samu", "police"])
    with pytest.raises(ValueError):
        check_topic_order(["samu", "victim", "vehicle", "police"])
    with pytest.raises(ValueError):
        check_topic_order(["vehicle", "victim", "samu", "samu"])
    with pytest.raises(ValueError):
        check_topic_order(["vehicle", "victim", "samu"])


def test_a_corrupt_history_reads_as_none_and_is_rewritten(tmp_path):
    path = tmp_path / HISTORY_FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert load_history(str(path)) == {}

    build_inspection_report("detected", history_path=str(path), rng=random.Random(1))

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == "bmg.inspection_history.v1"
    assert set(document["history"]) >= {"finding.dispatch_order", "finding.victim.detected"}
    assert "finding.lead" not in document["history"]


def test_an_invalid_victim_status_is_refused():
    with pytest.raises(ValueError):
        build_inspection_report("maybe")
    with pytest.raises(TypeError):
        build_inspection_report(None)
