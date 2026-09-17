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


# ------------------------------------------------- stage-aware climb authority


def build_failsafe(target_altitude=1.0):
    """A supervisor wired to a dummy actuator, as the mission would build it."""
    kinematics_cfg = FlightKinematicsConfig(target_altitude_m=target_altitude)
    timeouts_cfg = TimeoutsConfig()
    odom_sup = OdometrySupervisor(kinematics_cfg, timeouts_cfg)
    actuator = DummyActuator()
    return FailsafeSupervisor(actuator, odom_sup, timeouts_cfg)


def test_clamp_suppresses_climb_by_default():
    """The default is refusal: Stages 2-5 never opt in and never climb."""
    failsafe = build_failsafe()

    safe_vz, safe_vyaw = failsafe.clamp_kinematics(0.20, 0.0)

    assert safe_vz == 0.0
    assert safe_vyaw == 0.0


def test_clamp_preserves_descent_authority():
    failsafe = build_failsafe()

    safe_vz, _ = failsafe.clamp_kinematics(-0.06, 0.0)

    assert safe_vz == pytest.approx(-0.06)


def test_stage_two_onward_call_form_cannot_climb_even_inside_a_window():
    """Stages 2-5 call positionally with two arguments and must stay grounded.

    This is the exact call form used at search.py, tracking.py, inspection.py and
    rtl.py. Even with an ascent window open -- which cannot happen in the real
    pipeline, but is the worst case -- the invariant must hold for them, because
    they never opt in.
    """
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        for requested in (0.01, 0.05, 0.20, 1.00):
            safe_vz, safe_vyaw = failsafe.clamp_kinematics(requested, 0.0)
            assert safe_vz == 0.0, f"climb of {requested} leaked into a Stage 2-5 command"
            assert safe_vyaw == 0.0


def test_climb_is_rejected_when_the_caller_opts_in_without_a_window():
    """Opting in is not sufficient. Authority is the supervisor's to grant.

    A bare parameter would be a claim the caller makes about itself, and every
    step holds the same supervisor reference. Requiring an open window means
    misplaced or copy-pasted ``allow_climb=True`` outside Stage 1 is inert.
    """
    failsafe = build_failsafe()

    assert failsafe.climb_authorized is False
    safe_vz, _ = failsafe.clamp_kinematics(0.20, 0.0, allow_climb=True)

    assert safe_vz == 0.0


def test_climb_is_permitted_inside_an_authorized_window():
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        assert failsafe.climb_authorized is True
        safe_vz, safe_vyaw = failsafe.clamp_kinematics(0.18, 0.0, allow_climb=True)

    assert safe_vz == pytest.approx(0.18)
    assert safe_vyaw == 0.0


def test_authorized_climb_saturates_at_the_window_limit():
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        safe_vz, _ = failsafe.clamp_kinematics(4.0, 0.0, allow_climb=True)

    assert safe_vz == pytest.approx(0.25)


def test_yaw_is_suppressed_even_while_climbing():
    """The yaw invariant has no exception. Optical flow depends on it."""
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        safe_vz, safe_vyaw = failsafe.clamp_kinematics(0.10, 0.9, allow_climb=True)

    assert safe_vz == pytest.approx(0.10)
    assert safe_vyaw == 0.0


def test_climb_window_revokes_authority_on_exit():
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        pass

    assert failsafe.climb_authorized is False
    safe_vz, _ = failsafe.clamp_kinematics(0.20, 0.0, allow_climb=True)
    assert safe_vz == 0.0


def test_climb_window_revokes_authority_when_the_block_raises():
    """A fault mid-ascent must not leave the vertical invariant relaxed."""
    failsafe = build_failsafe()

    with pytest.raises(RuntimeError, match="ascent blew up"):
        with failsafe.climb_window(0.25):
            raise RuntimeError("ascent blew up")

    assert failsafe.climb_authorized is False
    safe_vz, _ = failsafe.clamp_kinematics(0.20, 0.0, allow_climb=True)
    assert safe_vz == 0.0


def test_climb_window_rejects_nesting():
    failsafe = build_failsafe()

    with failsafe.climb_window(0.25):
        with pytest.raises(RuntimeError, match="already open"):
            with failsafe.climb_window(0.25):
                pass


def test_a_non_positive_climb_limit_grants_no_authority():
    failsafe = build_failsafe()

    with failsafe.climb_window(0.0):
        assert failsafe.climb_authorized is False
        safe_vz, _ = failsafe.clamp_kinematics(0.20, 0.0, allow_climb=True)

    assert safe_vz == 0.0


def test_ceiling_breach_is_reported_as_unhealthy_during_a_climb():
    """A climb is the only phase that can drive the drone through its ceiling."""
    kinematics_cfg = FlightKinematicsConfig(target_altitude_m=1.80)
    timeouts_cfg = TimeoutsConfig()
    odom_sup = OdometrySupervisor(kinematics_cfg, timeouts_cfg)
    failsafe = FailsafeSupervisor(DummyActuator(), odom_sup, timeouts_cfg)

    odom_sup.inject_synthetic_sample(x=0.0, y=0.0, z=1.50)
    assert failsafe.evaluate_system_health()[0] is True

    # Ceiling is target + margin = 2.05 m.
    odom_sup.inject_synthetic_sample(x=0.0, y=0.0, z=2.40)
    healthy, reason = failsafe.evaluate_system_health()

    assert healthy is False
    assert "ceiling breached" in reason
