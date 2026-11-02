"""Orthogonal-axis retention on a translational leg."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from mvp_mission_bebop.controllers.cross_axis_hold import CrossAxisHold
from mvp_mission_bebop.parameters import TranslationHoldConfig

DT = 1.0 / 15.0
#: m/s per normalized unit at the uncalibrated gain.
GAIN = 1.0
TAU = 0.4


def drift(hold: bool, wind_mps: float = 0.02, seconds: float = 5.0) -> float:
    """Orthogonal displacement under a constant crosswind, first-order plant."""
    law = CrossAxisHold(TranslationHoldConfig(enabled=hold))
    v = 0.0
    y = 0.0
    for _ in range(int(seconds / DT)):
        correction, _ = law.update("vy", 0.10, v, None, DT)
        target = wind_mps + correction * GAIN
        v += (target - v) * DT / TAU
        y += v * DT
    return abs(y)


def test_crosswind_drift_is_reduced_to_a_quarter() -> None:
    assert drift(True) <= 0.25 * drift(False)


def test_correction_is_saturated() -> None:
    law = CrossAxisHold(TranslationHoldConfig())
    correction, _ = law.update("vx", 0.15, 5.0, None, DT)
    assert correction == pytest.approx(-0.03)
    correction, _ = law.update("vx", 0.15, -5.0, None, DT)
    assert abs(correction) <= 0.03 + 1e-12


def test_inactive_below_min_active_speed() -> None:
    law = CrossAxisHold(TranslationHoldConfig())
    law.update("vy", 0.10, 0.2, None, DT)
    assert law.update("vy", 0.01, 0.2, None, DT) == (0.0, 0.0)
    assert law.integral == 0.0


def test_integral_is_clamped() -> None:
    cfg = TranslationHoldConfig()
    law = CrossAxisHold(cfg)
    for _ in range(1000):
        law.update("vy", 0.10, 0.5, None, DT)
    assert abs(law.integral) <= cfg.max_correction / cfg.ki + 1e-12


def test_yaw_is_zero_without_heading_hold() -> None:
    law = CrossAxisHold(TranslationHoldConfig())
    assert law.update("vy", 0.10, 0.0, 10.0, DT)[1] == 0.0
    enabled = CrossAxisHold(replace(TranslationHoldConfig(), heading_hold_enabled=True))
    assert enabled.update("vy", 0.10, 0.0, None, DT)[1] == 0.0
    _, yaw = enabled.update("vy", 0.10, 0.0, 10.0, DT)
    assert yaw == pytest.approx(-0.04)
    assert enabled.update("vy", 0.10, 0.0, 1.0, DT)[1] == 0.0


def test_nan_input_returns_zero_and_keeps_the_integrator() -> None:
    law = CrossAxisHold(TranslationHoldConfig())
    law.update("vy", 0.10, 0.1, None, DT)
    before = law.integral
    assert law.update("vy", 0.10, float("nan"), None, DT) == (0.0, 0.0)
    assert law.integral == before
    assert math.isfinite(law.integral)


def test_reset_clears_and_disabled_is_inert() -> None:
    law = CrossAxisHold(TranslationHoldConfig())
    law.update("vy", 0.10, 0.1, None, DT)
    law.reset()
    assert law.integral == 0.0
    off = CrossAxisHold(replace(TranslationHoldConfig(), enabled=False))
    assert off.update("vy", 0.10, 0.3, 10.0, DT) == (0.0, 0.0)


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        CrossAxisHold(TranslationHoldConfig()).update("vz", 0.1, 0.0, None, DT)
    with pytest.raises(ValueError):
        CrossAxisHold(replace(TranslationHoldConfig(), kp_velocity=-1.0))
    with pytest.raises(TypeError):
        CrossAxisHold(replace(TranslationHoldConfig(), enabled="yes"))
