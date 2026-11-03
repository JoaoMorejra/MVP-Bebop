"""Advanced return at -70 deg: visibility stand-off, tracked descent and blind closure.

The Simple return keeps ``rtl.camera_tilt_deg`` and the -80 deg law unchanged;
these tests pin both sides of that split.
"""

from __future__ import annotations

import math
import types
from typing import Optional, Tuple

import pytest

from mvp_mission_bebop.controllers.precision_descent import PrecisionDescentController
from mvp_mission_bebop.controllers.rtl_guidance import (
    ADVANCED_RETURN_TILT_DEG,
    FRAME_BOTTOM_TAN,
    QUIET_ZONE_HALF_FRACTION,
    ArucoCenteringController,
    MarkerObservation,
    project_marker_to_body,
    visibility_standoff_m,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.parameters import FlightKinematicsConfig, MissionParameters, ReturnToLaunchConfig
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep

DT = 1.0 / 15.0


def sighting(cfg: ReturnToLaunchConfig, ex: float, ey: float, h: float, tilt: float) -> MarkerObservation:
    """Camera-frame sighting of a marker at CoG error ``(ex, ey)`` and lens height ``h``."""
    t = math.radians(abs(tilt))
    s = ex - cfg.camera_offset_forward_m
    z = math.cos(t) * s + math.sin(t) * h
    y = -math.sin(t) * s + math.cos(t) * h
    x = cfg.camera_offset_left_m - ey
    return MarkerObservation(marker_id=cfg.target_aruco_id, x_cam_m=x, y_cam_m=y, z_cam_m=z)


def advanced_law(cfg: Optional[ReturnToLaunchConfig] = None) -> ArucoCenteringController:
    return ArucoCenteringController(
        cfg or ReturnToLaunchConfig(),
        FlightKinematicsConfig(),
        SpeedCalibration(1.0),
        tilt_deg=ADVANCED_RETURN_TILT_DEG,
        standoff=True,
    )


# ---------------------------------------------------------------- geometry


def test_standoff_is_zero_at_minus_80_over_the_centring_height():
    cfg = ReturnToLaunchConfig()
    for h in (0.8, 1.0, 1.5):
        assert visibility_standoff_m(h, -80.0, cfg.tag_size, cfg.camera_offset_forward_m) == 0.0


def test_standoff_at_minus_70_matches_the_closed_form():
    cfg = ReturnToLaunchConfig()
    lowest = math.radians(70.0 + math.degrees(math.atan(FRAME_BOTTOM_TAN)) - 2.0)
    for h in (0.4, 1.0):
        expected = cfg.camera_offset_forward_m + QUIET_ZONE_HALF_FRACTION * cfg.tag_size + h / math.tan(lowest)
        assert visibility_standoff_m(h, -70.0, cfg.tag_size, cfg.camera_offset_forward_m) == pytest.approx(expected)
    assert visibility_standoff_m(1.0, -70.0, 0.20, 0.04) == pytest.approx(0.127, abs=2e-3)
    assert visibility_standoff_m(0.4, -70.0, 0.20, 0.04) == pytest.approx(0.153, abs=2e-3)


def test_standoff_grows_as_the_airframe_descends_at_minus_70():
    values = [visibility_standoff_m(h, -70.0, 0.20, 0.04) for h in (4.0, 2.0, 1.5, 1.0, 0.6, 0.4, 0.2)]
    assert values == sorted(values)
    assert values[0] == 0.0 and values[1] > 0.0


@pytest.mark.parametrize("h", [0.3, 0.6, 1.0, 1.5])
def test_marker_at_the_standoff_keeps_its_quiet_zone_framed(h: float):
    cfg = ReturnToLaunchConfig()
    standoff = visibility_standoff_m(h, -70.0, cfg.tag_size, cfg.camera_offset_forward_m)
    near_edge = sighting(cfg, standoff - QUIET_ZONE_HALF_FRACTION * cfg.tag_size, 0.0, h, -70.0)
    assert near_edge.y_cam_m / near_edge.z_cam_m <= FRAME_BOTTOM_TAN
    under_cog = sighting(cfg, -QUIET_ZONE_HALF_FRACTION * cfg.tag_size, 0.0, 1.0, -70.0)
    assert under_cog.y_cam_m / under_cog.z_cam_m > FRAME_BOTTOM_TAN


@pytest.mark.parametrize(
    "args",
    [(float("nan"), -70.0, 0.2, 0.0), (1.0, -70.0, 0.0, 0.0), (1.0, -170.0, 0.2, 0.0), (1.0, True, 0.2, 0.0)],
)
def test_standoff_rejects_invalid_inputs(args):
    with pytest.raises(ValueError):
        visibility_standoff_m(*args)


def test_projection_inverts_the_test_sighting_at_minus_70():
    cfg = ReturnToLaunchConfig()
    obs = sighting(cfg, 0.21, -0.07, 1.0, -70.0)
    ex, ey = project_marker_to_body(
        obs.x_cam_m, obs.y_cam_m, obs.z_cam_m, -70.0, cfg.camera_offset_forward_m, cfg.camera_offset_left_m
    )
    assert (ex, ey) == pytest.approx((0.21, -0.07))
    assert obs.height_above_marker_m(-70.0) == pytest.approx(1.0)


# ------------------------------------------------------------- centring law


def fly(law: ArucoCenteringController, cfg: ReturnToLaunchConfig, ex: float, ey: float, h: float, cycles: int = 900):
    """Ideal kinematics: the airframe moves exactly as commanded (calibration 1.0)."""
    command = None
    for _ in range(cycles):
        command = law.update(sighting(cfg, ex, ey, h, -70.0), DT, tilt_deg=-70.0)
        ex -= command.vx * DT
        ey -= command.vy * DT
        if command.settled:
            break
    return ex, ey, command


def test_advanced_law_settles_on_the_standoff_not_on_the_cog():
    cfg = ReturnToLaunchConfig()
    law = advanced_law(cfg)
    ex, ey, command = fly(law, cfg, 0.45, -0.30, 1.0)
    standoff = visibility_standoff_m(1.0, -70.0, cfg.tag_size, cfg.camera_offset_forward_m)
    assert command.settled
    assert command.standoff_m == pytest.approx(standoff)
    assert math.hypot(ex - standoff, ey) <= law.landing_radius_m


def test_advanced_law_never_drives_the_quiet_zone_out_of_frame():
    cfg = ReturnToLaunchConfig()
    law = advanced_law(cfg)
    ex, ey, h = 0.45, 0.0, 1.0
    for _ in range(900):
        obs = sighting(cfg, ex, ey, h, -70.0)
        near = sighting(cfg, ex - QUIET_ZONE_HALF_FRACTION * cfg.tag_size, ey, h, -70.0)
        assert near.y_cam_m / near.z_cam_m <= FRAME_BOTTOM_TAN + 1e-3
        command = law.update(obs, DT, tilt_deg=-70.0)
        ex -= command.vx * DT
        ey -= command.vy * DT
        if command.settled:
            break
    assert command.settled


def test_simple_law_is_unchanged_by_the_advanced_options():
    cfg = ReturnToLaunchConfig()
    default = ArucoCenteringController(cfg, FlightKinematicsConfig(), SpeedCalibration(1.0), simple=True)
    explicit = ArucoCenteringController(
        cfg, FlightKinematicsConfig(), SpeedCalibration(1.0), simple=True, tilt_deg=None, standoff=False
    )
    assert default.tilt_deg == cfg.camera_tilt_deg and not default.standoff_enabled
    for ex, ey in ((0.4, -0.2), (0.1, 0.05), (0.02, 0.0)):
        a = default.update(sighting(cfg, ex, ey, 1.0, -80.0), DT, tilt_deg=-80.0)
        b = explicit.update(sighting(cfg, ex, ey, 1.0, -80.0), DT, tilt_deg=-80.0)
        assert a == b
        assert a.standoff_m == 0.0


def test_constructor_rejects_a_non_finite_tilt():
    with pytest.raises(ValueError):
        ArucoCenteringController(ReturnToLaunchConfig(), FlightKinematicsConfig(), tilt_deg=float("inf"))
    with pytest.raises(TypeError):
        ArucoCenteringController(ReturnToLaunchConfig(), FlightKinematicsConfig(), tilt_deg="-70")


def test_tracked_descent_reaches_commit_on_the_standoff_at_minus_70():
    cfg = ReturnToLaunchConfig()
    law = advanced_law(cfg)
    descent = PrecisionDescentController(cfg, law)
    ex, ey, h = visibility_standoff_m(1.0, -70.0, cfg.tag_size, cfg.camera_offset_forward_m), 0.0, 1.0
    command = None
    for _ in range(3000):
        command = descent.update(sighting(cfg, ex, ey, h, -70.0), None, DT)
        ex -= command.vx * DT
        ey -= command.vy * DT
        h += command.vz_mps * DT
        if command.commit:
            break
    assert command is not None and command.commit
    assert h <= cfg.commit_height_m + 0.02
    standoff = visibility_standoff_m(h, -70.0, cfg.tag_size, cfg.camera_offset_forward_m)
    assert ex == pytest.approx(standoff, abs=cfg.commit_radius_m)


# ------------------------------------------------------------------- step


def _ctx(camera_tilt_deg: float = -80.0):
    params = MissionParameters()
    params.rtl.camera_tilt_deg = camera_tilt_deg
    tilts = []
    drone = types.SimpleNamespace(camera_control=lambda tilt, pan=0.0: tilts.append(tilt))
    ctx = types.SimpleNamespace(
        params=params, drone=drone, speed_calibration=SpeedCalibration(1.0), current_tilt_deg=None
    )
    return ctx, tilts


def test_advanced_step_commands_minus_70_regardless_of_rtl_camera_tilt():
    ctx, tilts = _ctx(camera_tilt_deg=-85.0)
    step = ClosedLoopRTLStep(mode=MissionMode.ADVANCED)
    step._assert_tilt(ctx)
    assert tilts == [ADVANCED_RETURN_TILT_DEG]
    assert ctx.current_tilt_deg == ADVANCED_RETURN_TILT_DEG
    centering = step._build_centering(ctx)
    assert centering.tilt_deg == ADVANCED_RETURN_TILT_DEG and centering.standoff_enabled


def test_advanced_flies_the_simple_centring_envelope():
    ctx, _ = _ctx()
    advanced = ClosedLoopRTLStep(mode=MissionMode.ADVANCED)._build_centering(ctx)
    simple = ClosedLoopRTLStep(mode=MissionMode.SIMPLE)._build_centering(ctx)
    assert advanced.max_speed_mps == pytest.approx(simple.max_speed_mps)
    assert advanced._forward_cap_mps == pytest.approx(simple._forward_cap_mps)
    assert advanced._overspeed_mps == pytest.approx(simple._overspeed_mps)
    assert advanced._overspeed_release_mps == pytest.approx(simple._overspeed_release_mps)
    assert advanced.tolerance_m == simple.tolerance_m and advanced.landing_radius_m == simple.landing_radius_m


def test_simple_step_keeps_rtl_camera_tilt_and_the_plain_law():
    ctx, tilts = _ctx(camera_tilt_deg=-85.0)
    step = ClosedLoopRTLStep(mode=MissionMode.SIMPLE)
    step._assert_tilt(ctx)
    assert tilts == [-85.0]
    assert ctx.current_tilt_deg == -85.0
    centering = step._build_centering(ctx)
    assert centering.tilt_deg == -85.0 and not centering.standoff_enabled
