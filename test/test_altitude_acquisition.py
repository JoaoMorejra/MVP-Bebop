"""Two-sided altitude acquisition against a lagged, delayed, sampled vertical plant."""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Tuple

import pytest

from mvp_mission_bebop.controllers.altitude_acquisition import (
    AcquisitionLimits,
    AltitudeAcquisitionController,
)

DT = 1.0 / 15.0


def limits(**overrides: float) -> AcquisitionLimits:
    values = dict(
        max_velocity_mps=0.25,
        max_accel_mps2=0.30,
        max_jerk_mps3=1.20,
        decel_mps2=0.12,
        lag_sec=0.35,
        position_kp=1.5,
        deadband_m=0.05,
    )
    values.update(overrides)
    return AcquisitionLimits(**values)


class Plant:
    """First-order vertical axis behind a transport delay, read by 5 Hz odometry."""

    def __init__(self, z0: float, tau: float = 0.35, latency: float = 0.15, odom_hz: float = 5.0) -> None:
        self.z = z0
        self.v = 0.0
        self.tau = tau
        self.delay: Deque[float] = deque([0.0] * max(1, int(round(latency / DT))))
        self.odom_period = 1.0 / odom_hz
        self.t = 0.0
        self.next_sample = 0.0
        self.z_meas = z0
        self.v_meas = 0.0

    def step(self, command: float) -> None:
        self.delay.append(command)
        applied = self.delay.popleft()
        substeps = 10
        h = DT / substeps
        for _ in range(substeps):
            self.v += (applied - self.v) * h / self.tau
            self.z += self.v * h
        self.t += DT
        if self.t >= self.next_sample:
            self.z_meas, self.v_meas = self.z, self.v
            self.next_sample += self.odom_period


def fly(z0: float, target: float, seconds: float = 30.0, **plant_kw: float) -> Tuple[List[float], List[float], float]:
    plant = Plant(z0, **plant_kw)
    controller = AltitudeAcquisitionController(limits())
    controller.start(z0, target)
    altitudes: List[float] = []
    commands: List[float] = []
    finished_at = math.inf
    for k in range(int(seconds / DT)):
        cmd = controller.update(plant.z_meas, plant.v_meas, DT)
        if cmd.finished and finished_at == math.inf:
            finished_at = k * DT
        commands.append(cmd.vz_mps)
        plant.step(cmd.vz_mps)
        altitudes.append(plant.z)
    return altitudes, commands, finished_at


@pytest.mark.parametrize("deficit", [0.25, 0.5, 1.0, 1.5])
def test_no_overshoot_for_climbs_of_0p25_0p5_1p0_1p5_m(deficit: float) -> None:
    altitudes, _, _ = fly(0.95, 0.95 + deficit)
    target = 0.95 + deficit
    assert max(altitudes) <= target + 0.05
    assert abs(altitudes[-1] - target) <= 0.03


@pytest.mark.parametrize("deficit", [0.25, 0.5, 1.0, 1.5])
def test_converges_within_distance_over_speed_plus_3s(deficit: float) -> None:
    _, _, finished_at = fly(0.95, 0.95 + deficit)
    assert finished_at <= deficit / 0.25 + 3.0


def test_descends_when_it_overshoots() -> None:
    controller = AltitudeAcquisitionController(limits())
    controller.start(1.30, 1.15)
    commands = [controller.update(1.30, 0.0, DT).vz_mps for _ in range(10)]
    assert all(c <= 0.0 for c in commands)
    assert commands[-1] < 0.0
    altitudes, _, _ = fly(1.30, 1.15)
    assert max(altitudes) <= 1.30 + 1e-9
    assert abs(altitudes[-1] - 1.15) <= 0.03


@pytest.mark.parametrize("tau", [0.25, 0.35, 0.5])
@pytest.mark.parametrize("latency", [0.10, 0.20])
def test_robust_to_plant_spread(tau: float, latency: float) -> None:
    for deficit in (0.25, 0.5, 1.0, 1.5):
        altitudes, _, _ = fly(0.95, 0.95 + deficit, tau=tau, latency=latency)
        assert max(altitudes) - (0.95 + deficit) <= 0.08


def test_nan_altitude_holds_at_rest() -> None:
    controller = AltitudeAcquisitionController(limits())
    controller.start(1.0, 1.5)
    cmd = controller.update(float("nan"), 0.0, DT)
    assert cmd.vz_mps == 0.0
    assert cmd.finished is False


def test_update_before_start_is_inert() -> None:
    cmd = AltitudeAcquisitionController(limits()).update(1.0, 0.0, DT)
    assert cmd.vz_mps == 0.0 and cmd.finished is False


def test_command_is_continuous() -> None:
    _, commands, _ = fly(0.95, 1.95)
    bound = limits().max_accel_mps2 * DT + 1e-9
    assert all(abs(b - a) <= bound for a, b in zip(commands, commands[1:]))


def test_invalid_limits_raise() -> None:
    with pytest.raises(ValueError):
        limits(lag_sec=-0.1)
    with pytest.raises(ValueError):
        limits(max_velocity_mps=0.0)
    with pytest.raises(ValueError):
        limits(position_kp=float("nan"))
    with pytest.raises(TypeError):
        AltitudeAcquisitionController(object())  # type: ignore[arg-type]
