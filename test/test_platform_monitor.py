"""Recognising where the airframe is relative to the pad edge, from the along-track position.

The monitor is agnostic to the axis of the track; the supervisor feeds it the leftward
projection (:func:`outbound_track_m`).

The sonar step happens at ``s* = edge_ahead_m + h * tan(sonar_half_angle)``.
The monitor brackets it with a guard window and names the four situations the
edge guard reacts to: over the pad, leaving it, off it, and coming back.
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import Iterable, List

import pytest

from mvp_mission_bebop.estimation.platform_monitor import (
    PlatformMonitor,
    PlatformReport,
    PlatformState,
)
from mvp_mission_bebop.parameters import MissionParameters, PlatformConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

DT = 0.1
H = 0.8


def config(**overrides) -> PlatformConfig:
    cfg = PlatformConfig(height_m=1.0, edge_ahead_m=1.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@contextlib.contextmanager
def monitor_log():
    handler = _Lines()
    target = logging.getLogger("PlatformMonitor")
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


def platform_lines(lines: Iterable[str]) -> List[str]:
    return [line for line in lines if line.startswith("[PLATFORM state=")]


def ramp(start: float, stop: float, step: float) -> List[float]:
    count = int(round(abs(stop - start) / step))
    sign = 1.0 if stop >= start else -1.0
    return [start + sign * step * i for i in range(count + 1)]


def drive(monitor: PlatformMonitor, positions: Iterable[float], h: float = H) -> List[PlatformReport]:
    return [monitor.update(s, h, DT) for s in positions]


def states(reports: Iterable[PlatformReport]) -> List[PlatformState]:
    seen: List[PlatformState] = []
    for report in reports:
        if not seen or seen[-1] is not report.state:
            seen.append(report.state)
    return seen


def edge_step(cfg: PlatformConfig, h: float = H) -> float:
    return cfg.edge_ahead_m + max(h, 0.3) * math.tan(math.radians(cfg.sonar_half_angle_deg))


def test_departure_runs_on_leaving_off_with_one_line_per_transition():
    cfg = config()
    monitor = PlatformMonitor(cfg)
    with monitor_log() as log:
        reports = drive(monitor, ramp(0.0, 3.0, 0.02))

    assert states(reports) == [PlatformState.ON, PlatformState.LEAVING, PlatformState.OFF]
    lines = platform_lines(log.lines)
    assert [line.split()[1] for line in lines] == ["state=ON", "state=LEAVING", "state=OFF"]
    leaving = lines[1]
    assert "s_star=1.29" in leaving and "h=0.80" in leaving and "dir=+1" in leaving


def test_the_guard_covers_the_edge_and_the_recovery_beyond_it():
    cfg = config()
    s_star = edge_step(cfg)
    monitor = PlatformMonitor(cfg)
    reports = drive(monitor, ramp(0.0, 3.0, 0.02))

    guarded = [r.along_track_m for r in reports if r.guard_active]
    assert min(guarded) == pytest.approx(s_star - cfg.edge_uncertainty_m - cfg.guard_lead_m, abs=0.03)
    assert max(guarded) == pytest.approx(s_star + cfg.edge_uncertainty_m + cfg.guard_extent_m, abs=0.03)
    assert all(r.guard_active == (r.state in (PlatformState.LEAVING, PlatformState.ENTERING)) for r in reports)


def test_return_runs_off_entering_on():
    cfg = config()
    monitor = PlatformMonitor(cfg)
    drive(monitor, ramp(0.0, 3.0, 0.02))
    with monitor_log() as log:
        reports = drive(monitor, ramp(3.0, 0.0, 0.02))

    assert states(reports) == [PlatformState.OFF, PlatformState.ENTERING, PlatformState.ON]
    on = [r for r in reports if r.state is PlatformState.ON][0]
    assert on.along_track_m <= cfg.edge_ahead_m - cfg.edge_uncertainty_m + 1e-9
    assert [line.split()[1] for line in platform_lines(log.lines)] == ["state=ENTERING", "state=ON"]
    assert "dir=-1" in platform_lines(log.lines)[0]


@pytest.mark.parametrize("edge", ["lower", "upper"])
def test_noise_around_a_window_edge_does_not_chatter(edge):
    cfg = config()
    s_star = edge_step(cfg)
    lower = s_star - cfg.edge_uncertainty_m - cfg.guard_lead_m
    upper = s_star + cfg.edge_uncertainty_m + cfg.guard_extent_m
    boundary = lower if edge == "lower" else upper
    monitor = PlatformMonitor(cfg)
    drive(monitor, ramp(0.0, boundary - 0.1, 0.02))

    with monitor_log() as log:
        drive(monitor, ramp(boundary - 0.1, boundary, 0.02))
        noise = [boundary + (0.04 if i % 2 else -0.04) for i in range(60)]
        drive(monitor, noise)

    assert len(platform_lines(log.lines)) == 1


def test_the_edge_step_moves_out_with_height_and_beam_width():
    low = PlatformMonitor(config()).update(0.0, 0.5, DT).edge_step_m
    high = PlatformMonitor(config()).update(0.0, 1.5, DT).edge_step_m
    wide = PlatformMonitor(config(sonar_half_angle_deg=35.0)).update(0.0, 0.5, DT).edge_step_m

    assert high > low
    assert wide > low
    assert low == pytest.approx(1.0 + 0.5 * math.tan(math.radians(20.0)))


def test_the_height_is_floored_at_thirty_centimetres():
    report = PlatformMonitor(config()).update(0.0, 0.05, DT)
    assert report.edge_step_m == pytest.approx(1.0 + 0.3 * math.tan(math.radians(20.0)))


@pytest.mark.parametrize(
    "along, height",
    [(None, H), (math.nan, H), (math.inf, H), (0.5, math.nan), (0.5, -math.inf)],
)
def test_missing_or_non_finite_inputs_read_unknown(along, height):
    monitor = PlatformMonitor(config())
    drive(monitor, ramp(0.0, 1.0, 0.02))

    report = monitor.update(along, height, DT)

    assert report.state is PlatformState.UNKNOWN
    assert report.guard_active is False


def test_no_edge_distance_means_no_guard():
    monitor = PlatformMonitor(config(edge_ahead_m=None))
    reports = drive(monitor, ramp(0.0, 3.0, 0.02))
    assert all(r.state is PlatformState.UNKNOWN and not r.guard_active for r in reports)
    assert all(r.edge_step_m is None for r in reports)


def test_slow_motion_still_resolves_a_direction():
    """0.05 m/s sampled at 15 Hz moves 3 mm per sample; the direction still resolves."""
    monitor = PlatformMonitor(config())
    reports = [monitor.update(0.0 + 0.05 * k / 15.0, H, 1.0 / 15.0) for k in range(60)]
    assert reports[-1].direction == 1


def test_sub_threshold_jitter_never_sets_a_direction():
    monitor = PlatformMonitor(config())
    reports = drive(monitor, [0.5 + (0.015 if i % 2 else -0.015) for i in range(50)])
    assert all(r.direction == 0 for r in reports)


def test_seconds_since_transition_accumulates():
    monitor = PlatformMonitor(config())
    first = monitor.update(0.0, H, DT)
    assert first.seconds_since_transition == pytest.approx(0.0)
    later = drive(monitor, [0.0] * 5)[-1]
    assert later.seconds_since_transition == pytest.approx(5 * DT)


def test_an_override_stands_in_for_the_odometry_until_its_ttl_expires():
    monitor = PlatformMonitor(config())
    monitor.override_along_track(0.1, ttl_sec=0.5)

    held = drive(monitor, [None] * 5)
    assert all(r.state is PlatformState.ON for r in held)
    assert all(r.along_track_m == pytest.approx(0.1) for r in held)

    expired = monitor.update(None, H, DT)
    assert expired.state is PlatformState.UNKNOWN


def test_reset_returns_to_unknown():
    monitor = PlatformMonitor(config())
    drive(monitor, ramp(0.0, 1.0, 0.02))
    monitor.reset()
    assert monitor.report.state is PlatformState.UNKNOWN


# --------------------------------------------------------------- supervisor


def pad_supervisor(edge: float = 1.0) -> OdometrySupervisor:
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.8
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = edge
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    for _ in range(params.calibration.min_ground_samples):
        odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.0)
    assert odom.calibrate_ground_reference()
    return odom


def test_the_along_track_position_waits_for_the_hover_origin():
    odom = pad_supervisor()
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8)
    snap = odom.snapshot()
    assert snap.along_track_m is None
    assert snap.platform_state == "unknown"
    assert snap.guard_active is False


def test_the_supervisor_tracks_the_departure_from_the_frozen_origin():
    odom = pad_supervisor()
    odom.inject_synthetic_sample(x=0.0, y=0.2, z=0.8)
    odom.freeze_hover_takeoff_origin()

    odom.inject_synthetic_sample(x=0.0, y=0.2, z=0.8)
    assert odom.snapshot().along_track_m == pytest.approx(0.0)
    assert odom.snapshot().platform_state == "on"

    for k in range(1, 80):
        odom.inject_synthetic_sample(x=0.0, y=0.2 + 0.02 * k, z=0.8)
        if odom.snapshot().platform_state == "leaving":
            break
    snap = odom.snapshot()
    assert snap.platform_state == "leaving"
    assert snap.guard_active is True
    assert odom.platform_report().guard_active is True
    assert snap.along_track_m == pytest.approx(0.02 * k)


def test_a_marker_position_overrides_a_drifted_odometry():
    """Stage 5 sees the pad marker; its offset stands in for an along-track estimate gone stale."""
    odom = pad_supervisor()
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8)
    odom.freeze_hover_takeoff_origin()
    for k in range(200):
        odom.inject_synthetic_sample(x=0.0, y=0.02 * k, z=0.8)
    for k in range(100):
        odom.inject_synthetic_sample(x=0.0, y=4.0 - 0.02 * k, z=0.8)
    assert odom.snapshot().platform_state == "entering"

    odom.override_along_track(0.1, ttl_sec=0.5)
    odom.inject_synthetic_sample(x=0.0, y=2.0, z=0.8)

    snap = odom.snapshot()
    assert snap.along_track_m == pytest.approx(0.1)
    assert snap.platform_state == "on"


# ------------------------------------------------- re-entry false climb over the pad edge


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import types

    from mvp_mission_bebop.telemetry import odometry as odometry_module

    fake = _Clock()
    monkeypatch.setattr(odometry_module, "time", types.SimpleNamespace(monotonic=fake.monotonic))
    return fake


def reentry_supervisor(clock, grace: float = 3.0, edge=0.30) -> OdometrySupervisor:
    """The measured pad: 1.0 m high, edge 0.30 m to the left, venue ceiling 2.50 m over the floor."""
    params = MissionParameters()
    params.kinematics.target_altitude_m = 1.8
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.height_m = 1.0
    params.platform.edge_ahead_m = edge
    params.platform.venue_ceiling_agl_m = 2.5
    params.platform.ceiling_allowance_m = 0.4
    params.platform.reentry_breach_grace_sec = grace
    params.calibration.altitude_probe_enabled = False
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)

    def sample(y: float, z: float) -> None:
        clock.now += 0.1
        odom.inject_synthetic_sample(x=0.0, y=y, z=z)

    for _ in range(params.calibration.min_ground_samples):
        sample(0.0, 0.0)
    assert odom.calibrate_ground_reference()
    sample(0.0, 0.8)
    odom.freeze_hover_takeoff_origin()
    for k in range(126):
        sample(0.02 * k, 0.8)
    for k in range(76):
        sample(2.5 - 0.02 * k, 0.8)
    odom.sample = sample
    return odom


def climb_over_the_ceiling(odom, y: float = 1.0, z: float = 1.6, samples: int = 4) -> None:
    for _ in range(samples):
        odom.sample(y, z)


def test_a_re_entry_breach_does_not_land_the_aircraft_inside_the_grace(clock):
    odom = reentry_supervisor(clock)
    assert odom.snapshot().platform_state == "entering"
    assert odom.altitude_ceiling == pytest.approx(1.5)

    climb_over_the_ceiling(odom)

    assert odom.ceiling_breach_streak >= 3
    assert odom.is_ceiling_breached() is False
    assert odom.reentry_descent_demanded() is True


def test_a_re_entry_breach_that_outlasts_the_grace_lands_the_aircraft(clock):
    odom = reentry_supervisor(clock, grace=3.0)
    climb_over_the_ceiling(odom)
    assert odom.is_ceiling_breached() is False

    climb_over_the_ceiling(odom, samples=30)

    assert odom.is_ceiling_breached() is True


def test_back_under_the_ceiling_the_grace_restarts(clock):
    odom = reentry_supervisor(clock, grace=1.0)
    climb_over_the_ceiling(odom, samples=8)
    climb_over_the_ceiling(odom, z=1.2, samples=2)
    assert odom.reentry_descent_demanded() is False

    climb_over_the_ceiling(odom, samples=8)

    assert odom.is_ceiling_breached() is False


def test_leaving_entering_while_still_over_the_ceiling_lands_the_aircraft(clock):
    odom = reentry_supervisor(clock, grace=100.0)
    climb_over_the_ceiling(odom)
    for k in range(60):
        odom.sample(1.0 - 0.02 * k, 1.6)
    assert odom.snapshot().platform_state == "on"

    assert odom.is_ceiling_breached() is True
    assert odom.reentry_descent_demanded() is False


def test_a_breach_off_the_pad_still_lands_the_aircraft(clock):
    odom = reentry_supervisor(clock)
    for k in range(60):
        odom.sample(1.0 + 0.04 * k, 0.8)
    assert odom.snapshot().platform_state == "off"

    climb_over_the_ceiling(odom, y=3.4)

    assert odom.is_ceiling_breached() is True
    assert odom.reentry_descent_demanded() is False


def test_without_an_edge_distance_a_breach_lands_the_aircraft_as_before(clock):
    odom = reentry_supervisor(clock, edge=None)
    climb_over_the_ceiling(odom)
    assert odom.is_ceiling_breached() is True
    assert odom.reentry_descent_demanded() is False
