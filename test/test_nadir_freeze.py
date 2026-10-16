"""The inspection attitude, and the lateral freeze that precedes it.

*The camera never goes past roughly -69 degrees by default.* In the lateral
flight pattern ``gimbal.nadir_tilt_deg`` is the steepest tilt Stage 4 may frame
with, not an attitude Stage 3 drives toward; the default is pinned here because
the rest of the geometry is stated against it.

*Centring laterally freezes the airframe.* Closed against the projection
geometry -- the observation is re-derived from the lateral offset every cycle --
so the freeze is only reached if the lateral commands genuinely carry the
airframe onto the target's bearing, and nothing translates afterwards. The
open-loop properties of the same law are pinned in ``test_lateral_servoing``.
"""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.visual_servoing import (
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    MissionParameters,
    VisionConfig,
)

DT = 1.0 / 16.0
FRAME = (856, 480)
WIDTH, HEIGHT = FRAME
CENTER_X, CENTER_Y = WIDTH / 2.0, HEIGHT / 2.0
SEARCH_TILT_DEG = -20.0
ALTITUDE_M = 1.55
#: Range the search tilt centres vertically at this altitude.
GROUND_RANGE_M = ALTITUDE_M / math.tan(math.radians(abs(SEARCH_TILT_DEG)))


def build_controller():
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=VisionConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def target_pixel(lateral_m, tilt_deg=SEARCH_TILT_DEG):
    """Inverse of ``project_to_ground`` at the constant range of the lateral pattern."""
    depression = math.atan2(ALTITUDE_M, GROUND_RANGE_M)
    theta_y = depression - math.radians(-tilt_deg)
    # Negated, matching ``project_to_ground``: positive lateral is to the left,
    # and a target to the left appears left of the optical axis.
    theta_x = math.atan2(-lateral_m, math.hypot(GROUND_RANGE_M, ALTITUDE_M))
    return (
        CENTER_X + CENTER_X * math.tan(theta_x) / math.tan(math.radians(40.0)),
        CENTER_Y + CENTER_Y * math.tan(theta_y) / math.tan(math.radians(25.0)),
    )


def fly_to_freeze(controller, lateral_m, cycles=1600):
    trace = []
    for _ in range(cycles):
        command = controller.compute(
            target_pixel(lateral_m), FRAME, SEARCH_TILT_DEG, ALTITUDE_M, DT
        )
        lateral_m -= command.vy * DT
        trace.append(command)
        if command.locked:
            break
    return trace, lateral_m


def test_the_configured_inspection_attitude_is_about_sixty_nine_degrees():
    """The default the rest of the geometry is stated against."""
    gimbal = MissionParameters().gimbal
    assert gimbal.nadir_tilt_deg == pytest.approx(-69.0, abs=0.5)
    assert gimbal.nadir_tilt_deg > -80.0, "the drone must not go all the way down to -80"


@pytest.mark.parametrize("lateral_m", [0.8, -0.8], ids=["target-left", "target-right"])
def test_a_closed_loop_lateral_offset_ends_in_a_latched_freeze(lateral_m):
    controller = build_controller()
    trace, residual_m = fly_to_freeze(controller, lateral_m)

    assert trace[-1].locked, "the lateral loop never centred the target"
    assert abs(residual_m) < abs(lateral_m)
    tolerance = max(
        VisionConfig().optical_center_tolerance_px,
        VisionConfig().optical_center_tolerance_ratio * WIDTH,
    )
    assert abs(target_pixel(residual_m)[0] - CENTER_X) <= tolerance
    assert all(command.vx == 0.0 for command in trace)
    assert all(command.tilt_deg == SEARCH_TILT_DEG for command in trace)

    for _ in range(200):
        command = controller.compute(
            target_pixel(residual_m), FRAME, SEARCH_TILT_DEG, ALTITUDE_M, DT
        )
        residual_m -= command.vy * DT
        assert command.vx == 0.0 and command.vy == 0.0, "the freeze must not resume translation"
        assert command.phase is TrackingPhase.LOCKED
