"""Unit tests for the altitude governor and the visual servoing control law."""

import pytest

from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import (
    TrackingPhase,
    VisualServoingController,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    AltitudeGovernorConfig,
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)

DT = 1.0 / 15.0
FRAME = (856, 480)
CENTER = (428.0, 240.0)

#: Measured in-gate frames that latch the lateral freeze.
LOCK_CYCLES = VisionConfig().alignment_dwell_cycles


# ------------------------------------------------------------------ governor


def governor(target=1.0, **overrides):
    return AltitudeAntiClimbGovernor(target, AltitudeGovernorConfig(**overrides))


def test_governor_is_silent_inside_the_deadband():
    gov = governor()
    for altitude in (0.80, 0.95, 1.00, 1.02):
        assert gov.compute_vz(altitude, DT) == 0.0
    assert not gov.engaged


def test_governor_descends_when_above_target():
    gov = governor()
    for _ in range(10):
        command = gov.compute_vz(1.20, DT)
    assert command < 0.0
    assert gov.engaged


def test_governor_never_commands_climb():
    """The vertical invariant is structural: this controller cannot ask to rise."""
    gov = governor()
    for altitude in (0.10, 0.50, 0.97, 1.00, 1.05, 1.50, 3.00, 0.20):
        assert gov.compute_vz(altitude, DT) <= 0.0


def test_governor_saturates_at_the_descent_limit():
    config = AltitudeGovernorConfig()
    gov = governor()
    for _ in range(20):
        command = gov.compute_vz(5.0, DT)
    assert command == pytest.approx(-config.max_descent_speed)


def test_governor_releases_when_altitude_recovers():
    gov = governor()
    for _ in range(10):
        gov.compute_vz(1.30, DT)
    assert gov.engaged
    assert gov.compute_vz(0.99, DT) == 0.0
    assert not gov.engaged


def test_governor_does_not_spike_on_crossing_the_deadband():
    """Regression: the derivative used to be taken across the deadband edge.

    The previous implementation updated its derivative memory on suppressed
    cycles too, so the first sample past the threshold differentiated a step and
    applied near-full descent authority for a few centimetres of drift.
    """
    gov = governor()
    for _ in range(20):
        gov.compute_vz(0.99, DT)

    first = gov.compute_vz(1.04, DT)
    config = AltitudeGovernorConfig()
    assert abs(first) < config.max_descent_speed, "crossing the deadband must not saturate"


def test_governor_reset_clears_state():
    gov = governor()
    for _ in range(10):
        gov.compute_vz(1.40, DT)
    gov.reset()
    assert not gov.engaged
    assert gov.compute_vz(1.00, DT) == 0.0


# ----------------------------------------------------------- visual servoing


def build_controller(**vision_overrides):
    vision = VisionConfig(
        optical_center_tolerance_px=35.0,
        confirmation_frames=3,
        horizontal_fov_deg=80.0,
        vertical_fov_deg=50.0,
        **vision_overrides,
    )
    return VisualServoingController(
        gimbal_config=GimbalConstraintsConfig(),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=vision,
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def drive(controller, target_px, *, tilt=-20.0, altitude=1.5, cycles=1, at_rest=True):
    command = None
    for _ in range(cycles):
        command = controller.compute(
            target_px, FRAME, tilt, altitude, DT, airframe_at_rest=at_rest
        )
        tilt = command.tilt_deg
    return command


def test_starts_in_centering_with_no_forward_motion():
    controller = build_controller()
    command = drive(controller, (700.0, 240.0))
    assert command.phase is TrackingPhase.CENTERING
    assert command.vx == 0.0


def test_lateral_command_opposes_horizontal_error():
    controller = build_controller()
    right = drive(controller, (700.0, 240.0), cycles=5)
    controller.reset()
    left = drive(controller, (150.0, 240.0), cycles=5)

    # Target right of centre requires motion to the right, which is negative in
    # the body FLU frame where +y is left.
    assert right.vy < 0.0
    assert left.vy > 0.0


def test_lateral_deadband_is_not_defeated():
    """Regression: a fallback reinjected a raw proportional term.

    The old controller treated any zero from the PID as a first-call artefact
    and substituted ``-kp * error``, including when the zero came from the
    configured output deadband. The deadband therefore never took effect.
    """
    controller = build_controller()
    command = drive(controller, (429.0, 240.0), at_rest=False)
    assert command.phase is TrackingPhase.CENTERING
    assert command.vy == 0.0


def test_geometry_is_reported_for_diagnostics():
    controller = build_controller()
    command = drive(controller, CENTER, tilt=-45.0, altitude=2.0)
    assert command.depression_deg == pytest.approx(45.0, abs=0.5)
    assert command.ground_range_m == pytest.approx(2.0, rel=0.02)


def test_geometry_is_withheld_when_altitude_is_untrustworthy():
    """The diagnostic range is not reported from an unusable altitude."""
    controller = build_controller()
    command = drive(controller, CENTER, altitude=0.05)
    assert command.ground_range_m is None

    controller = build_controller(ibvs_enabled=False)
    command = drive(controller, CENTER, altitude=2.0)
    assert command.ground_range_m is None


def test_reset_returns_to_acquisition():
    controller = build_controller()
    drive(controller, CENTER, cycles=LOCK_CYCLES)
    assert controller.phase is TrackingPhase.LOCKED
    controller.reset()
    assert controller.phase is TrackingPhase.CENTERING
    assert not controller.locked


# ------------------------ governor integrity at a raised operating altitude


def test_governor_suppresses_a_phantom_climb_above_a_raised_target():
    """Requirement C: the governor stays the authority against firmware drift.

    Flying over an obstacle shortens the ultrasonic range, the firmware reads
    that as lost height and climbs to compensate. At a 1.80 m operating altitude
    that drift must still produce descent, exactly as it does at 1.00 m -- the
    Stage 1 ascent changed which altitude is nominal, not who governs it.
    """
    gov = governor(target=1.80)

    assert gov.compute_vz(1.80, DT) == 0.0
    assert not gov.engaged

    command = gov.compute_vz(1.95, DT)

    assert command < 0.0
    assert gov.engaged


def test_governor_never_climbs_toward_a_raised_target():
    """The deficit the Stage 1 ascent closes is not the governor's to close.

    Below target the excess is negative, so the governor must output exactly
    zero -- not a corrective climb. Climb authority belongs solely to Stage 1.
    """
    gov = governor(target=1.80)

    for altitude in (0.00, 0.50, 1.00, 1.35, 1.79, 1.80, 1.82):
        assert gov.compute_vz(altitude, DT) <= 0.0

    assert gov.compute_vz(1.00, DT) == 0.0


def test_governor_output_limits_remain_descent_only():
    """Requirement C: the output band is (-max_descent_speed, 0.0], always."""
    config = AltitudeGovernorConfig()
    gov = AltitudeAntiClimbGovernor(1.80, config)

    for _ in range(40):
        command = gov.compute_vz(3.00, DT)
        assert -config.max_descent_speed <= command <= 0.0
