"""Pure parts of ``scripts/bench_rehearsal.py`` (8.3)."""

from __future__ import annotations

import importlib.util
import os
import sys

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "bench_rehearsal.py")
_spec = importlib.util.spec_from_file_location("bench_rehearsal", _SCRIPT)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def lines(*items):
    return [(t, text) for t, text in items]


def test_events_are_parsed_from_the_mission_log():
    events = bench.parse_events(
        lines(
            (0.10, "INFO --- [STEP 1: Calibration, Takeoff & Stabilization] ---"),
            (0.20, '[MILESTONE mission.takeoff] {"altitude_m": 1.8}'),
            (0.30, "[SPEECH] 'Decolagem confirmada.' latency_ms=12 source=cache priority=NORMAL"),
            (0.40, "[SPEECH_DONE] 'Decolagem confirmada.' audio_ms=1500 priority=NORMAL"),
            (0.50, '[ALERT mission.abort] {"priority": "URGENT"}'),
            (0.60, "noise"),
        )
    )
    assert [e["kind"] for e in events] == ["step", "milestone", "speech", "speech_done", "alert"]
    assert events[0]["step"] == 1
    assert events[1]["key"] == "mission.takeoff" and events[1]["payload"] == {"altitude_m": 1.8}
    assert events[3]["audio_ms"] == 1500


def test_the_step_order_is_read_once_per_step():
    events = bench.parse_events(lines((0, "[STEP 1: a]"), (1, "[STEP 1: a]"), (2, "[STEP 2: b]"), (3, "[STEP 5: e]")))
    assert bench.step_order(events) == [1, 2, 5]


def test_back_to_back_lines_do_not_overlap():
    events = bench.parse_events(
        lines(
            (1.0, "[SPEECH] 'A' latency_ms=1 source=cache priority=NORMAL"),
            (1.0, "[SPEECH_DONE] 'A' audio_ms=2000 priority=NORMAL"),
            (3.05, "[SPEECH] 'B' latency_ms=1 source=cache priority=NORMAL"),
            (3.05, "[SPEECH_DONE] 'B' audio_ms=500 priority=NORMAL"),
        )
    )
    assert bench.speech_overlaps(events) == []


def test_a_line_starting_under_the_previous_one_is_an_overlap():
    events = bench.parse_events(
        lines(
            (1.0, "[SPEECH] 'A' latency_ms=1 source=cache priority=NORMAL"),
            (1.0, "[SPEECH_DONE] 'A' audio_ms=2000 priority=NORMAL"),
            (2.0, "[SPEECH] 'B' latency_ms=1 source=cache priority=NORMAL"),
        )
    )
    overlaps = bench.speech_overlaps(events)
    assert len(overlaps) == 1 and overlaps[0]["second"] == "B"


def test_an_urgent_line_may_preempt():
    events = bench.parse_events(
        lines(
            (1.0, "[SPEECH] 'A' latency_ms=1 source=cache priority=NORMAL"),
            (1.0, "[SPEECH_DONE] 'A' audio_ms=2000 priority=NORMAL"),
            (1.5, "[SPEECH] 'Abortar' latency_ms=1 source=cache priority=URGENT"),
        )
    )
    assert bench.speech_overlaps(events) == []


def test_milestones_in_order_checks_a_subsequence():
    events = bench.parse_events(
        lines(
            (0, '[MILESTONE mission.countdown] {}'),
            (1, '[MILESTONE mission.takeoff] {}'),
            (2, '[MILESTONE mission.target_detected] {}'),
            (3, '[MILESTONE mission.touchdown] {}'),
        )
    )
    assert bench.milestones_in_order(events, ["mission.takeoff", "mission.touchdown"]) is True
    assert bench.milestones_in_order(events, ["mission.touchdown", "mission.takeoff"]) is False
