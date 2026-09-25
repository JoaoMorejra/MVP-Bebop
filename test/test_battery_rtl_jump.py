"""The station's low-battery return rides the stage-jump path mid-flight.

The ground station answers a critical charge during stages 2-4 by publishing
stage 5 on ``mission/goto_stage`` (``bebop_mission_control/src/lib/
batteryFailsafe.ts``). These tests pin what that relies on in the runner: a
step blocked in its control loop unwinds on the request, the stages in between
are skipped, the return runs, the jump is not reported as a failure, and the
``[STEP 5: ...]`` marker the station waits for before its fallback landing is
logged.
"""

from __future__ import annotations

import logging
import threading
import time
import types

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.steps.base import BaseStep, StepStatus


class Cruise(BaseStep):
    """A step that flies until interrupted, as the search loop does."""

    def __init__(self, name, trace):
        super().__init__(name)
        self.trace = trace

    def execute(self, ctx):
        self.trace.append(self.name)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if ctx.interrupted():
                return StepStatus.ABORTED
            time.sleep(0.005)
        return StepStatus.SUCCESS


class Instant(BaseStep):
    def __init__(self, name, trace):
        super().__init__(name)
        self.trace = trace

    def execute(self, ctx):
        self.trace.append(self.name)
        return StepStatus.SUCCESS


class _Recorder(logging.Handler):
    """Attached directly: this environment's pytest does not deliver ``caplog``."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def context():
    return types.SimpleNamespace(
        emergency_event=threading.Event(),
        stage_jump_event=threading.Event(),
        requested_stage=None,
        detection_reveal_enabled=False,
        interrupted=lambda: False,
    )


def run_with_jump_during(stage_under_way, monkeypatch):
    trace = []
    ctx = context()
    ctx.interrupted = lambda: MissionContext.interrupted(ctx)
    failures = []
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: failures.append(a)))

    steps = [
        Instant("takeoff", trace) if stage_under_way != 1 else Cruise("takeoff", trace),
        Cruise("search", trace) if stage_under_way == 2 else Instant("search", trace),
        Cruise("tracking", trace) if stage_under_way == 3 else Instant("tracking", trace),
        Cruise("inspection", trace) if stage_under_way == 4 else Instant("inspection", trace),
        Instant("rtl", trace),
    ]
    runner = MissionRunner(ctx, steps, stage_numbers=[1, 2, 3, 4, 5])

    def station():
        while len(trace) < stage_under_way:
            time.sleep(0.005)
        MissionContext.request_stage(ctx, 5)

    threading.Thread(target=station, daemon=True).start()
    recorder = _Recorder()
    target = logging.getLogger("MissionRunner")
    previous = target.level
    target.setLevel(logging.INFO)
    target.addHandler(recorder)
    started = time.monotonic()
    try:
        succeeded = runner.run()
    finally:
        target.removeHandler(recorder)
        target.setLevel(previous)
    return trace, succeeded, failures, time.monotonic() - started, recorder.messages


def test_a_jump_to_stage_5_during_the_search_skips_straight_to_the_return(monkeypatch):
    trace, succeeded, failures, elapsed, messages = run_with_jump_during(2, monkeypatch)

    assert trace == ["takeoff", "search", "rtl"]
    assert succeeded is True
    assert failures == []
    assert elapsed < 1.0, "the running step must unwind promptly, not run out its loop"
    assert any("[STEP 5: rtl]" in message for message in messages)


def test_the_same_jump_works_from_the_approach_and_from_the_inspection(monkeypatch):
    for stage in (3, 4):
        trace, succeeded, failures, _, _ = run_with_jump_during(stage, monkeypatch)
        assert trace[-2:] == [trace[stage - 1], "rtl"]
        assert "rtl" in trace and trace.count("rtl") == 1
        assert succeeded is True and failures == []


def test_an_emergency_still_wins_over_a_pending_jump(monkeypatch):
    trace = []
    ctx = context()
    ctx.interrupted = lambda: MissionContext.interrupted(ctx)
    monkeypatch.setattr(MissionRunner, "_announce_failure", staticmethod(lambda *a, **k: None))
    ctx.emergency_event.set()
    runner = MissionRunner(ctx, [Instant("takeoff", trace), Instant("rtl", trace)], stage_numbers=[1, 5])

    assert runner.run() is False
    assert trace == []
