"""Unit tests for the return-to-launch guidance law.

Replaces the previous ``test_rtl.py``, which exercised two module-level helpers
that implemented a piecewise ramp in displacement. The properties asserted here
are the ones that ramp could not provide: bounded kinematics, convergence
without overshoot, and a command stream the driver can actually reproduce.
"""

import math
import random

import pytest

from mvp_mission_bebop.controllers.rtl_guidance import (
    RTLGuidanceController,
    RTLPhase,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import FlightKinematicsConfig, ReturnToLaunchConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot

DT = 1.0 / 15.0


@pytest.fixture
def rtl_config():
    return ReturnToLaunchConfig()


@pytest.fixture
def kinematics_config():
    return FlightKinematicsConfig()


@pytest.fixture
def controller(rtl_config, kinematics_config):
    return RTLGuidanceController(rtl_config, kinematics_config, SpeedCalibration(1.0))


def snapshot(x, y, vx=0.0, vy=0.0, yaw=0.0, origin=(0.0, 0.0)):
    return OdometrySnapshot(
        x=x,
        y=y,
        raw_altitude=1.0,
        relative_altitude=1.0,
        vx=vx,
        vy=vy,
        vz=0.0,
        yaw=yaw,
        timestamp=0.0,
        sample_count=1,
        ground_reference=0.0,
        takeoff_x=origin[0],
        takeoff_y=origin[1],
    )


def fly(controller, start, *, noise=0.0, seed=0, cycles=1500, phase=RTLPhase.NAVIGATING):
    """Close the loop: guidance drives a point mass, position feeds back.

    Returns the trace as ``(final_x, final_y, arrival_time, peak_accel, peak_jerk)``.
    """
    random.seed(seed)
    x, y = start
    previous = (x, y)
    elapsed = 0.0
    peak_accel = peak_jerk = 0.0
    last_velocity = last_accel = 0.0
    arrival = None

    for _ in range(cycles):
        measured_vx = (x - previous[0]) / DT
        measured_vy = (y - previous[1]) / DT
        previous = (x, y)

        snap = snapshot(
            x + random.gauss(0.0, noise),
            y + random.gauss(0.0, noise),
            vx=measured_vx,
            vy=measured_vy,
        )
        command = controller.compute(snap, vz_command=0.0, dt=DT, phase=phase)

        accel = (command.target_speed_mps - last_velocity) / DT
        jerk = (accel - last_accel) / DT
        peak_accel = max(peak_accel, abs(accel))
        peak_jerk = max(peak_jerk, abs(jerk))
        last_velocity, last_accel = command.target_speed_mps, accel

        x += command.vx * DT
        y += command.vy * DT
        elapsed += DT

        if command.arrived and arrival is None:
            arrival = elapsed
            break

    return x, y, arrival, peak_accel, peak_jerk


# ------------------------------------------------------------------ invariants


@pytest.mark.parametrize("start", [(2.0, 0.3), (0.5, -0.4), (4.0, 0.0), (0.1, 0.05)])
def test_never_commands_forward_flight(controller, start):
    """'De re' is a hard invariant, enforced structurally rather than asserted."""
    x, y = start
    for _ in range(400):
        command = controller.compute(snapshot(x, y), vz_command=0.0, dt=DT)
        assert command.vx <= 0.0
        x += command.vx * DT
        y += command.vy * DT


def test_never_commands_yaw_or_climb(controller):
    for _ in range(200):
        command = controller.compute(snapshot(1.5, 0.4), vz_command=0.05, dt=DT)
        assert command.vyaw == 0.0
        assert command.vz <= 0.0


def test_governor_descent_is_passed_through(controller):
    command = controller.compute(snapshot(1.0, 0.0), vz_command=-0.06, dt=DT)
    assert command.vz == pytest.approx(-0.06)


# ----------------------------------------------------------------- convergence


@pytest.mark.parametrize("start", [(1.0, 0.0), (2.0, 0.3), (4.0, -1.0), (0.4, 0.25)])
def test_converges_inside_the_arrival_radius(controller, rtl_config, start):
    x, y, arrival, _, _ = fly(controller, start)
    assert arrival is not None, "guidance must reach a settled state"
    assert arrival <= rtl_config.timeout_sec
    assert math.hypot(x, y) <= rtl_config.arrival_radius_m


@pytest.mark.parametrize("noise", [0.0, 0.02, 0.05])
def test_kinematic_limits_hold_under_odometry_noise(controller, rtl_config, noise):
    _, _, _, peak_accel, peak_jerk = fly(controller, (2.0, 0.3), noise=noise, seed=3)
    assert peak_accel <= rtl_config.max_accel_mps2 + 1e-6
    assert peak_jerk <= rtl_config.max_jerk_mps3 + 1e-6


def test_does_not_overshoot_the_origin(controller):
    x, y = 2.0, 0.0
    furthest_past = 0.0
    for _ in range(1500):
        command = controller.compute(snapshot(x, y), vz_command=0.0, dt=DT)
        x += command.vx * DT
        furthest_past = min(furthest_past, x)
        if command.arrived:
            break
    assert furthest_past >= -0.05, "braking profile must not carry the drone past the origin"


# -------------------------------------------------------------------- commands


def test_emitted_commands_survive_driver_quantization(controller):
    """Every non-zero command must clear the driver's int8(v * 100) truncation."""
    x, y = 1.0, 0.2
    for _ in range(900):
        command = controller.compute(snapshot(x, y), vz_command=0.0, dt=DT)
        for value in (command.vx, command.vy):
            if value != 0.0:
                assert abs(int(value * 100.0)) >= 1
        x += command.vx * DT
        y += command.vy * DT
        if command.arrived:
            break


def test_lateral_authority_clears_the_historical_threshold(controller):
    """The original suite pinned int(vy * 100) >= 3 for a lateral correction."""
    commands = [
        controller.compute(snapshot(1.0, 0.5), vz_command=0.0, dt=DT).vy for _ in range(200)
    ]
    non_zero = [value for value in commands if value != 0.0]
    assert non_zero
    assert all(abs(int(value * 100.0)) >= 3 for value in non_zero)


def test_deadband_suppresses_commands_at_the_origin(controller):
    for _ in range(60):
        command = controller.compute(snapshot(0.0, 0.0), vz_command=0.0, dt=DT)
    assert command.is_stationary


# ------------------------------------------------------------------- behaviour


def test_overshoot_holds_the_along_track_axis(controller):
    """The origin ahead of the nose cannot be corrected without forward flight."""
    for _ in range(120):
        command = controller.compute(snapshot(-0.6, 0.2), vz_command=0.0, dt=DT)
    assert command.vx == 0.0
    assert "overshoot" in command.note


def test_lateral_correction_still_runs_during_an_overshoot(controller):
    commands = [
        controller.compute(snapshot(-0.6, 0.5), vz_command=0.0, dt=DT).vy for _ in range(120)
    ]
    assert any(value != 0.0 for value in commands), "cross-track axis must stay live"


def test_station_keeping_caps_speed_below_cruise(controller):
    navigating = controller.compute(snapshot(3.0, 0.0), vz_command=0.0, dt=DT)
    for _ in range(200):
        navigating = controller.compute(snapshot(3.0, 0.0), vz_command=0.0, dt=DT)

    controller.reset()
    holding = controller.compute(snapshot(3.0, 0.0), vz_command=0.0, dt=DT,
                                 phase=RTLPhase.STATION_KEEPING)
    for _ in range(200):
        holding = controller.compute(snapshot(3.0, 0.0), vz_command=0.0, dt=DT,
                                     phase=RTLPhase.STATION_KEEPING)

    assert abs(holding.target_speed_mps) < abs(navigating.target_speed_mps)


def test_body_frame_rotation_is_respected(controller):
    """Displaced along +x with the nose turned 90 deg: the origin is to starboard."""
    snap = snapshot(2.0, 0.0, yaw=math.pi / 2.0)
    command = controller.compute(snap, vz_command=0.0, dt=DT)
    assert command.ex_body_m == pytest.approx(0.0, abs=1e-9)
    assert command.ey_body_m == pytest.approx(2.0)


def test_guidance_is_clock_free(controller):
    """Mission time must accumulate from dt, never from a wall clock.

    A control law that reads the clock cannot be stepped deterministically and
    couples its behaviour to how fast the loop happens to run.
    """
    for _ in range(30):
        controller.compute(snapshot(1.0, 0.0), vz_command=0.0, dt=DT)
    assert controller.elapsed_sec == pytest.approx(30 * DT)


def test_reset_clears_controller_state(controller):
    for _ in range(50):
        controller.compute(snapshot(2.0, 0.5), vz_command=0.0, dt=DT)
    controller.reset()
    assert controller.elapsed_sec == 0.0
    assert controller.settlement is None


def test_calibration_converts_configuration_into_physical_units(rtl_config, kinematics_config):
    controller = RTLGuidanceController(rtl_config, kinematics_config, SpeedCalibration(2.0))
    assert controller.cruise_speed_mps == pytest.approx(rtl_config.max_speed * 2.0)
