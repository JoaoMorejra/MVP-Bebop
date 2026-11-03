"""Advanced terminal phases: odometry velocity frame and heading telemetry.

Flight logs 2026-11-03 05:48, 05:50 and 06:14 (Advanced profile):

* 05:48 -- the blind stand-off closure (since removed) flew off the pad on a
  ``/bebop/odom`` twist rotated by the heading a second time; the twist is
  already body FLU.
* 05:50 -- a physical rotation the mission did not command, in the descent.
* 06:14 -- the odometric heading drifted 24 deg with the airframe still; a
  guard acting on it landed 0.8 m from the marker. Heading is now telemetry
  only and never ends a phase.
"""

from __future__ import annotations

import math
import types

import pytest

from mvp_mission_bebop.controllers.cross_axis_hold import body_velocity
from mvp_mission_bebop.controllers.heading_guard import HeadingGuard, wrap_deg
from mvp_mission_bebop.controllers.rtl_guidance import MarkerObservation
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep, _PhaseOutcome

DT = 1.0 / 15.0


# ------------------------------------------------------------ velocity frame


@pytest.mark.parametrize("heading", [0.0, 1.0, math.pi, -2.5])
def test_odometry_twist_is_read_as_body_velocity_at_any_heading(heading: float):
    assert body_velocity(0.07, -0.01) == (0.07, -0.01)


# -------------------------------------------------------------- heading guard


def test_wrap_crosses_the_seam():
    assert wrap_deg(179.0 - -179.0) == pytest.approx(-2.0)
    assert wrap_deg(-190.0) == pytest.approx(170.0)
    assert wrap_deg(180.0) == pytest.approx(180.0)


def test_small_heading_drift_is_tolerated_across_the_seam():
    guard = HeadingGuard()
    for yaw_deg in (179.0, 179.5, -179.5, -178.0, 178.5):
        reading = guard.update(math.radians(yaw_deg), None)
    assert not reading.violated
    assert abs(reading.odom_delta_deg) < 5.0


def test_odometric_rotation_latches_the_guard():
    guard = HeadingGuard()
    guard.update(math.radians(170.0), None)
    reading = guard.update(math.radians(-165.0), None)
    assert reading.violated and "odometric" in reading.note
    assert guard.update(math.radians(170.0), None).violated


def test_marker_rotation_needs_consecutive_cycles():
    guard = HeadingGuard()
    guard.update(None, 10.0)
    assert not guard.update(None, 50.0).violated
    assert not guard.update(None, 12.0).violated
    for _ in range(2):
        assert not guard.update(None, 55.0).violated
    reading = guard.update(None, 56.0)
    assert reading.violated and "marker" in reading.note


def test_guard_rejects_invalid_limits():
    with pytest.raises(ValueError):
        HeadingGuard(odom_limit_deg=0.0)
    with pytest.raises(ValueError):
        HeadingGuard(marker_confirm_cycles=0)


# ----------------------------------------------------------------------- step


def _ctx(yaw_rad: float):
    snapshot = types.SimpleNamespace(yaw=yaw_rad, vx=0.0, vy=0.0)
    return types.SimpleNamespace(odom_supervisor=types.SimpleNamespace(snapshot=lambda: snapshot)), snapshot


def test_advanced_step_supervises_heading_and_simple_does_not():
    ctx, snapshot = _ctx(0.5)
    advanced = ClosedLoopRTLStep(mode=MissionMode.ADVANCED)
    advanced._heading_guard = HeadingGuard()
    seen = MarkerObservation(marker_id=8, x_cam_m=0.0, y_cam_m=0.2, z_cam_m=1.0, yaw_deg=170.0)
    assert not advanced._supervise_heading(ctx, seen).violated
    snapshot.yaw = 0.5 + math.radians(25.0)
    assert advanced._supervise_heading(ctx, None).violated
    assert "hdg odom +25.0" in advanced._heading_suffix(advanced._heading_guard.update(snapshot.yaw, None))

    simple = ClosedLoopRTLStep(mode=MissionMode.SIMPLE)
    assert simple._supervise_heading(ctx, seen) is None
    assert simple._heading_suffix(None) == ""


def test_no_phase_outcome_ends_a_phase_on_heading():
    assert {outcome.name for outcome in _PhaseOutcome} == {"ACHIEVED", "EXHAUSTED", "ABORTED", "FAILED"}
    assert not hasattr(ClosedLoopRTLStep, "_close_standoff")


def test_a_heading_excursion_is_logged_once_and_changes_nothing(monkeypatch):
    warnings = []
    monkeypatch.setattr(rtl_module.logger, "warning", lambda *args, **kwargs: warnings.append(args))
    ctx, snapshot = _ctx(0.0)
    step = ClosedLoopRTLStep(mode=MissionMode.ADVANCED)
    step._heading_guard = HeadingGuard()
    step._supervise_heading(ctx, None)
    for yaw_deg in (25.0, 30.0, 35.0):
        snapshot.yaw = math.radians(yaw_deg)
        reading = step._supervise_heading(ctx, None)
    assert reading.violated
    assert len([w for w in warnings if "Heading excursion" in w[0]]) == 1
