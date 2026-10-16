"""Yaw is forbidden everywhere except inside an explicit, bounded window."""

from __future__ import annotations

import math

import pytest

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.yaw_authority import YawAuthority
from mvp_mission_bebop.parameters import FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor


class RawDrone:
    def __init__(self) -> None:
        self.calls = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append((vx, vy, vz, vyaw))


class Odometry:
    def snapshot(self):
        return self


def failsafe(authority=None) -> FailsafeSupervisor:
    return FailsafeSupervisor(
        drone_actuator=None,
        odom_supervisor=Odometry(),
        timeouts_cfg=TimeoutsConfig(),
        kinematics_cfg=FlightKinematicsConfig(),
        yaw_authority=authority,
    )


def test_a_closed_authority_forces_zero():
    authority = YawAuthority()
    assert not authority.is_open
    assert authority.clamp(0.5) == 0.0
    assert authority.clamp(-0.5) == 0.0


def test_an_open_window_passes_up_to_its_ceiling_and_saturates_beyond():
    authority = YawAuthority()
    with authority.window(0.30):
        assert authority.is_open
        assert authority.clamp(0.20) == 0.20
        assert authority.clamp(-0.20) == -0.20
        assert authority.clamp(0.90) == 0.30
        assert authority.clamp(-0.90) == -0.30


def test_the_window_closes_on_exit_even_after_an_exception():
    authority = YawAuthority()
    with pytest.raises(RuntimeError):
        with authority.window(0.30):
            raise RuntimeError("step fault")
    assert not authority.is_open
    assert authority.clamp(0.2) == 0.0


def test_windows_nest_and_restore_the_previous_ceiling():
    authority = YawAuthority()
    with authority.window(0.30):
        with authority.window(0.10):
            assert authority.ceiling == 0.10
        assert authority.ceiling == 0.30
    assert authority.ceiling == 0.0


@pytest.mark.parametrize("ceiling", [-0.1, math.nan, math.inf])
def test_an_invalid_ceiling_is_refused(ceiling):
    with pytest.raises(ValueError):
        with YawAuthority().window(ceiling):
            pass


def test_a_non_finite_command_is_suppressed_inside_a_window():
    authority = YawAuthority()
    with authority.window(0.30):
        assert authority.clamp(math.nan) == 0.0
        assert authority.clamp(math.inf) == 0.0


def test_the_failsafe_suppresses_yaw_unless_its_authority_is_open():
    supervisor = failsafe()
    assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.0)
    with supervisor.yaw_window(0.30):
        assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.2)
    assert supervisor.clamp_kinematics(0.0, 0.2) == (0.0, 0.0)


def test_the_proxy_suppresses_yaw_unless_its_authority_is_open():
    authority = YawAuthority()
    raw = RawDrone()
    proxy = BenchtopDroneProxy(raw, no_fly=False, yaw_authority=authority)
    proxy.move_velocity(vyaw=0.2)
    with authority.window(0.30):
        proxy.move_velocity(vyaw=0.2)
    proxy.move_velocity(vyaw=0.2)
    assert [call[3] for call in raw.calls] == [0.0, 0.2, 0.0]


def test_a_mismatched_pair_fails_closed():
    # The failsafe opens its window, the proxy holds a different, closed one:
    # the airframe must still receive zero.
    supervisor = failsafe(YawAuthority())
    raw = RawDrone()
    proxy = BenchtopDroneProxy(raw, no_fly=False, yaw_authority=YawAuthority())
    with supervisor.yaw_window(0.30):
        _vz, vyaw = supervisor.clamp_kinematics(0.0, 0.2)
        proxy.move_velocity(vyaw=vyaw)
    assert raw.calls[-1][3] == 0.0
