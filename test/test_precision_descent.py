"""Staged, marker-tracked descent from the centring height to the commit height."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import List, Optional, Tuple

import pytest

from mvp_mission_bebop.controllers.precision_descent import DescentPhase, PrecisionDescentController
from mvp_mission_bebop.controllers.rtl_guidance import ArucoCenteringController, MarkerObservation
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig

DT = 1.0 / 15.0
TILT = -80.0


def make(cfg: Optional[ReturnToLaunchConfig] = None) -> Tuple[PrecisionDescentController, ReturnToLaunchConfig]:
    cfg = cfg or ReturnToLaunchConfig()
    centering = ArucoCenteringController(cfg, FlightKinematicsConfig())
    return PrecisionDescentController(cfg, centering), cfg


def obs(cfg: ReturnToLaunchConfig, ex: float, ey: float, h: float, marker_id: int = 8) -> MarkerObservation:
    """Camera-frame sighting of a marker at body error ``(ex, ey)`` and height ``h``."""
    t = math.radians(abs(TILT))
    ex_cam = ex - cfg.camera_offset_forward_m
    z = math.cos(t) * ex_cam + math.sin(t) * h
    y = -math.sin(t) * ex_cam + math.cos(t) * h
    x = cfg.camera_offset_left_m - ey
    return MarkerObservation(marker_id=marker_id, x_cam_m=x, y_cam_m=y, z_cam_m=z)


def descend_to(ctrl: PrecisionDescentController, cfg: ReturnToLaunchConfig, h: float, floor: float, error: float = 0.0):
    command = None
    for _ in range(2000):
        command = ctrl.update(obs(cfg, error, 0.0, h), None, DT, tilt_deg=TILT)
        h += command.vz_mps * DT
        if h <= floor or command.commit:
            break
    return h, command


def test_height_is_read_from_the_marker() -> None:
    cfg = ReturnToLaunchConfig()
    assert obs(cfg, 0.02, 0.0, 0.9).height_above_marker_m(TILT) == pytest.approx(0.9)


def test_does_not_descend_until_gate_radius_is_met() -> None:
    ctrl, cfg = make()
    for _ in range(int(cfg.descent_pause_max_sec / DT) - 3):
        command = ctrl.update(obs(cfg, 0.10, 0.0, 1.0), None, DT, tilt_deg=TILT)
        assert command.phase is DescentPhase.ALIGN
        assert command.vz_mps == 0.0


def test_descends_with_decelerating_speed_to_commit_height() -> None:
    ctrl, cfg = make()
    h = 1.1
    speeds: List[Tuple[float, float]] = []
    committed_at = None
    for _ in range(3000):
        command = ctrl.update(obs(cfg, 0.0, 0.0, h), None, DT, tilt_deg=TILT)
        assert abs(command.vz_mps) <= cfg.descent_speed_mps + 1e-12
        assert command.vz_mps <= 0.0
        if command.commit:
            committed_at = h
            break
        speeds.append((h, -command.vz_mps))
        h += command.vz_mps * DT
    assert committed_at is not None and committed_at <= cfg.commit_height_m
    tail = [s for height, s in speeds if height <= cfg.commit_height_m + 0.3]
    assert tail
    assert all(b <= a + 1e-9 for a, b in zip(tail, tail[1:]))


def test_pauses_descent_when_error_grows() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.8)
    assert command.phase is DescentPhase.DESCEND
    descending = command.vz_mps
    command = ctrl.update(obs(cfg, 0.09, 0.0, h), None, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.ALIGN
    assert descending <= command.vz_mps <= 0.0
    for _ in range(60):
        command = ctrl.update(obs(cfg, 0.09, 0.0, h), None, DT, tilt_deg=TILT)
        if command.vz_mps == 0.0:
            break
    assert command.vz_mps == 0.0
    resumed = False
    for _ in range(60):
        command = ctrl.update(obs(cfg, 0.03, 0.0, h), None, DT, tilt_deg=TILT)
        if command.vz_mps < 0.0:
            resumed = True
            break
    assert resumed


def test_marker_lost_during_descent_commits_immediately() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.9)
    assert command.phase is DescentPhase.DESCEND
    command = ctrl.update(None, h, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.COMMIT
    assert command.commit is True
    assert command.vz_mps == 0.0


def test_marker_lost_below_commit_commits_with_last_good_error() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.45, error=0.04)
    assert not command.commit
    command = ctrl.update(None, None, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.COMMIT
    assert command.commit is True
    assert command.radial_error_m == pytest.approx(0.04, abs=0.005)


def test_marker_lost_before_descent_holds_height_in_align() -> None:
    ctrl, cfg = make()
    command = ctrl.update(None, 1.0, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.ALIGN
    assert command.commit is False
    assert command.vz_mps == 0.0


def test_ground_effect_scales_lateral_gain() -> None:
    cfg = ReturnToLaunchConfig()
    for h, expected_scale in ((0.8, 1.0), (0.5, cfg.ground_effect_gain_scale)):
        ctrl, _ = make(cfg)
        twin = ArucoCenteringController(cfg, FlightKinematicsConfig())
        for _ in range(50):
            sighting = obs(cfg, 0.20, 0.20, h)
            command = ctrl.update(sighting, None, DT, tilt_deg=TILT)
            raw = twin.update(sighting, DT, tilt_deg=TILT)
            if raw.vx != 0.0 and raw.vy != 0.0:
                break
        assert raw.vx != 0.0 and raw.vy != 0.0
        assert command.vx == pytest.approx(raw.vx * expected_scale)
        assert command.vy == pytest.approx(raw.vy * expected_scale)


def test_wrong_marker_id_is_a_loss_and_never_a_sideways_move() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.9)
    rejected = MarkerObservation.from_pose_estimate(7, (0.0, 0.0, 1.0), None, expected_id=8)
    assert rejected is None
    before = command
    command = ctrl.update(rejected, None, DT, tilt_deg=TILT)
    assert command.height_m == before.height_m
    assert command.commit
    assert (command.vx, command.vy, command.vz_mps) == (0.0, 0.0, 0.0)


def test_marker_sighting_then_loss_commits_immediately() -> None:
    ctrl, cfg = make()
    # Sighting establishes height and position
    command = ctrl.update(obs(cfg, 0.08, 0.0, 0.9), None, DT, tilt_deg=TILT)
    assert command.height_m is not None
    command = ctrl.update(None, None, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.COMMIT
    assert command.commit is True
    assert command.vz_mps == 0.0


def test_forward_surge_prevented_when_marker_in_lower_half() -> None:
    cfg = ReturnToLaunchConfig()
    centering = ArucoCenteringController(cfg, FlightKinematicsConfig())
    # Sighting where y_cam_m >= 0 (marker at or below optical center)
    sighting = MarkerObservation(marker_id=8, x_cam_m=0.10, y_cam_m=0.05, z_cam_m=0.95)
    command = centering.update(sighting, DT, tilt_deg=TILT)
    # Forward command vx must be clamped to 0 to prevent forward surge and quiet zone loss
    assert command.vx <= 0.0


def test_invalid_config_raises() -> None:
    for field, value in (("commit_height_m", 0.0), ("descent_speed_mps", 0.0), ("descent_gate_radius_m", -0.1)):
        cfg = replace(ReturnToLaunchConfig(), **{field: value})
        with pytest.raises(ValueError):
            PrecisionDescentController(cfg, ArucoCenteringController(ReturnToLaunchConfig(), FlightKinematicsConfig()))


def test_pause_budget_forces_the_descent_and_the_commit() -> None:
    ctrl, cfg = make()
    descended = False
    command = None
    h = 1.0
    for _ in range(int(20.0 / DT)):
        command = ctrl.update(obs(cfg, 0.30, 0.0, h), None, DT, tilt_deg=TILT)
        if command.vz_mps < 0.0:
            descended = True
            h += command.vz_mps * DT
        if command.commit:
            break
    assert descended
    assert command is not None and command.commit
    assert h <= cfg.commit_height_m + 0.02


def test_waiting_at_the_commit_height_is_bounded() -> None:
    ctrl, cfg = make()
    cycles = 0
    command = ctrl.update(obs(cfg, 0.30, 0.0, cfg.commit_height_m - 0.02), None, DT, tilt_deg=TILT)
    while not command.commit and cycles < 1000:
        command = ctrl.update(obs(cfg, 0.30, 0.0, cfg.commit_height_m - 0.02), None, DT, tilt_deg=TILT)
        cycles += 1
    assert command.commit
    assert cycles * DT <= cfg.descent_pause_max_sec + 4 * DT


def test_the_vertical_command_is_never_positive() -> None:
    import random

    rng = random.Random(7)
    for _ in range(20):
        ctrl, cfg = make()
        h = 1.2
        for _ in range(600):
            if rng.random() < 0.15:
                command = ctrl.update(None, h, DT, tilt_deg=TILT)
            else:
                command = ctrl.update(obs(cfg, rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), h), None, DT, tilt_deg=TILT)
            assert command.vz_mps <= 0.0
            h = max(0.05, h + command.vz_mps * DT)
            if command.commit:
                break


def test_a_loss_high_in_the_descent_commits_on_the_first_missed_frame() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.95)
    assert command.phase is DescentPhase.DESCEND
    command = ctrl.update(None, h, DT, tilt_deg=TILT)
    assert command.commit
    assert (command.vx, command.vy, command.vz_mps) == (0.0, 0.0, 0.0)


def test_a_marker_the_centring_sighted_is_not_waited_for_on_entry() -> None:
    cfg = ReturnToLaunchConfig()
    centering = ArucoCenteringController(cfg, FlightKinematicsConfig())
    centering.update(obs(cfg, 0.02, 0.0, 1.0), DT, tilt_deg=TILT)
    assert centering.last_radial_m is not None
    ctrl = PrecisionDescentController(cfg, centering)
    command = ctrl.update(None, 1.0, DT, tilt_deg=TILT)
    assert command.commit


def test_the_descent_never_steps_its_vertical_command() -> None:
    ctrl, cfg = make()
    jump = cfg.max_accel_mps2 * DT + 1e-9
    h, previous = 1.2, 0.0
    errors = [0.0] * 40 + [0.12] * 30 + [0.0] * 200
    for error in errors:
        command = ctrl.update(obs(cfg, error, 0.0, h), None, DT, tilt_deg=TILT)
        if command.commit:
            break
        assert abs(command.vz_mps - previous) <= jump
        previous = command.vz_mps
        h += command.vz_mps * DT


def test_first_missed_frame_below_the_low_height_commits() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, cfg.descent_lost_commit_height_m - 0.05)
    assert not command.commit
    command = ctrl.update(None, None, DT, tilt_deg=TILT)
    assert command.commit


def test_a_marker_never_sighted_commits_after_the_pause_budget() -> None:
    ctrl, cfg = make()
    command = None
    for _ in range(int(cfg.descent_pause_max_sec / DT) + 3):
        command = ctrl.update(None, 1.0, DT, tilt_deg=TILT)
        assert command.vz_mps == 0.0
    assert command is not None and command.commit
