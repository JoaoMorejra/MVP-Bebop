"""Unit tests for continuous safety and kinematic invariants."""

import pytest

from mvp_mission_bebop.exceptions import KinematicConstraintViolation
from mvp_mission_bebop.parameters import FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class DummyActuator:
    def __init__(self):
        self.landed = False
        self.velocities = []

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0):
        self.velocities.append((vx, vy, vz, vyaw))

    def land(self):
        self.landed = True


def test_kinematic_invariants_enforcement():
    kinematics_cfg = FlightKinematicsConfig(target_altitude_m=1.0)
    timeouts_cfg = TimeoutsConfig()
    odom_sup = OdometrySupervisor(kinematics_cfg, timeouts_cfg)
    actuator = DummyActuator()
    failsafe = FailsafeSupervisor(actuator, odom_sup, timeouts_cfg)

    # Valid commands: vz <= 0.0, vyaw == 0.0
    failsafe.assert_kinematics(vz=0.0, vyaw=0.0)
    failsafe.assert_kinematics(vz=-0.05, vyaw=0.0)

    # Positive vertical velocity (climb) is prohibited
    with pytest.raises(KinematicConstraintViolation):
        failsafe.assert_kinematics(vz=0.02, vyaw=0.0)

    # Yaw rotation is prohibited
    with pytest.raises(KinematicConstraintViolation):
        failsafe.assert_kinematics(vz=-0.01, vyaw=0.1)


def test_emergency_landing_trigger():
    kinematics_cfg = FlightKinematicsConfig(target_altitude_m=1.0)
    timeouts_cfg = TimeoutsConfig()
    odom_sup = OdometrySupervisor(kinematics_cfg, timeouts_cfg)
    actuator = DummyActuator()
    failsafe = FailsafeSupervisor(actuator, odom_sup, timeouts_cfg)

    assert failsafe.failsafe_active is False
    assert actuator.landed is False

    failsafe.trigger_emergency_land("Test Emergency Reason")
    assert failsafe.failsafe_active is True
    assert actuator.landed is True
