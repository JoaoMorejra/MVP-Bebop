"""Stage 4 framing law: the gimbal alone points the optical axis at the target.

In the lateral flight pattern the airframe never advances on the target, so the
ground range ``R`` is constant through Stage 4 and no fixed nadir attitude frames
it in general. The law nulls the vertical pixel error with the tilt alone,
``tilt_desired = tilt_current - theta_y``, rate-limited by
``gimbal.max_slew_limit_deg`` and bounded to ``[nadir_tilt_deg, search_tilt_deg]``.

The closed-loop cases use the Stage 3 scene, which renders the target's pixel from
the current tilt with the exact pinhole model, so they pin the sign convention of
``CameraIntrinsics.bearing`` as well as the law itself.
"""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers import FramingCommand, GimbalFramingController
from mvp_mission_bebop.parameters import GimbalConstraintsConfig, MissionParameters, VisionConfig
from test_stage3_integration import FRAME, HEIGHT, WIDTH, Scene

DT = 1.0 / 16.0
ALTITUDE_M = 1.8


def build_controller(gimbal=None, vision=None):
    return GimbalFramingController(gimbal or GimbalConstraintsConfig(), vision or VisionConfig())


def tolerance_px(vision=None):
    vision = vision or VisionConfig()
    return max(vision.optical_center_tolerance_px, vision.optical_center_tolerance_ratio * WIDTH)


def fixed_point_deg(ground_range_m, altitude_m=ALTITUDE_M):
    return -math.degrees(math.atan(altitude_m / ground_range_m))


def fly(controller, scene, tilt_deg, max_cycles=400):
    """Iterate the law against the scene until it reports the target framed."""
    trace = []
    for _ in range(max_cycles):
        command = controller.compute(scene.pixel(tilt_deg), FRAME, tilt_deg, DT)
        trace.append((tilt_deg, command))
        tilt_deg = command.tilt_deg
        if command.framed:
            break
    return tilt_deg, trace


def test_a_target_below_the_axis_pitches_the_gimbal_down():
    current = -30.0

    below = build_controller().compute((WIDTH / 2.0, HEIGHT / 2.0 + 60.0), FRAME, current, DT)
    above = build_controller().compute((WIDTH / 2.0, HEIGHT / 2.0 - 60.0), FRAME, current, DT)

    assert isinstance(below, FramingCommand)
    assert below.vertical_error_px > 0.0 and below.tilt_deg < current
    assert above.vertical_error_px < 0.0 and above.tilt_deg > current


def test_closed_loop_converges_on_a_simulated_target():
    scene = Scene(4.0, altitude_m=ALTITUDE_M)
    controller = build_controller()

    tilt, trace = fly(controller, scene, -10.0)
    final = trace[-1][1]

    assert final.framed, "the law never framed a target it could see"
    assert not final.at_floor
    assert abs(final.vertical_error_px) <= tolerance_px()
    assert abs(scene.pixel(tilt)[1] - HEIGHT / 2.0) <= tolerance_px()
    assert tilt == pytest.approx(fixed_point_deg(4.0), abs=1.0)


def test_the_slew_limit_is_respected():
    gimbal = GimbalConstraintsConfig()
    scene = Scene(1.5, altitude_m=ALTITUDE_M)
    controller = build_controller(gimbal)
    tilt = -30.0
    steps = []

    for _ in range(200):
        command = controller.compute(scene.pixel(tilt), FRAME, tilt, DT)
        steps.append(abs(command.tilt_deg - tilt))
        tilt = command.tilt_deg

    max_step = gimbal.max_slew_limit_deg * DT
    assert all(step <= max_step + 1e-9 for step in steps)
    assert max(steps) == pytest.approx(max_step), "the scenario never exercised the slew limit"
    assert tilt == pytest.approx(fixed_point_deg(1.5), abs=1.0)


def test_the_gimbal_never_leaves_its_band():
    gimbal = GimbalConstraintsConfig()
    scene = Scene(0.6, altitude_m=ALTITUDE_M)
    assert fixed_point_deg(0.6) < gimbal.nadir_tilt_deg, "the scenario must lie below the floor"

    tilt, trace = fly(build_controller(gimbal), scene, -50.0)
    final = trace[-1][1]

    assert all(
        gimbal.nadir_tilt_deg <= command.tilt_deg <= gimbal.search_tilt_deg
        for _tilt, command in trace
    )
    assert tilt == pytest.approx(gimbal.nadir_tilt_deg)
    assert final.at_floor is True
    assert final.framed is True
    assert 0.0 <= scene.pixel(tilt)[1] <= HEIGHT, "framed at the floor with the target out of view"


def test_no_target_holds_the_tilt():
    command = build_controller().compute(None, FRAME, -37.5, DT)

    assert command.tilt_deg == -37.5
    assert command.framed is False
    assert math.isnan(command.vertical_error_px)


def test_a_frame_without_the_target_restarts_the_dwell():
    vision = VisionConfig()
    dwell = max(1, int(vision.alignment_dwell_cycles))
    controller = build_controller(vision=vision)
    centred = (WIDTH / 2.0, HEIGHT / 2.0)

    for _ in range(dwell - 1):
        assert not controller.compute(centred, FRAME, -30.0, DT).framed
    controller.compute(None, FRAME, -30.0, DT)
    for _ in range(dwell - 1):
        assert not controller.compute(centred, FRAME, -30.0, DT).framed
    assert controller.compute(centred, FRAME, -30.0, DT).framed


@pytest.mark.parametrize("ground_range_m", [2.5, 4.0])
def test_open_loop_tilt_matches_the_closed_loop_fixed_point(ground_range_m):
    scene = Scene(ground_range_m, altitude_m=ALTITUDE_M)
    search_tilt = GimbalConstraintsConfig().search_tilt_deg
    controller = build_controller()

    open_loop = controller.open_loop_tilt(scene.pixel(search_tilt), search_tilt, FRAME)
    closed_loop, _trace = fly(build_controller(), scene, search_tilt)

    assert open_loop == pytest.approx(fixed_point_deg(ground_range_m), abs=1.0)
    assert open_loop == pytest.approx(closed_loop, abs=1.0)


def test_the_inspection_stage_points_the_gimbal_by_the_framing_law():
    """Stage 4 frames inside ``[nadir_tilt_deg, search_tilt_deg]``; it no longer
    re-asserts ``nadir_tilt_deg`` as a fixed attitude.

    Replaces the forward-pattern pin that Stage 4 commanded the attitude Stage 3
    had stopped at: a target on the optical axis now leaves the tilt where it is.
    """
    params = MissionParameters()
    gimbal = params.gimbal
    controller = GimbalFramingController(gimbal, params.vision)

    on_axis = controller.compute((WIDTH / 2.0, HEIGHT / 2.0), FRAME, -35.0, DT)
    assert on_axis.tilt_deg == pytest.approx(-35.0)
    assert on_axis.tilt_deg != gimbal.nadir_tilt_deg

    for tilt in (gimbal.search_tilt_deg, -35.0, -50.0, gimbal.nadir_tilt_deg):
        for row in (0.0, HEIGHT / 4.0, HEIGHT / 2.0, 3.0 * HEIGHT / 4.0, float(HEIGHT)):
            controller.reset()
            command = controller.compute((WIDTH / 2.0, row), FRAME, tilt, DT)
            assert gimbal.nadir_tilt_deg <= command.tilt_deg <= gimbal.search_tilt_deg
            open_loop = controller.open_loop_tilt((WIDTH / 2.0, row), tilt, FRAME)
            assert gimbal.nadir_tilt_deg <= open_loop <= gimbal.search_tilt_deg


def test_invalid_inputs_raise():
    controller = build_controller()
    centred = (WIDTH / 2.0, HEIGHT / 2.0)

    with pytest.raises(ValueError):
        controller.compute(centred, FRAME, -30.0, 0.0)
    with pytest.raises(ValueError):
        controller.compute(centred, FRAME, -30.0, -DT)
    with pytest.raises(ValueError):
        controller.compute(centred, (0, 480), -30.0, DT)
    with pytest.raises(ValueError):
        controller.compute((float("nan"), 240.0), FRAME, -30.0, DT)
    with pytest.raises(ValueError):
        controller.compute(centred, FRAME, float("nan"), DT)
    with pytest.raises(ValueError):
        controller.compute(centred, FRAME, -30.0, float("nan"))
    with pytest.raises(ValueError):
        controller.open_loop_tilt((428.0, float("inf")), -20.0, FRAME)
    with pytest.raises(ValueError):
        controller.open_loop_tilt(centred, -20.0, (856, 0))
    with pytest.raises(TypeError):
        controller.compute("ab", FRAME, -30.0, DT)
    with pytest.raises(TypeError):
        controller.compute((True, 240.0), FRAME, -30.0, DT)
    with pytest.raises(TypeError):
        controller.compute((428.0,), FRAME, -30.0, DT)


def test_slew_toward_respects_the_rate_and_the_band():
    gimbal = GimbalConstraintsConfig()
    controller = build_controller(gimbal)
    max_step = gimbal.max_slew_limit_deg * DT

    assert controller.slew_toward(-30.0, -60.0, DT) == pytest.approx(-30.0 - max_step)
    assert controller.slew_toward(-60.0, -30.0, DT) == pytest.approx(-60.0 + max_step)
    assert controller.slew_toward(-30.0, -30.5, DT) == pytest.approx(-30.5)
    assert controller.slew_toward(gimbal.nadir_tilt_deg + 1.0, -90.0, DT) == pytest.approx(
        gimbal.nadir_tilt_deg
    )
    assert controller.slew_toward(gimbal.search_tilt_deg - 1.0, 0.0, DT) == pytest.approx(
        gimbal.search_tilt_deg
    )

    for bad_dt in (0.0, -DT, float("nan")):
        with pytest.raises(ValueError):
            controller.slew_toward(-30.0, -40.0, bad_dt)
    with pytest.raises(ValueError):
        controller.slew_toward(float("nan"), -40.0, DT)
    with pytest.raises(ValueError):
        controller.slew_toward(-30.0, float("inf"), DT)
