"""Regressions from the Stage 3 centering deadlock that still bind the lateral servo.

On 2026-09-12 the airframe confirmed a bicycle, entered visual servoing, and
hovered until the stage deadline expired. Part of that failure belonged to the
forward approach, which the lateral flight pattern no longer flies; the tests
for it were retired with it. The part that remains is the cross-track actuator:
the driver quantizes to ``int8(v * 100)``, so a lateral demand under the
airframe's response threshold arrived as nothing and the lateral loop
asymptoted into the dead zone instead of reaching the centring gate. The lateral
servo is now the whole of Stage 3, so that property is pinned here.

The freeze is held off with ``airframe_at_rest=False`` so that the lateral
channel can be observed for as long as each test needs.
"""

from __future__ import annotations

import pytest

from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)

#: The mission's real cadence and capture size.
DT = 1.0 / 16.0
FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
CENTER_X, CENTER_Y = WIDTH / 2.0, HEIGHT / 2.0
SEARCH_TILT_DEG = -20.0



@pytest.fixture(autouse=True)
def _lens_frame_centering(monkeypatch):
    """Pin the centering law in the lens frame (camera-to-CoG offset zero).

    These tests simulate the marker relative to the camera and exercise the
    law's dynamics, gates and recovery; the camera-to-CoG extrinsic that
    shifts the equilibrium is covered by ``test_camera_extrinsics.py``.
    """
    from mvp_mission_bebop.controllers import rtl_guidance as _rtl_guidance

    monkeypatch.setattr(_rtl_guidance, "_camera_offset", lambda _cfg, _name: 0.0)

def build_controller(**vision_overrides):
    vision = VisionConfig(**vision_overrides)
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=vision,
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def lateral(controller, px_x):
    return controller.compute(
        (px_x, CENTER_Y), FRAME, SEARCH_TILT_DEG, 1.23, DT, airframe_at_rest=False
    ).vy


# ------------------------------------------------- cross-track actuator floor


def test_lateral_commands_clear_the_driver_truncation_threshold():
    """``min_effective_velocity`` was declared and read by nothing.

    The Bebop driver quantizes to ``int8(v * 100)``. A demand of 0.029 -- what a
    35 px offset produces at the configured gain -- survives as two counts, below
    the airframe's response threshold, so the cross-track error stopped falling
    while still outside the tolerance that gated the phase exit.
    """
    controller = build_controller()
    floor = LateralPIDConfig().min_effective_velocity

    commands = [lateral(controller, CENTER_X + 8.0) for _ in range(60)]

    nonzero = [v for v in commands if v != 0.0]
    assert nonzero, "a sub-floor demand must still be delivered, as pulses"
    assert all(abs(v) >= floor - 1e-9 for v in nonzero), (
        "every emitted command must clear the driver's truncation threshold"
    )
    assert all(v < 0.0 for v in nonzero), "a target right of centre must drive right"


def test_shaped_lateral_mean_tracks_the_demand():
    """The floor is a duty cycle, not an amplitude: the mean must stay faithful."""
    controller = build_controller()

    commands = [lateral(controller, CENTER_X + 8.0) for _ in range(400)]

    mean = sum(commands) / len(commands)
    demand = -LateralPIDConfig().kp * (8.0 / CENTER_X)
    assert mean == pytest.approx(demand, abs=0.01)
    assert abs(mean) < LateralPIDConfig().min_effective_velocity, (
        "a floor applied as an amplitude would have pinned the mean at 0.06"
    )


# ---------------------------------------------------- alignment diagnostic


def test_the_alignment_diagnostic_tapers_with_cross_track_error():
    """Reported on the annotated stream; graded rather than a switch."""
    controller = build_controller()
    vision = VisionConfig()
    # One cycle first: the tolerance is derived from the frame width, so it is
    # only resolved once the controller has seen a frame.
    lateral(controller, CENTER_X + 300.0)
    tolerance = controller._tolerance_px
    corridor = tolerance * vision.approach_corridor_ratio

    gains = [controller._alignment_gain(offset) for offset in (0.0, tolerance, corridor - 1.0)]

    assert gains[0] == 1.0
    assert gains[1] == 1.0
    assert 0.0 < gains[2] < 1.0, "the taper must be graded, not a switch"
    assert gains[2] >= vision.min_alignment_gain
    assert controller._alignment_gain(corridor + 1.0) == 0.0
    assert gains == sorted(gains, reverse=True), "gain must fall monotonically with error"
