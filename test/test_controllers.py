"""Unit tests for the altitude governor and the visual servoing control law."""

import math

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
        gimbal_config=GimbalConstraintsConfig(search_tilt_deg=-20.0, nadir_tilt_deg=-80.0),
        gimbal_pid_cfg=GimbalPIDConfig(),
        lateral_pid_cfg=LateralPIDConfig(),
        vision_cfg=vision,
        kinematics_cfg=FlightKinematicsConfig(),
        calibration=SpeedCalibration(1.0),
    )


def drive(controller, target_px, *, tilt=-20.0, altitude=1.5, cycles=1):
    command = None
    for _ in range(cycles):
        command = controller.compute(target_px, FRAME, tilt, altitude, DT)
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


def test_transitions_to_approaching_once_centred():
    controller = build_controller()
    command = drive(controller, CENTER, cycles=6)
    assert command.phase is TrackingPhase.APPROACHING


def steady_approach_speed(target_px, tilt, altitude, cycles=90):
    """Run the approach to steady state and report the settled forward speed."""
    controller = build_controller()
    drive(controller, CENTER, cycles=6, altitude=altitude)

    command = None
    for _ in range(cycles):
        command = controller.compute(target_px, FRAME, tilt, altitude, DT)
    return command.vx, command.ground_range_m


def test_approach_speed_follows_the_measured_range():
    """The coupling that was previously a function of elapsed frames.

    Forward speed must be a non-decreasing function of how far the target
    actually is. The old law derived it from how far the gimbal ramp had
    progressed, which is a function of elapsed frames and says nothing about
    distance; here the gimbal is held fixed at a range of angles so only the
    geometry varies.
    """
    # Steeper gimbal, with altitude fixed, means a closer target.
    samples = [
        steady_approach_speed(CENTER, tilt=tilt, altitude=1.0)
        for tilt in (-78.0, -75.0, -70.0, -60.0, -45.0, -25.0)
    ]

    ranges = [ground_range for _, ground_range in samples]
    speeds = [speed for speed, _ in samples]

    assert ranges == sorted(ranges), "steeper tilt must estimate a closer target"
    for closer, further in zip(speeds, speeds[1:]):
        assert closer <= further + 1e-9, "approach speed must not decrease with range"

    # And the coupling must actually bite somewhere in that span, rather than
    # saturating at the cruise cap across the whole range.
    assert speeds[0] < speeds[-1]


def test_gimbal_tracks_the_target_downward_during_approach():
    controller = build_controller()
    drive(controller, CENTER, cycles=6)

    tilt = -20.0
    tilts = []
    for _ in range(60):
        # Target sitting low in frame: the camera must pitch down to follow it.
        command = controller.compute((428.0, 420.0), FRAME, tilt, 1.5, DT)
        tilt = command.tilt_deg
        tilts.append(tilt)

    assert tilts[-1] < tilts[0], "gimbal must pitch down toward the target"
    assert tilts[-1] >= -80.0, "gimbal must not exceed the nadir limit"


def test_gimbal_respects_the_slew_rate_limit():
    """``max_slew_limit_deg`` was declared in config and never read."""
    controller = build_controller()
    drive(controller, CENTER, cycles=6)

    gimbal_cfg = GimbalConstraintsConfig()
    max_step = gimbal_cfg.max_slew_limit_deg * DT
    tilt = -20.0
    for _ in range(60):
        command = controller.compute((428.0, 470.0), FRAME, tilt, 1.5, DT)
        assert abs(command.tilt_deg - tilt) <= max_step + 1e-6
        tilt = command.tilt_deg


def test_forward_motion_freezes_outside_the_lateral_corridor():
    controller = build_controller()
    drive(controller, CENTER, cycles=6)
    assert controller.phase is TrackingPhase.APPROACHING

    # The demand drops to zero immediately; the command follows it down the
    # jerk-limited ramp rather than stepping, which is the whole point of the
    # profile. What matters is that it reaches rest and says why.
    command = None
    for _ in range(40):
        command = controller.compute((820.0, 300.0), FRAME, -30.0, 1.5, DT)
        if "corridor" in command.note:
            break

    assert "corridor" in command.note
    for _ in range(60):
        command = controller.compute((820.0, 300.0), FRAME, -30.0, 1.5, DT)
    assert command.vx == pytest.approx(0.0, abs=1e-6)


def test_sustained_drift_reverts_to_centering():
    controller = build_controller()
    drive(controller, CENTER, cycles=6)

    tilt = -30.0
    for _ in range(VisionConfig().decentered_frames_to_revert + 2):
        command = controller.compute((830.0, 300.0), FRAME, tilt, 1.5, DT)
        tilt = command.tilt_deg

    assert controller.phase is TrackingPhase.CENTERING


def test_nadir_phase_is_reachable_and_exits_on_sustained_excursion():
    """Regression: the nadir phase previously had no exit at all."""
    controller = build_controller()
    controller._phase = TrackingPhase.NADIR

    for _ in range(VisionConfig().nadir_exit_frames + 2):
        controller.compute((820.0, 60.0), FRAME, -80.0, 1.5, DT)

    assert controller.phase is TrackingPhase.APPROACHING


def test_nadir_alignment_is_reported_when_centred():
    controller = build_controller()
    controller._phase = TrackingPhase.NADIR
    command = controller.compute(CENTER, FRAME, -80.0, 1.5, DT)
    assert command.nadir_aligned
    assert command.tilt_deg == pytest.approx(-80.0)


def test_lateral_deadband_is_not_defeated():
    """Regression: a fallback reinjected a raw proportional term.

    The old controller treated any zero from the PID as a first-call artefact
    and substituted ``-kp * error``, including when the zero came from the
    configured output deadband. The deadband therefore never took effect.
    """
    controller = build_controller()
    controller._phase = TrackingPhase.NADIR
    command = controller.compute((429.0, 240.0), FRAME, -80.0, 1.5, DT)
    assert command.vy == 0.0


def test_geometry_is_reported_for_diagnostics():
    controller = build_controller()
    command = drive(controller, CENTER, tilt=-45.0, altitude=2.0)
    assert command.depression_deg == pytest.approx(45.0, abs=0.5)
    assert command.ground_range_m == pytest.approx(2.0, rel=0.02)


def test_falls_back_to_open_loop_when_altitude_is_untrustworthy():
    """The escape hatch for a field session where altitude proves unusable."""
    controller = build_controller()
    command = drive(controller, CENTER, altitude=0.05)
    assert command.ground_range_m is None

    controller = build_controller(ibvs_enabled=False)
    command = drive(controller, CENTER, altitude=2.0)
    assert command.ground_range_m is None


def test_reset_returns_to_centering():
    controller = build_controller()
    drive(controller, CENTER, cycles=6)
    assert controller.phase is TrackingPhase.APPROACHING
    controller.reset()
    assert controller.phase is TrackingPhase.CENTERING
