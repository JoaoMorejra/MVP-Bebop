"""Keyboard nudge: shaper ramps and the additive, deadman-guarded overlay."""

from __future__ import annotations

import ast
import math
import os
from dataclasses import replace
from typing import List, Tuple

import pytest

from mvp_mission_bebop.actuators.nudge_overlay import NudgeOverlay
from mvp_mission_bebop.controllers.nudge import NudgeShaper
from mvp_mission_bebop.parameters import NudgeConfig

DT = 1.0 / 15.0


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def ramp_time(shaper: NudgeShaper, intent: Tuple[float, float, float], axis: int, goal: float) -> float:
    t = 0.0
    dt = 0.001
    while t < 5.0:
        out = shaper.step(intent, dt)
        t += dt
        if abs(out[axis] - goal) <= 1e-9:
            return t
    return math.inf


def test_attack_reaches_ceiling_in_attack_sec() -> None:
    cfg = NudgeConfig()
    shaper = NudgeShaper(cfg)
    t = ramp_time(shaper, (1.0, 0.0, 0.0), 0, cfg.max_horizontal)
    assert t == pytest.approx(cfg.attack_sec, rel=0.05)
    assert shaper.output[0] <= cfg.max_horizontal + 1e-12


def test_release_reaches_zero_in_release_sec_without_crossing() -> None:
    cfg = NudgeConfig()
    shaper = NudgeShaper(cfg)
    for _ in range(100):
        shaper.step((1.0, 0.0, 0.0), DT)
    outputs: List[float] = []
    t = 0.0
    while shaper.output[0] > 0.0 and t < 5.0:
        outputs.append(shaper.step((0.0, 0.0, 0.0), 0.001)[0])
        t += 0.001
    assert t == pytest.approx(cfg.release_sec, rel=0.05)
    assert min(outputs) >= 0.0


def test_diagonal_is_saturated_as_a_vector() -> None:
    cfg = NudgeConfig()
    shaper = NudgeShaper(cfg)
    for _ in range(60):
        vx, vy, _ = shaper.step((1.0, 1.0, 0.0), DT)
    assert math.hypot(vx, vy) <= cfg.max_horizontal + 1e-9
    assert math.degrees(math.atan2(vy, vx)) == pytest.approx(45.0, abs=0.5)


def test_non_finite_intent_decays_instead_of_propagating() -> None:
    shaper = NudgeShaper(NudgeConfig())
    for _ in range(10):
        shaper.step((1.0, 0.0, 0.0), DT)
    before = shaper.output[0]
    out = shaper.step((float("nan"), float("inf"), 0.0), DT)
    assert all(math.isfinite(v) for v in out)
    assert 0.0 <= out[0] < before


def overlay(cfg: NudgeConfig = NudgeConfig(), **kw: object) -> Tuple[NudgeOverlay, Clock]:
    clock = Clock()
    return NudgeOverlay(cfg, clock=clock, **kw), clock  # type: ignore[arg-type]


def run(ov: NudgeOverlay, clock: Clock, seconds: float, command=(0.0, 0.0, 0.0), intent=None):
    out = command
    for _ in range(int(round(seconds / DT))):
        if intent is not None:
            ov.set_intent(*intent)
        clock.now += DT
        out = ov.apply(*command)
    return out


def test_inactive_overlay_is_identity() -> None:
    ov, clock = overlay()
    ov.set_intent(1.0, 0.0, 0.0)
    clock.now += DT
    assert ov.apply(0.1, -0.05, 0.0) == (0.1, -0.05, 0.0)


def test_nudge_is_additive_to_autonomous_command() -> None:
    ov, clock = overlay()
    ov.arm()
    vx, vy, vyaw = run(ov, clock, 1.0, command=(0.0, -0.15, 0.0), intent=(-1.0, 0.0, 0.0))
    assert vy == -0.15
    assert vx == pytest.approx(-0.08)
    assert vyaw == 0.0


def test_total_is_clamped_to_the_horizontal_envelope() -> None:
    ov, clock = overlay()
    ov.arm()
    vx, vy, _ = run(ov, clock, 1.0, command=(0.0, -0.19, 0.0), intent=(0.0, -1.0, 0.0))
    assert math.hypot(vx, vy) <= 0.2 + 1e-12


def test_deadman_zeroes_after_timeout() -> None:
    cfg = NudgeConfig()
    ov, clock = overlay(cfg)
    ov.arm()
    run(ov, clock, 1.0, intent=(1.0, 0.0, 0.0))
    out = run(ov, clock, cfg.deadman_sec + cfg.release_sec + 2 * DT, command=(0.0, 0.1, 0.0))
    assert out == (0.0, 0.1, 0.0)


def test_disarm_latches_and_arm_reenables() -> None:
    ov, clock = overlay()
    ov.arm()
    ov.disarm("landing")
    out = run(ov, clock, 0.5, intent=(1.0, 0.0, 0.0))
    assert out == (0.0, 0.0, 0.0)
    ov.arm()
    out = run(ov, clock, 0.5, intent=(1.0, 0.0, 0.0))
    assert out[0] > 0.0


def test_permit_false_zeroes_the_nudge_with_a_ramp() -> None:
    allowed = {"value": True}
    cfg = NudgeConfig()
    ov, clock = overlay(cfg, permit=lambda: allowed["value"])
    ov.arm()
    run(ov, clock, 1.0, intent=(1.0, 0.0, 0.0))
    allowed["value"] = False
    ov.set_intent(1.0, 0.0, 0.0)
    clock.now += DT
    first = ov.apply(0.0, 0.0, 0.0)[0]
    assert 0.0 < first < cfg.max_horizontal
    out = run(ov, clock, cfg.release_sec + DT)
    assert out == (0.0, 0.0, 0.0)
    clock.now += cfg.deadman_sec
    allowed["value"] = True
    out = run(ov, clock, 0.5)
    assert out == (0.0, 0.0, 0.0)


def test_yaw_budget_saturates_one_direction() -> None:
    cfg = replace(NudgeConfig(), yaw_budget_deg=10.0)
    ov, clock = overlay(cfg)
    ov.arm()
    run(ov, clock, 3.0, intent=(0.0, 0.0, 1.0))
    assert ov.snapshot().yaw_budget_left_deg == pytest.approx(0.0, abs=1.0)
    out = run(ov, clock, 0.5, intent=(0.0, 0.0, 1.0))
    assert out[2] == 0.0
    out = run(ov, clock, 0.5, intent=(0.0, 0.0, -1.0))
    assert out[2] < 0.0
    assert ov.snapshot().yaw_budget_left_deg > 0.0


def test_governor_scale_reduces_nudge() -> None:
    ov, clock = overlay(scale_source=lambda: 0.5)
    ov.arm()
    out = run(ov, clock, 1.0, intent=(1.0, 0.0, 0.0))
    assert out[0] == pytest.approx(0.04)
    assert ov.horizontal_magnitude() == pytest.approx(0.04)


def test_disabled_overlay_is_identity() -> None:
    ov, clock = overlay(replace(NudgeConfig(), enabled=False))
    ov.arm()
    assert run(ov, clock, 1.0, command=(0.0, 0.1, 0.0), intent=(1.0, 0.0, 0.0)) == (0.0, 0.1, 0.0)


def test_non_finite_intent_is_dropped() -> None:
    ov, clock = overlay()
    ov.arm()
    ov.set_intent(float("nan"), 0.0, 0.0)
    assert ov.snapshot().intent == (0.0, 0.0, 0.0)


def test_overlay_has_no_reference_to_mission_control_flow() -> None:
    import mvp_mission_bebop.actuators.nudge_overlay as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for name in imported:
        assert "context" not in name and "failsafe" not in name and ".engine" not in name, name
    source = open(module.__file__, encoding="utf-8").read()
    for forbidden in ("emergency_event", "stage_jump_event", ".land(", ".takeoff(", "request_"):
        assert forbidden not in source.split('"""', 2)[-1], forbidden


def test_invalid_config_raises() -> None:
    with pytest.raises(ValueError):
        NudgeShaper(replace(NudgeConfig(), attack_sec=0.0))
    with pytest.raises(ValueError):
        NudgeShaper(replace(NudgeConfig(), max_horizontal=1.5))
    with pytest.raises(ValueError):
        NudgeOverlay(replace(NudgeConfig(), deadman_sec=0.0))
