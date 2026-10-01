"""Flat trim acknowledged by the aircraft (6.3).

The driver publishes the count of ``FlatTrimChanged`` events on
``states/flat_trim``. Stage 1 reads it before requesting the flat trim and waits
up to ``timeouts.flat_trim_ack_timeout_sec`` for it to advance; a real flight
without that confirmation does not take off.
"""

from __future__ import annotations

import threading
import types

import pytest

from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.announcer import _format_telemetry_statement
from mvp_mission_bebop.telemetry.flat_trim_ack import FlatTrimAckTracker


def test_the_tracker_waits_for_the_count_to_advance():
    tracker = FlatTrimAckTracker()
    tracker.update(4)
    mark = tracker.sequence
    threading.Timer(0.05, lambda: tracker.update(5)).start()
    assert tracker.wait_after(mark, 1.0) is True


def test_the_tracker_times_out_without_a_new_count():
    tracker = FlatTrimAckTracker()
    tracker.update(4)
    assert tracker.wait_after(4, 0.05) is False


def test_a_tracker_that_never_heard_the_driver_has_no_mark():
    tracker = FlatTrimAckTracker()
    assert tracker.sequence is None
    tracker.update(1)
    assert tracker.wait_after(None, 0.05) is True


@pytest.mark.parametrize("value", [-1, True, "3", None])
def test_invalid_counts_are_ignored(value):
    tracker = FlatTrimAckTracker()
    tracker.update(value)
    assert tracker.sequence is None


class Drone:
    def __init__(self, no_fly, tracker=None, ack=True):
        self.no_fly = no_fly
        self.tracker = tracker
        self.ack = ack
        self.calls = []

    def flat_trim(self):
        self.calls.append("flat_trim")
        if self.ack and self.tracker is not None:
            self.tracker.update((self.tracker.sequence or 0) + 1)

    def delay(self, seconds):
        self.calls.append(("delay", seconds))

    def camera_control(self, tilt, pan):
        self.calls.append("camera_control")


def make_ctx(no_fly, ack=True, tracked=True):
    params = MissionParameters()
    params.timeouts.flat_trim_ack_timeout_sec = 0.1
    tracker = FlatTrimAckTracker() if tracked else None
    if tracker is not None:
        tracker.update(7)
    return types.SimpleNamespace(
        params=params,
        drone=Drone(no_fly, tracker, ack),
        flat_trim_ack=tracker,
        failsafe=types.SimpleNamespace(),
        current_tilt_deg=0.0,
    )


@pytest.fixture
def announced(monkeypatch):
    calls = []
    monkeypatch.setattr(TakeoffStep, "_announce", staticmethod(lambda action, detail, **kw: calls.append((action, detail, kw))))
    return calls


def test_an_acknowledged_flat_trim_proceeds(announced):
    ctx = make_ctx(no_fly=False)
    assert TakeoffStep()._flat_trim(ctx) is True
    assert announced == []


def test_a_real_flight_without_acknowledgement_does_not_take_off(announced):
    ctx = make_ctx(no_fly=False, ack=False)
    assert TakeoffStep()._flat_trim(ctx) is False
    assert announced and announced[0][0] == "Nivelamento sem confirmação"
    assert announced[0][2].get("priority") == "CRITICAL"


def test_a_real_flight_without_the_driver_topic_does_not_take_off(announced):
    ctx = make_ctx(no_fly=False, tracked=False)
    assert TakeoffStep()._flat_trim(ctx) is False


def test_the_bench_proceeds_without_acknowledgement(announced):
    ctx = make_ctx(no_fly=True, ack=False)
    assert TakeoffStep()._flat_trim(ctx) is True
    assert announced == []


def test_stage_one_fails_before_calibrating_on_a_missing_acknowledgement(announced, monkeypatch):
    ctx = make_ctx(no_fly=False, ack=False)
    monkeypatch.setattr(TakeoffStep, "_calibrate", lambda self, c: pytest.fail("calibrated after a refused flat trim"))
    assert TakeoffStep().execute(ctx) is StepStatus.FAILURE


def test_the_alert_claims_no_landing_on_the_ground():
    sentence = _format_telemetry_statement(
        "Nivelamento sem confirmação", {"etapa": "a aeronave não confirmou o nivelamento, decolagem cancelada"}
    )
    assert sentence.startswith("Nivelamento sem confirmação")
    assert "pouso" not in sentence.lower()
