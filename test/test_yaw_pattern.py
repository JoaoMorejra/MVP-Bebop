"""Pure yaw laws: scan ramp, pixel-error servo, heading restore."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.controllers.visual_servoing import TrackingPhase
from mvp_mission_bebop.controllers.yaw_pattern import (
    YawHeadingController,
    YawRateLimiter,
    YawServoingController,
    validate_yaw_pattern,
    wrap_angle,
)
from mvp_mission_bebop.parameters import VisionConfig, YawPatternConfig

WIDTH, HEIGHT = 856, 480
CENTER = (WIDTH / 2.0, HEIGHT / 2.0)
DT = 1.0 / 15.0
YAW_RATE_PER_UNIT = 1.75


def servo(**overrides) -> YawServoingController:
    cfg = YawPatternConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return YawServoingController(cfg, VisionConfig())


def compute(controller, px, *, measured=True, at_rest=True):
    return controller.compute(
        target_center=(px, HEIGHT / 2.0),
        frame_dimensions=(WIDTH, HEIGHT),
        current_tilt_deg=-20.0,
        relative_altitude_m=1.0,
        dt=DT,
        measured=measured,
        airframe_at_rest=at_rest,
    )


# ---------------------------------------------------------------- wrap_angle


@pytest.mark.parametrize(
    "angle, expected",
    [
        (0.0, 0.0),
        (math.pi, math.pi),
        (-math.pi, math.pi),
        (3 * math.pi / 2, -math.pi / 2),
        (-3 * math.pi / 2, math.pi / 2),
        (4 * math.pi + 0.3, 0.3),
    ],
)
def test_wrap_angle_lands_in_the_half_open_interval(angle, expected):
    assert wrap_angle(angle) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("angle", [math.nan, math.inf])
def test_wrap_angle_refuses_a_non_finite_angle(angle):
    with pytest.raises(ValueError):
        wrap_angle(angle)


# ------------------------------------------------------------- rate limiter


def test_the_ramp_never_changes_faster_than_its_slew_limit():
    limiter = YawRateLimiter(max_rate_per_sec=0.30)
    previous = 0.0
    for _ in range(30):
        value = limiter.step(0.15, DT)
        assert abs(value - previous) <= 0.30 * DT + 1e-12
        previous = value
    assert previous == pytest.approx(0.15)


def test_the_ramp_decelerates_to_exactly_zero():
    limiter = YawRateLimiter(0.30)
    for _ in range(30):
        limiter.step(0.15, DT)
    for _ in range(30):
        value = limiter.step(0.0, DT)
    assert value == 0.0


def test_the_ramp_refuses_a_non_positive_slew_limit():
    with pytest.raises(ValueError):
        YawRateLimiter(0.0)


# --------------------------------------------------------------- validation


def test_the_factory_configuration_is_executable():
    validate_yaw_pattern(YawPatternConfig())


@pytest.mark.parametrize(
    "field, value",
    [
        ("scan_vyaw", 0.0),
        ("scan_vyaw", 0.9),
        ("max_vyaw", 0.05),
        ("min_effective_vyaw", 0.001),
        ("return_tolerance_deg", 0.0),
        ("return_timeout_sec", 0.0),
        ("return_settle_cycles", 0),
        ("scan_ramp_per_sec", -1.0),
    ],
)
def test_an_inexecutable_configuration_is_refused(field, value):
    cfg = YawPatternConfig()
    setattr(cfg, field, value)
    with pytest.raises(ValueError):
        validate_yaw_pattern(cfg)


# ----------------------------------------------------------- servoing law


def test_a_target_right_of_centre_turns_the_airframe_right():
    command = compute(servo(), CENTER[0] + 250.0)
    assert command.vyaw < 0.0
    assert command.vx == 0.0 and command.vy == 0.0


def test_a_target_left_of_centre_turns_the_airframe_left():
    command = compute(servo(), CENTER[0] - 250.0)
    assert command.vyaw > 0.0
    assert command.vx == 0.0 and command.vy == 0.0


def test_the_command_never_exceeds_the_pid_saturation():
    cfg = YawPatternConfig()
    controller = YawServoingController(cfg, VisionConfig())
    for px in (0.0, WIDTH):
        command = compute(controller, px)
        assert abs(command.vyaw) <= cfg.pid_output_limit + 1e-6


def test_the_lock_latches_after_the_dwell_and_stays_latched():
    controller = servo()
    dwell = VisionConfig().alignment_dwell_cycles
    commands = [compute(controller, CENTER[0] + 3.0) for _ in range(dwell + 2)]
    locked = [c for c in commands if c.locked]
    assert locked, "the freeze never latched"
    assert all(c.vyaw == 0.0 for c in locked)
    assert commands[-1].locked and commands[-1].phase is TrackingPhase.LOCKED
    assert compute(controller, CENTER[0] + 200.0).locked


def test_the_lock_waits_for_the_airframe_to_be_at_rest():
    controller = servo()
    for _ in range(20):
        assert not compute(controller, CENTER[0], at_rest=False).locked
    assert compute(controller, CENTER[0], at_rest=True).locked


def test_banked_credit_does_not_lock_on_an_off_centre_frame():
    controller = servo()
    for _ in range(40):
        compute(controller, CENTER[0], at_rest=False)
    command = compute(controller, CENTER[0] + 300.0, at_rest=True)
    assert not command.locked
    assert not command.laterally_centered


def test_banked_credit_does_not_lock_on_a_predicted_frame():
    controller = servo()
    for _ in range(40):
        compute(controller, CENTER[0], at_rest=False)
    command = compute(controller, CENTER[0], measured=False, at_rest=True)
    assert not command.locked


def test_predicted_frames_earn_no_centring_credit():
    controller = servo()
    for _ in range(20):
        assert not compute(controller, CENTER[0], measured=False).locked


def test_losing_the_target_searches_toward_the_side_it_left_from():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    command = controller.note_target_lost(-20.0, DT)
    assert command.phase is TrackingPhase.REACQUIRING
    assert command.vyaw < 0.0
    assert command.vx == 0.0 and command.vy == 0.0

    controller = servo()
    compute(controller, CENTER[0] - 300.0)
    assert controller.note_target_lost(-20.0, DT).vyaw > 0.0


def test_reacquisition_is_bounded_and_reports_exhaustion():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    window = VisionConfig().reacquire_timeout_sec
    exhausted = False
    for _ in range(int(window / DT) + 5):
        command = controller.note_target_lost(-20.0, DT)
        exhausted = exhausted or command.recovery_exhausted
    assert exhausted
    assert command.vyaw == 0.0


def test_regaining_the_target_resumes_centring():
    controller = servo()
    compute(controller, CENTER[0] + 300.0)
    for _ in range(3):
        controller.note_target_lost(-20.0, DT)
    confirm = VisionConfig().reacquire_confirm_frames
    for _ in range(confirm):
        command = compute(controller, CENTER[0] + 100.0)
    assert command.phase is TrackingPhase.CENTERING


def test_reset_returns_to_centring_and_clears_the_lock():
    controller = servo()
    for _ in range(10):
        compute(controller, CENTER[0])
    assert controller.locked
    controller.reset()
    assert not controller.locked and controller.phase is TrackingPhase.CENTERING


def test_a_non_positive_frame_is_refused():
    with pytest.raises(ValueError):
        servo().compute((0.0, 0.0), (0, 480), -20.0, 1.0, DT)


# ------------------------------------------------------------ heading law


def settle(controller, yaw0, reference, cycles=600):
    yaw = yaw0
    for index in range(cycles):
        command = controller.update(yaw, reference, DT)
        if command.settled:
            return yaw, index, True
        yaw += command.vyaw * YAW_RATE_PER_UNIT * DT
    return yaw, cycles, False


@pytest.mark.parametrize("offset_deg", [90.0, -90.0, 25.0, -170.0, 179.0])
def test_the_heading_restore_converges_from_any_offset(offset_deg):
    cfg = YawPatternConfig()
    controller = YawHeadingController(cfg)
    reference = math.radians(30.0)
    yaw, _cycles, settled = settle(controller, reference + math.radians(offset_deg), reference)
    assert settled, "the heading restore did not settle in 40 s of simulated time"
    assert abs(wrap_angle(reference - yaw)) <= math.radians(cfg.return_tolerance_deg) + 1e-9


def test_the_restore_takes_the_short_way_round():
    controller = YawHeadingController(YawPatternConfig())
    # Reference +170 deg, airframe at -170 deg: the short way is -20 deg (clockwise).
    command = controller.update(math.radians(-170.0), math.radians(170.0), DT)
    assert command.error_rad == pytest.approx(math.radians(-20.0), abs=1e-9)
    assert command.vyaw <= 0.0


def test_the_restore_never_exceeds_its_saturation():
    cfg = YawPatternConfig()
    controller = YawHeadingController(cfg)
    for _ in range(40):
        command = controller.update(math.radians(179.0), 0.0, DT)
        assert abs(command.vyaw) <= cfg.return_max_vyaw + 1e-6


def test_the_restore_commands_zero_inside_the_tolerance():
    controller = YawHeadingController(YawPatternConfig())
    command = controller.update(math.radians(1.0), 0.0, DT)
    assert command.vyaw == 0.0


def test_a_non_finite_heading_is_refused():
    controller = YawHeadingController(YawPatternConfig())
    with pytest.raises(ValueError):
        controller.update(math.nan, 0.0, DT)
