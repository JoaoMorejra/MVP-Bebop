"""The inspection attitude, and the freeze that reaching it commits to.

Two requirements are pinned here and they are not the same requirement.

*The camera stops at roughly -69 degrees rather than -80.* The gimbal must not
be driven to the old attitude at all, and reaching the new one is what ends the
approach -- not a separately-chosen ground range that happens to be nearby.

*Reaching it freezes the airframe.* No further translation on either horizontal
axis until the return leg. Stage 4 opens by verifying motionlessness
statistically before it triggers a 14 MP capture, so anything the approach
leaves moving is time that verification spends waiting for a transient this
stage should have absorbed -- and the standoff is chosen so that translating
further would push the subject out from under an optical axis that has run out of
travel to follow it with.
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


def build_controller(**gimbal_overrides):
    gimbal = GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG, **gimbal_overrides)
    return VisualServoingController(
        gimbal_config=gimbal,
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=VisionConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def target_pixel(ground_range_m, tilt_deg, altitude_m, lateral_m=0.0):
    """Inverse of ``project_to_ground``: where a ground target appears in frame."""
    depression = math.atan2(altitude_m, max(ground_range_m, 1e-3))
    theta_y = depression - math.radians(-tilt_deg)
    # Negated, matching ``project_to_ground``: the ground frame has positive
    # lateral to the *left*, and a target to the left appears left of the
    # optical axis, i.e. at a pixel below the principal point in x.
    theta_x = math.atan2(-lateral_m, math.hypot(ground_range_m, altitude_m))
    return (
        CENTER_X + CENTER_X * math.tan(theta_x) / math.tan(math.radians(40.0)),
        CENTER_Y + CENTER_Y * math.tan(theta_y) / math.tan(math.radians(25.0)),
    )


def fly_to_freeze(controller, *, ground_range_m=6.0, lateral_m=0.0, altitude_m=ALTITUDE_M,
                  cycles=1400):
    """Close the loop until the controller freezes, or give up.

    The observation is re-derived from the geometry every cycle, so the only way
    this converges is if the commands the controller emits genuinely carry the
    airframe to the standoff.
    """
    tilt = SEARCH_TILT_DEG
    trace = []
    for _ in range(cycles):
        command = controller.compute(
            target_pixel(ground_range_m, tilt, altitude_m, lateral_m),
            FRAME,
            tilt,
            altitude_m,
            DT,
        )
        tilt = command.tilt_deg
        ground_range_m = max(0.0, ground_range_m - command.vx * DT)
        lateral_m -= command.vy * DT
        trace.append(command)
        if command.nadir_frozen:
            break
    return trace, ground_range_m, lateral_m, tilt


# --------------------------------------------------------------- the attitude


def test_the_configured_inspection_attitude_is_about_sixty_nine_degrees():
    """The requirement, stated as the number the rest of the geometry uses."""
    gimbal = MissionParameters().gimbal
    assert gimbal.nadir_tilt_deg == pytest.approx(-69.0, abs=0.5)
    assert gimbal.nadir_tilt_deg > -80.0, "the drone must not go all the way down to -80"


def test_the_gimbal_is_never_commanded_past_the_inspection_attitude():
    controller = build_controller()
    trace, _, _, _ = fly_to_freeze(controller)

    limit = GimbalConstraintsConfig().nadir_tilt_deg
    assert trace, "the approach emitted nothing"
    assert min(command.tilt_deg for command in trace) >= limit - 1e-9
    assert min(command.tilt_deg for command in trace) > -80.0


def test_reaching_the_attitude_is_what_ends_the_approach():
    """Not the ground range. The two used to have to agree and no longer can.

    At -80 the gimbal arrived with the subject a couple of tens of centimetres
    ahead, so a 0.25 m range gate described the same moment. At -69 it arrives
    with the subject 0.60 m ahead at the operating altitude, and insisting on
    0.25 m would fly the drone a third of a metre past the point where the
    camera can still follow the subject down.
    """
    controller = build_controller()
    trace, ground_range, _, tilt = fly_to_freeze(controller)

    assert trace[-1].nadir_frozen, "the approach never froze"
    assert controller.phase is TrackingPhase.NADIR
    assert tilt == pytest.approx(GimbalConstraintsConfig().nadir_tilt_deg, abs=0.5)
    assert ground_range > VisionConfig().nadir_range_threshold_m, (
        "the drone flew onto the target rather than stopping at the standoff"
    )


def test_the_stopping_point_is_the_standoff_the_attitude_frames():
    """``h / tan(69 deg)``: where the optical axis meets the ground."""
    controller = build_controller()
    _, ground_range, _, _ = fly_to_freeze(controller)

    standoff = controller.inspection_standoff_m(ALTITUDE_M)
    assert standoff == pytest.approx(0.60, abs=0.02)
    assert ground_range == pytest.approx(standoff, abs=0.12)


@pytest.mark.parametrize("altitude_m", [1.00, 1.55, 2.00])
def test_the_standoff_scales_with_altitude(altitude_m):
    """It is geometry, not a tuned constant, so it must track the altitude."""
    controller = build_controller()
    expected = altitude_m / math.tan(math.radians(69.0))
    assert controller.inspection_standoff_m(altitude_m) == pytest.approx(expected, rel=0.01)


# ------------------------------------------------------------------ the freeze


def test_the_airframe_is_at_rest_on_both_axes_once_frozen():
    controller = build_controller()
    trace, _, _, _ = fly_to_freeze(controller, lateral_m=0.8)

    assert trace[-1].nadir_frozen
    assert trace[-1].vx == 0.0
    assert trace[-1].vy == 0.0


def test_nothing_translates_again_after_the_freeze():
    """The commitment is latched. Inspection happens here, then the return leg.

    Sustained afterwards rather than checked once: the sigma-delta shaper banks
    sub-threshold displacement and discharges it as a pulse several cycles later,
    so a cross-track axis that was merely zeroed rather than drained would emit a
    kick well after the drone was supposed to be still.
    """
    controller = build_controller()
    trace, ground_range, lateral, tilt = fly_to_freeze(controller, lateral_m=0.8)
    assert trace[-1].nadir_frozen

    for _ in range(200):
        command = controller.compute(
            target_pixel(ground_range, tilt, ALTITUDE_M, lateral), FRAME, tilt, ALTITUDE_M, DT
        )
        tilt = command.tilt_deg
        assert command.vx == 0.0, "the freeze must not resume forward flight"
        assert command.vy == 0.0, "the freeze must not resume cross-track trim"
        assert command.phase is TrackingPhase.NADIR


def test_a_target_drifting_out_of_the_window_does_not_restart_the_approach():
    """A committed freeze does not revert.

    The excursion exit exists for a nadir hold that is still tracking. Applying
    it to a committed freeze would send the airframe translating again at the
    moment Stage 4 is about to ask it to hold perfectly still, to chase a target
    the gimbal has already been pointed at from the standoff it was chosen for.
    """
    controller = build_controller()
    trace, _, _, tilt = fly_to_freeze(controller)
    assert trace[-1].nadir_frozen

    for _ in range(VisionConfig().nadir_exit_frames * 4):
        command = controller.compute((820.0, 60.0), FRAME, tilt, ALTITUDE_M, DT)
        tilt = command.tilt_deg

    assert controller.phase is TrackingPhase.NADIR
    assert controller.nadir_committed
    assert command.vx == 0.0 and command.vy == 0.0


def test_the_freeze_is_not_declared_while_the_airframe_is_still_stopping():
    """``nadir_committed`` precedes ``nadir_frozen``, and the gap is real.

    The approach arrives carrying velocity. Stepping it to zero is a pitch
    transient that swings the camera at the exact moment the mission needs it
    steady, so the along-track axis is brought to rest through the jerk-limited
    profile -- and the stage must not call itself finished until it is there.
    """
    controller = build_controller()
    trace, _, _, _ = fly_to_freeze(controller)

    committed = [index for index, cmd in enumerate(trace) if cmd.nadir_committed]
    frozen = [index for index, cmd in enumerate(trace) if cmd.nadir_frozen]

    assert committed and frozen
    assert committed[0] < frozen[0], "the freeze was reported before the drone had stopped"
    settling = trace[committed[0] : frozen[0]]
    assert all(cmd.vy == 0.0 for cmd in settling), "cross-track stops immediately"
    speeds = [cmd.vx for cmd in settling]
    assert speeds == sorted(speeds, reverse=True), "the along-track ramp must be monotone"


def test_an_off_axis_target_does_not_trigger_the_freeze():
    """The gimbal has one axis, so pitch alone cannot prove what it is aimed at.

    A target well off to the side drives the pitch solution down exactly as a
    centred one does, because the pointing law reads the vertical bearing and
    nothing else. Without the corridor veto the drone would freeze at the
    inspection attitude aimed beside its own track and photograph the tarmac
    next to the accident.
    """
    controller = build_controller()
    tilt = SEARCH_TILT_DEG
    for _ in range(400):
        command = controller.compute((830.0, 300.0), FRAME, tilt, ALTITUDE_M, DT)
        tilt = command.tilt_deg

    assert command.alignment == 0.0, "this scenario must actually be off-corridor"
    assert controller.phase is TrackingPhase.APPROACHING
    assert not controller.nadir_committed
    assert command.vx == pytest.approx(0.0, abs=1e-6)


# ------------------------------------------------------- downstream agreement


def test_the_inspection_stage_points_the_gimbal_at_the_same_attitude():
    """Stage 4 re-asserts the tilt; it must be the one Stage 3 stopped at."""
    params = MissionParameters()
    assert params.gimbal.nadir_tilt_deg == build_controller().gimbal_cfg.nadir_tilt_deg


def test_the_open_loop_fallback_ramp_is_rescaled_to_the_new_sweep():
    """The no-geometry path derives its speed from progress along the sweep.

    The sweep is now 49 degrees rather than 60. A ramp still normalised against
    the old span would report the gimbal as less far along than it is and hold
    the approach faster than it should be, at exactly the moment the fallback is
    in use because the altitude estimate is untrustworthy.
    """
    controller = build_controller()
    gimbal = GimbalConstraintsConfig(search_tilt_deg=SEARCH_TILT_DEG)

    at_search, _ = controller._approach_speed(None, gimbal.search_tilt_deg)
    at_nadir, _ = controller._approach_speed(None, gimbal.nadir_tilt_deg)

    cap = FlightKinematicsConfig().max_approach_forward_speed
    assert at_search == pytest.approx(cap)
    assert at_nadir == pytest.approx(cap * 0.20)
