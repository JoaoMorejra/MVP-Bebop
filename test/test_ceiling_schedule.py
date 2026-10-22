"""The altitude ceiling and its ratchet through the takeoff transient (section 3.5)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from mvp_mission_bebop.estimation.ceiling_schedule import (
    TOTEM_CEILING_HEADROOM_M,
    CeilingPhase,
    CeilingSchedule,
)
from mvp_mission_bebop.mission_mode import NATIVE_HOVER_HEIGHT_M
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

H_FW = NATIVE_HOVER_HEIGHT_M


def schedule(target: float, *, pad: float = 0.0, ground: bool = False, margin: float = 0.4, venue=None, allowance=0.0):
    params = MissionParameters()
    params.kinematics.target_altitude_m = target
    params.kinematics.altitude_ceiling_margin_m = margin
    params.platform.height_m = pad
    params.platform.takeoff_from_ground = ground
    params.platform.venue_ceiling_agl_m = venue
    params.platform.ceiling_allowance_m = allowance
    return CeilingSchedule(params.kinematics, params.platform), params


# ------------------------------------------------------------------ nominal


def test_from_the_floor_the_ceiling_is_target_plus_margin():
    ceiling, _ = schedule(1.8, margin=0.4)
    assert ceiling.value() == pytest.approx(2.2)


def test_on_the_pad_top_the_ceiling_is_fixed_and_ignores_the_margin():
    for margin in (0.0, 0.4, 2.0):
        ceiling, _ = schedule(1.8, pad=1.0, margin=margin)
        assert ceiling.value() == pytest.approx(max(0.8, H_FW) + TOTEM_CEILING_HEADROOM_M)


def test_d2_defaults_put_the_pad_top_ceiling_at_the_venue():
    ceiling, _ = schedule(1.8, pad=1.0, venue=2.5)
    assert ceiling.value() == pytest.approx(1.5)
    assert ceiling.value() + 1.0 == pytest.approx(2.5)


def test_the_ground_flag_uses_the_floor_formula_whatever_the_pad():
    ceiling, _ = schedule(0.5, pad=1.0, ground=True, margin=0.4, venue=2.5)
    assert ceiling.pad_height_m == 0.0
    assert ceiling.value() == pytest.approx(0.9)
    assert ceiling.venue_limit_m == pytest.approx(2.5)


@pytest.mark.parametrize("pad, ground", [(0.0, False), (1.0, False), (1.0, True)])
def test_the_guard_allowance_is_added_while_the_guard_is_active(pad, ground):
    ceiling, _ = schedule(1.8, pad=pad, ground=ground, allowance=0.3)
    assert ceiling.value(guard_active=True) == pytest.approx(ceiling.value() + 0.3)


def test_the_venue_caps_every_phase():
    ceiling, _ = schedule(1.8, venue=2.0)
    assert ceiling.value() == pytest.approx(2.0)
    ceiling.arm()
    assert ceiling.value() == pytest.approx(2.0)
    ceiling.end_native_profile()
    ceiling.update(1.0)
    assert ceiling.value() <= 2.0 + 1e-9


# ------------------------------------------------------------ the transient


def test_the_native_profile_is_bounded_by_the_venue_only():
    ceiling, _ = schedule(0.3, margin=0.21, venue=2.5)
    ceiling.arm()
    assert ceiling.phase is CeilingPhase.NATIVE
    assert ceiling.value() == pytest.approx(2.5)
    ceiling.update(1.25)
    assert ceiling.value() == pytest.approx(2.5)


def test_the_native_profile_without_a_venue_has_no_ceiling():
    ceiling, _ = schedule(0.5)
    ceiling.arm()
    assert math.isinf(ceiling.value())


@pytest.mark.parametrize(
    "target, pad, ground, margin",
    [(0.5, 0.0, False, 0.4), (0.3, 0.0, False, 0.21), (1.8, 1.0, False, 0.4), (1.3, 1.0, False, 0.0), (1.8, 0.0, False, 0.6)],
)
def test_property_1_the_ratchet_never_rises(target, pad, ground, margin):
    ceiling, _ = schedule(target, pad=pad, ground=ground, margin=margin)
    ceiling.arm()
    ceiling.end_native_profile()
    rng = np.random.default_rng(7)
    previous = ceiling.value()
    for z in np.concatenate([np.linspace(1.25, 0.0, 40), rng.uniform(0.0, 2.5, 200)]):
        ceiling.update(float(z))
        assert ceiling.value() <= previous + 1e-12
        previous = ceiling.value()


@pytest.mark.parametrize(
    "target, pad, margin",
    [(0.5, 0.0, 0.4), (0.3, 0.0, 0.21), (1.8, 1.0, 0.4), (1.3, 1.0, 0.0), (0.4, 0.0, 0.05)],
)
def test_property_2_the_ratchet_stays_clear_of_every_sample_it_tracked(target, pad, margin):
    """``value() >= z + m_e`` for each sample as it is tracked, on the descent from the native height.

    The ratchet never rises (property 1), so the bound holds for a sample at or
    under every earlier one: the acquisition from the native height down to
    the target, then the hold on it.
    """
    ceiling, _ = schedule(target, pad=pad, margin=margin)
    ceiling.arm()
    ceiling.end_native_profile()
    m_e = ceiling.effective_margin_m
    relative = ceiling.target_m
    path = np.concatenate([np.linspace(H_FW, relative, 30), np.full(20, relative)])
    for z in path:
        ceiling.update(float(z))
        assert ceiling.value() >= z + m_e - 1e-9


@pytest.mark.parametrize(
    "target, pad, ground, margin",
    [(0.5, 0.0, False, 0.4), (1.8, 0.0, False, 0.4), (1.8, 1.0, False, 0.4), (1.3, 1.0, False, 0.2), (0.5, 1.0, True, 0.3)],
)
def test_property_3_after_release_inside_the_deadband_the_ceiling_is_nominal(target, pad, ground, margin):
    ceiling, params = schedule(target, pad=pad, ground=ground, margin=margin, venue=2.5)
    nominal = ceiling.value()
    ceiling.arm()
    ceiling.end_native_profile()
    deadband = params.governor.deadband_m
    for z in np.linspace(H_FW, ceiling.target_m, 25):
        ceiling.update(float(z))
    for z in ceiling.target_m + np.linspace(deadband, -deadband, 11):
        ceiling.update(float(z))
    assert nominal - 1e-9 <= ceiling.value() <= nominal + deadband + 1e-9
    ceiling.release()
    assert ceiling.phase is CeilingPhase.NOMINAL
    assert ceiling.value() == pytest.approx(nominal)


def test_symptom_a_the_native_overshoot_on_the_pad_top_never_reaches_the_ceiling():
    """Pad 1.0, target 1.8 over the floor, margin 0.4: the old ceiling sat at 1.20 m."""
    ceiling, _ = schedule(1.8, pad=1.0, margin=0.4, venue=2.5)
    ceiling.arm()
    for z in (0.0, 0.4, 0.9, 1.25, 1.1):
        ceiling.update(z)
        assert z < ceiling.value()
    ceiling.end_native_profile()
    for z in np.linspace(1.1, 0.8, 20):
        ceiling.update(float(z))
        assert z < ceiling.value()


def test_a_ground_launch_to_a_low_target_keeps_room_for_the_native_profile():
    ceiling, _ = schedule(0.5, margin=0.4)
    ceiling.arm()
    assert ceiling.value() == math.inf
    ceiling.end_native_profile()
    assert ceiling.value() == pytest.approx(H_FW + 0.4)
    ceiling.update(1.0)
    assert ceiling.value() == pytest.approx(H_FW + 0.4)
    ceiling.update(0.7)
    assert ceiling.value() == pytest.approx(1.1)


def test_the_native_height_must_be_positive():
    params = MissionParameters()
    with pytest.raises(ValueError):
        CeilingSchedule(params.kinematics, params.platform, native_height_m=0.0)


# ------------------------------------------------------------- supervisor


def supervisor(params: MissionParameters) -> OdometrySupervisor:
    return OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)


def test_the_supervisor_delegates_and_follows_the_flying_state():
    _, params = schedule(0.5, margin=0.4, venue=2.5)
    odom = supervisor(params)
    assert odom.altitude_ceiling == pytest.approx(0.9)
    odom.arm_ceiling_transient()
    assert odom.altitude_ceiling == pytest.approx(2.5)
    odom.note_flying_state(1)
    assert odom.altitude_ceiling == pytest.approx(2.5)
    odom.note_flying_state(2)
    assert odom.altitude_ceiling == pytest.approx(1.4)
    odom.release_ceiling_transient()
    assert odom.altitude_ceiling == pytest.approx(0.9)


def test_the_supervisor_follows_a_pad_resolved_after_construction():
    _, params = schedule(1.8, pad=1.0, ground=True, margin=0.4)
    odom = supervisor(params)
    params.platform.height_m = 0.0
    assert odom.altitude_ceiling == pytest.approx(2.2)


def test_the_venue_breach_check_is_in_the_control_frame():
    _, params = schedule(1.8, pad=1.0, venue=2.5)
    odom = supervisor(params)
    assert not odom.venue_ceiling_breached(1.5)
    assert odom.venue_ceiling_breached(1.51)
