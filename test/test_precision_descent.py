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
    for _ in range(100):
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
    command = ctrl.update(obs(cfg, 0.09, 0.0, h), None, DT, tilt_deg=TILT)
    assert command.vz_mps == 0.0
    assert command.phase is DescentPhase.ALIGN
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
    for _ in range(int(cfg.descent_marker_lost_hold_sec / DT) + 3):
        command = ctrl.update(None, h, DT, tilt_deg=TILT)
    assert command.phase is DescentPhase.COMMIT
    assert command.commit is True
    assert command.vz_mps == 0.0


def test_marker_lost_below_commit_commits_with_last_good_error() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.45, error=0.04)
    assert not command.commit
    for _ in range(int(cfg.descent_marker_lost_hold_sec / DT) + 3):
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


def test_wrong_marker_id_never_counts() -> None:
    ctrl, cfg = make()
    h, command = descend_to(ctrl, cfg, 1.1, 0.9)
    rejected = MarkerObservation.from_pose_estimate(7, (0.0, 0.0, 1.0), None, expected_id=8)
    assert rejected is None
    before = command
    command = ctrl.update(rejected, None, DT, tilt_deg=TILT)
    assert command.height_m == before.height_m
    assert command.phase is before.phase
    assert command.vz_mps == 0.0


def test_marker_sighting_then_loss_commits_immediately() -> None:
    ctrl, cfg = make()
    # Sighting establishes height and position
    command = ctrl.update(obs(cfg, 0.08, 0.0, 0.9), None, DT, tilt_deg=TILT)
    assert command.height_m is not None
    # Subsequent loss must commit immediately after the debounce hold
    for _ in range(int(cfg.descent_marker_lost_hold_sec / DT) + 2):
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
