"""Stage 2 does not leave the pad until the altitude has settled on the relative target.

The firmware ends its launch profile about 1.0 m above the pad and the target is
0.8 m above it. Crossing the edge with that 0.2 m still being corrected mixes the
governor's descent with the firmware's answer to the sonar step, and field test
F-2 has to tell the two apart.
"""

from __future__ import annotations

import time
from typing import List, Tuple

import pytest

from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.search import ForwardSearchStep
from test_stage_altitude_wiring import Ctx

HIGH = 1.00
ON_TARGET = 0.80


class TimedOdometry:
    """Healthy telemetry whose altitude drops onto the target after ``switch_sec``."""

    def __init__(self, switch_sec: float) -> None:
        self.switch_sec = switch_sec
        self.started = None
        self.speed = self.vx = self.vy = self.vz = self.horizontal_speed = 0.0
        self.x = self.y = self.takeoff_x = self.takeoff_y = 0.0
        self.has_launch_origin = True

    def elapsed(self) -> float:
        if self.started is None:
            self.started = time.monotonic()
        return time.monotonic() - self.started

    @property
    def relative_altitude(self) -> float:
        return ON_TARGET if self.elapsed() >= self.switch_sec else HIGH

    def snapshot(self):
        return self

    @staticmethod
    def telemetry_health():
        from mvp_mission_bebop.telemetry.odometry import TelemetryHealth

        return TelemetryHealth.HEALTHY

    @staticmethod
    def is_ceiling_breached():
        return False


def pad_ctx(switch_sec: float, edge=0.25, window=0.2, timeout=8.0):
    ctx = Ctx(detections=False)
    ctx.params.kinematics.target_altitude_m = 1.8
    ctx.params.platform.height_m = 1.0
    ctx.params.platform.edge_ahead_m = edge
    ctx.params.platform.departure_settle_window_sec = window
    ctx.params.platform.departure_settle_timeout_sec = timeout
    odometry = TimedOdometry(switch_sec)
    ctx.odom_supervisor = odometry
    ctx.failsafe.odom_supervisor = odometry
    stamped: List[Tuple[float, float]] = []
    move = ctx.drone.move_velocity

    def recording_move(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        stamped.append((odometry.elapsed(), vy))
        move(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    ctx.drone.move_velocity = recording_move
    return ctx, stamped


def test_no_translation_until_the_altitude_holds_inside_the_deadband():
    ctx, stamped = pad_ctx(switch_sec=0.3, window=0.2)

    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS

    released = 0.3 + 0.2
    early = [vy for t, vy in stamped if t < released - 0.02]
    assert early, "the settle sent no commands"
    assert all(vy == 0.0 for vy in early)
    assert any(vy > 0.0 for t, vy in stamped if t >= released)


def test_a_settle_that_never_converges_times_out_and_the_search_goes_on():
    ctx, stamped = pad_ctx(switch_sec=1e9, timeout=0.3)

    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS

    assert all(vy == 0.0 for t, vy in stamped if t < 0.28)
    assert any(vy > 0.0 for _t, vy in stamped), "an expired settle must not end the search"


def test_the_search_window_is_not_spent_on_the_settle():
    ctx, stamped = pad_ctx(switch_sec=0.0, window=0.2)
    ctx.params.timeouts.search_timeout_sec = 0.4

    ForwardSearchStep().execute(ctx)

    cruising = [t for t, vy in stamped if vy > 0.0]
    assert cruising
    assert max(t for t, _vx in stamped) - min(cruising) >= 0.3


def test_the_settle_is_logged(caplog):
    import logging

    ctx, _ = pad_ctx(switch_sec=0.0, window=0.1)
    with caplog.at_level(logging.INFO, logger="Step2Search"):
        logging.getLogger("Step2Search").addHandler(caplog.handler)
        try:
            ForwardSearchStep().execute(ctx)
        finally:
            logging.getLogger("Step2Search").removeHandler(caplog.handler)
    assert any(r.getMessage().startswith("[PLATFORM departure_settle err=") for r in caplog.records)


def test_without_an_edge_distance_the_step_does_not_settle(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("settled with platform.edge_ahead_m = None")

    monkeypatch.setattr(ForwardSearchStep, "_settle_before_departure", forbidden)
    ctx, stamped = pad_ctx(switch_sec=1e9, edge=None)

    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS
    assert stamped[0][1] >= 0.0
    assert any(vy > 0.0 for t, vy in stamped if t < 0.2)


class LowOdometry(TimedOdometry):
    """Holds 5 cm under the target: a deficit the quantized climb never closes."""

    @property
    def relative_altitude(self) -> float:
        self.elapsed()
        return ON_TARGET - 0.05


def test_a_small_deficit_the_governor_cannot_close_does_not_hold_the_departure():
    ctx, stamped = pad_ctx(switch_sec=0.0, window=0.2, timeout=5.0)
    odometry = LowOdometry(0.0)
    ctx.odom_supervisor = odometry
    ctx.failsafe.odom_supervisor = odometry
    ctx.params.timeouts.search_timeout_sec = 1.0

    assert ForwardSearchStep().execute(ctx) is StepStatus.SUCCESS

    departed = [t for t, vy in stamped if vy > 0.0]
    assert departed, "the search never left the pad"
    assert min(departed) < 1.0
