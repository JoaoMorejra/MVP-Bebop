"""Unit tests for jerk-limited velocity profiling."""

import math

import pytest

from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)

DT = 1.0 / 15.0


def limits(max_velocity=0.20, max_accel=0.25, max_jerk=1.0):
    return ProfileLimits(max_velocity=max_velocity, max_accel=max_accel, max_jerk=max_jerk)


def test_rejects_non_positive_limits():
    with pytest.raises(ValueError):
        ProfileLimits(max_velocity=0.0, max_accel=0.25, max_jerk=1.0)
    with pytest.raises(ValueError):
        ProfileLimits(max_velocity=0.2, max_accel=-1.0, max_jerk=1.0)
    with pytest.raises(ValueError):
        ProfileLimits(max_velocity=0.2, max_accel=0.25, max_jerk=0.0)


def test_acceleration_and_jerk_are_bounded():
    cfg = limits()
    profile = JerkLimitedProfile(cfg)

    previous_velocity = 0.0
    previous_accel = 0.0
    for _ in range(200):
        velocity = profile.step(cfg.max_velocity, DT)
        accel = (velocity - previous_velocity) / DT
        jerk = (accel - previous_accel) / DT

        assert abs(accel) <= cfg.max_accel + 1e-6
        assert abs(jerk) <= cfg.max_jerk + 1e-6

        previous_velocity, previous_accel = velocity, accel


def test_converges_to_target_without_overshoot():
    cfg = limits()
    profile = JerkLimitedProfile(cfg)
    target = 0.15

    peak = 0.0
    for _ in range(400):
        velocity = profile.step(target, DT)
        peak = max(peak, velocity)

    assert profile.velocity == pytest.approx(target, abs=1e-6)
    assert peak <= target + 1e-6, "jerk-limited profile must not overshoot its target"


def test_decelerates_to_rest():
    cfg = limits()
    profile = JerkLimitedProfile(cfg, initial_velocity=0.20)
    for _ in range(400):
        profile.step(0.0, DT)
    assert profile.velocity == pytest.approx(0.0, abs=1e-6)
    assert profile.acceleration == pytest.approx(0.0, abs=1e-6)


def test_velocity_is_clamped_to_limit():
    cfg = limits(max_velocity=0.10)
    profile = JerkLimitedProfile(cfg)
    for _ in range(500):
        profile.step(10.0, DT)
    assert profile.velocity <= cfg.max_velocity + 1e-9


def test_sign_reversal_stays_within_jerk_budget():
    cfg = limits()
    profile = JerkLimitedProfile(cfg, initial_velocity=0.15)

    previous_accel = 0.0
    for _ in range(300):
        before = profile.velocity
        velocity = profile.step(-0.15, DT)
        accel = (velocity - before) / DT
        assert abs(accel - previous_accel) <= cfg.max_jerk * DT + 1e-6
        previous_accel = accel

    assert profile.velocity == pytest.approx(-0.15, abs=1e-6)


def test_non_positive_dt_is_a_no_op():
    profile = JerkLimitedProfile(limits(), initial_velocity=0.05)
    assert profile.step(0.20, 0.0) == pytest.approx(0.05)
    assert profile.step(0.20, -1.0) == pytest.approx(0.05)


def test_reset_reseeds_state():
    profile = JerkLimitedProfile(limits())
    for _ in range(50):
        profile.step(0.20, DT)
    profile.reset(velocity=0.03, acceleration=0.0)
    assert profile.velocity == pytest.approx(0.03)
    assert profile.acceleration == pytest.approx(0.0)


def test_braking_velocity_matches_kinematics():
    # v = sqrt(2 a d): 0.25 m/s^2 over 0.5 m gives 0.5 m/s, saturated by cruise.
    assert braking_velocity(0.5, 0.25, cruise_velocity=10.0) == pytest.approx(
        math.sqrt(2 * 0.25 * 0.5)
    )
    assert braking_velocity(0.5, 0.25, cruise_velocity=0.10) == pytest.approx(0.10)


def test_braking_velocity_aims_at_the_arrival_window():
    # Inside the tolerance there is nothing left to travel, so no speed is allowed.
    assert braking_velocity(0.15, 0.25, cruise_velocity=0.10, arrival_tolerance=0.20) == 0.0
    assert braking_velocity(-1.0, 0.25, cruise_velocity=0.10) == 0.0


def test_braking_velocity_rejects_invalid_deceleration():
    with pytest.raises(ValueError):
        braking_velocity(1.0, 0.0, cruise_velocity=0.1)


def test_jerk_stays_bounded_through_arbitrary_target_changes():
    """Regression: the anti-overshoot snap used to zero acceleration outright.

    Dropping the acceleration in a single cycle is itself an unbounded jerk, so
    a profile that only ever accelerates from rest toward a fixed target could
    satisfy the limits while a realistic command sequence violated them.
    """
    cfg = limits()
    profile = JerkLimitedProfile(cfg)
    targets = [0.20, 0.20, 0.0, -0.15, -0.15, 0.05, 0.0, 0.18, -0.02, 0.0]

    previous_velocity = 0.0
    previous_accel = 0.0
    for target in targets:
        for _ in range(25):
            velocity = profile.step(target, DT)
            accel = (velocity - previous_velocity) / DT
            jerk = (accel - previous_accel) / DT

            assert abs(accel) <= cfg.max_accel + 1e-6
            assert abs(jerk) <= cfg.max_jerk + 1e-6, f"jerk {jerk} exceeded limit at target {target}"

            previous_velocity, previous_accel = velocity, accel


def test_settles_exactly_on_a_reachable_target():
    profile = JerkLimitedProfile(limits())
    for _ in range(300):
        profile.step(0.07, DT)
    assert profile.velocity == pytest.approx(0.07, abs=1e-9)


def test_jerk_is_bounded_when_tracking_a_noisy_target():
    """Regression: the terminal snap stored an acceleration it had not emitted.

    A jittering target makes the snap fire on consecutive cycles. If the stored
    state does not match the command that was actually returned, the next cycle
    measures its jerk against the wrong reference and two opposing snaps emit
    twice the budget.
    """
    import random

    random.seed(11)
    cfg = limits()
    profile = JerkLimitedProfile(cfg)

    previous_velocity = 0.0
    previous_accel = 0.0
    for _ in range(2000):
        target = 0.02 + random.gauss(0.0, 0.01)
        velocity = profile.step(target, DT)
        accel = (velocity - previous_velocity) / DT
        jerk = (accel - previous_accel) / DT

        assert abs(accel) <= cfg.max_accel + 1e-6
        assert abs(jerk) <= cfg.max_jerk + 1e-6

        previous_velocity, previous_accel = velocity, accel


def test_arrival_at_the_velocity_limit_is_smooth():
    """Regression: the profile used to overshoot into the velocity clamp.

    The overshoot test evaluated the stopping distance of the *current*
    acceleration, which authorises an increment whose own stopping distance is
    larger. One cycle later the clamp truncated the step, producing a velocity
    discontinuity -- precisely the artefact this class exists to prevent.
    """
    cfg = limits(max_velocity=0.10, max_accel=0.25, max_jerk=1.0)
    profile = JerkLimitedProfile(cfg)

    previous_velocity = 0.0
    previous_accel = 0.0
    saturated = False
    for _ in range(120):
        velocity = profile.step(-cfg.max_velocity, DT)
        accel = (velocity - previous_velocity) / DT
        jerk = (accel - previous_accel) / DT

        assert velocity >= -cfg.max_velocity - 1e-9
        assert abs(jerk) <= cfg.max_jerk + 1e-6

        previous_velocity, previous_accel = velocity, accel
        saturated = saturated or velocity == pytest.approx(-cfg.max_velocity, abs=1e-9)

    assert saturated, "the profile should reach the velocity limit"
    assert profile.acceleration == pytest.approx(0.0, abs=1e-9)
