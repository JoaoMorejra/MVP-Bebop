"""Native photo acknowledgement (4.7): RecordPictureV2 answered by the aircraft.

The driver publishes the ARSDK photo events as JSON on ``states/picture_event``.
The capture triggers the 14 MP photo before its own inference and records in the
evidence sidecar whether the aircraft reported it taken.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker


def event(sequence, kind="event", value=0, error=0):
    return json.dumps({"sequence": sequence, "kind": kind, "value": value, "error": error, "stamp": 1.0})


def test_a_taken_event_after_the_request_is_an_acknowledgement():
    tracker = PictureAckTracker()
    tracker.update(event(1, kind="state", value=1))
    marker = tracker.sequence
    threading.Timer(0.05, lambda: tracker.update(event(2, value=0))).start()
    ack = tracker.wait_after(marker, timeout_sec=1.0)
    assert ack == {"acknowledged": True, "event": "taken", "error": "ok", "sequence": 2}


def test_a_failed_event_is_reported_with_its_reason():
    tracker = PictureAckTracker()
    tracker.update(event(5, value=1, error=4))
    assert tracker.wait_after(0, timeout_sec=0.1) == {
        "acknowledged": False,
        "event": "failed",
        "error": "memoryfull",
        "sequence": 5,
    }


def test_no_event_within_the_window_is_no_acknowledgement():
    tracker = PictureAckTracker()
    started = time.monotonic()
    assert tracker.wait_after(0, timeout_sec=0.1) is None
    assert time.monotonic() - started < 0.5


def test_state_changes_alone_do_not_acknowledge_the_photo():
    tracker = PictureAckTracker()
    tracker.update(event(1, kind="state", value=1))
    tracker.update(event(2, kind="state", value=0))
    assert tracker.wait_after(0, timeout_sec=0.05) is None


@pytest.mark.parametrize("text", ["", "not json", "[1]", json.dumps({"kind": "event"})])
def test_malformed_events_are_ignored(text):
    tracker = PictureAckTracker()
    tracker.update(text)
    assert tracker.sequence == 0
