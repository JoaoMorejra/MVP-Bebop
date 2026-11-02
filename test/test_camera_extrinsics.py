"""Camera-to-body extrinsics in the marker projection and the strict landing gate."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import cv2
import numpy as np
import pytest

from mvp_mission_bebop.controllers.rtl_guidance import (
    ArucoCenteringController,
    MarkerObservation,
    project_marker_to_body,
)
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig

DT = 1.0 / 15.0


def legacy(x: float, y: float, z: float, tilt: float) -> "tuple[float, float]":
    t = math.radians(abs(tilt))
    return z * math.cos(t) - y * math.sin(t), -x


def test_zero_offsets_reproduce_the_legacy_projection() -> None:
    rng = random.Random(7)
    for _ in range(200):
        x, y, z = rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.1, 3)
        tilt = rng.uniform(-90, 0)
        assert project_marker_to_body(x, y, z, tilt) == legacy(x, y, z, tilt)
        assert project_marker_to_body(x, y, z, tilt, 0.0, 0.0) == legacy(x, y, z, tilt)


@pytest.mark.parametrize("tilt, expected", [(80.0, 1.0 * math.cos(math.radians(80.0)) + 0.10), (90.0, 0.10)])
def test_marker_on_the_optical_axis_with_a_forward_offset(tilt: float, expected: float) -> None:
    ex, ey = project_marker_to_body(0.0, 0.0, 1.0, -tilt, offset_forward_m=0.10, offset_left_m=0.0)
    assert ex == pytest.approx(expected, abs=1e-9)
    assert ey == 0.0
    if tilt == 80.0:
        assert ex == pytest.approx(0.2736, abs=1e-4)


def test_lateral_offset_shifts_the_left_error() -> None:
    _, base = project_marker_to_body(0.02, 0.1, 1.0, -80.0)
    _, shifted = project_marker_to_body(0.02, 0.1, 1.0, -80.0, offset_left_m=0.05)
    assert shifted - base == pytest.approx(0.05)


def nadir_obs(ex_cam: float, ey_cam: float, h: float = 1.0) -> MarkerObservation:
    """Sighting at nadir of a marker ``ex_cam`` ahead and ``ey_cam`` left of the lens."""
    return MarkerObservation(marker_id=8, x_cam_m=-ey_cam, y_cam_m=-ex_cam, z_cam_m=h)


def test_a_forward_offset_drives_the_cog_over_the_marker() -> None:
    cfg = replace(ReturnToLaunchConfig(), camera_offset_forward_m=0.10, camera_tilt_deg=-89.9)
    controller = ArucoCenteringController(cfg, FlightKinematicsConfig())
    command = controller.update(nadir_obs(0.0, 0.0), DT, tilt_deg=-90.0)
    for _ in range(30):
        command = controller.update(nadir_obs(0.0, 0.0), DT, tilt_deg=-90.0)
    assert command.ex_body_m == pytest.approx(0.10)
    assert command.vx > 0.0
    lens = 0.0
    for _ in range(600):
        command = controller.update(nadir_obs(-lens, 0.0), DT, tilt_deg=-90.0)
        lens += controller.calibration.to_mps(command.vx) * DT
    assert -lens == pytest.approx(-0.10, abs=0.045)
    assert abs(command.ex_body_m) <= cfg.centering_tolerance_m


def test_a_left_offset_commands_leftward() -> None:
    cfg = replace(ReturnToLaunchConfig(), camera_offset_forward_m=0.0, camera_offset_left_m=0.12)
    controller = ArucoCenteringController(cfg, FlightKinematicsConfig())
    for _ in range(20):
        command = controller.update(nadir_obs(0.0, 0.0), DT, tilt_deg=-90.0)
    assert command.ey_body_m == pytest.approx(0.12)
    assert command.vy > 0.0


def test_height_above_marker() -> None:
    obs = MarkerObservation(marker_id=8, x_cam_m=0.03, y_cam_m=0.12, z_cam_m=1.1)
    assert obs.height_above_marker_m(-90.0) == pytest.approx(1.1)
    t = math.radians(80.0)
    assert obs.height_above_marker_m(-80.0) == pytest.approx(1.1 * math.sin(t) + 0.12 * math.cos(t))


def test_height_matches_a_marker_projected_by_opencv() -> None:
    """A 0.20 m pad 0.95 m below the lens and 0.07 m ahead, recovered by solvePnP."""
    tilt = math.radians(80.0)
    f = 600.0
    camera = np.array([[f, 0, 428.0], [0, f, 240.0], [0, 0, 1]], dtype=float)
    half = 0.10
    ahead, height = 0.07, 0.95
    offsets = ((half, half), (half, -half), (-half, -half), (-half, half))
    model = np.array([[dx, dy, 0.0] for dx, dy in offsets], dtype=float)
    pts_cam = []
    for dx, dy in offsets:
        bx, by, bz = ahead + dx, dy, -height
        pts_cam.append((-by, -math.sin(tilt) * bx - math.cos(tilt) * bz, math.cos(tilt) * bx - math.sin(tilt) * bz))
    image, _ = cv2.projectPoints(np.array(pts_cam, dtype=float), np.zeros(3), np.zeros(3), camera, None)
    ok, _, tvec = cv2.solvePnP(model, image.reshape(-1, 2), camera, None)
    assert ok
    obs = MarkerObservation(
        marker_id=8, x_cam_m=float(tvec[0][0]), y_cam_m=float(tvec[1][0]), z_cam_m=float(tvec[2][0])
    )
    assert obs.height_above_marker_m(-80.0) == pytest.approx(height, abs=1e-3)


def test_default_extrinsics_center_camera_over_marker() -> None:
    cfg = ReturnToLaunchConfig()
    assert cfg.camera_offset_forward_m == 0.04
    assert cfg.camera_offset_left_m == 0.0
    assert cfg.landing_radius_m == 0.06


@pytest.mark.parametrize(
    "value, error",
    [(float("nan"), ValueError), ("0.1", TypeError), (0.6, ValueError), (-0.51, ValueError), (True, TypeError)],
)
def test_invalid_offsets_raise(value: object, error: type) -> None:
    with pytest.raises(error):
        ArucoCenteringController(replace(ReturnToLaunchConfig(), camera_offset_forward_m=value), FlightKinematicsConfig())
    with pytest.raises(error):
        ArucoCenteringController(replace(ReturnToLaunchConfig(), camera_offset_left_m=value), FlightKinematicsConfig())


def stationary(radial_ex: float, cycles: int) -> bool:
    cfg = replace(ReturnToLaunchConfig(), camera_offset_forward_m=0.0)
    controller = ArucoCenteringController(cfg, FlightKinematicsConfig())
    settled = False
    for _ in range(cycles):
        command = controller.update(nadir_obs(radial_ex, 0.0), DT, tilt_deg=-90.0)
        settled = settled or command.settled
    return settled


def test_the_gate_refuses_a_stationary_eleven_centimetre_error() -> None:
    assert stationary(0.11, 100) is False


def test_the_gate_accepts_a_small_error_at_rest() -> None:
    cfg = replace(ReturnToLaunchConfig(), camera_offset_forward_m=0.0)
    controller = ArucoCenteringController(cfg, FlightKinematicsConfig())
    settled_at = None
    for k in range(40):
        command = controller.update(nadir_obs(0.03, 0.0), DT, tilt_deg=-90.0)
        if command.settled:
            settled_at = k + 1
            break
    assert settled_at is not None and settled_at <= cfg.centering_settle_cycles + 1


def test_tilt_override_replaces_the_configured_tilt() -> None:
    cfg = replace(ReturnToLaunchConfig(), camera_offset_forward_m=0.0)
    controller = ArucoCenteringController(cfg, FlightKinematicsConfig())
    obs = MarkerObservation(marker_id=8, x_cam_m=0.0, y_cam_m=0.0, z_cam_m=1.0)
    default = controller.update(obs, DT)
    assert default.ex_body_m == pytest.approx(math.cos(math.radians(80.0)))
    confirmed = controller.update(obs, DT, tilt_deg=-79.2)
    assert confirmed.ex_body_m == pytest.approx(math.cos(math.radians(79.2)))
    ignored = controller.update(obs, DT, tilt_deg=float("nan"))
    assert ignored.ex_body_m == pytest.approx(math.cos(math.radians(80.0)))
