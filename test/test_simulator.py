"""Unit tests for the benchtop kinematic simulator."""

import math

import pytest

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.simulator import KinematicSimulator
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    CalibrationConfig,
    FlightKinematicsConfig,
    TimeoutsConfig,
)
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class StubDrone:
    """Records what the proxy forwards, standing in for the Nectar drone."""

    def __init__(self):
        self.calls = []

    def flat_trim(self):
        self.calls.append(("flat_trim",))

    def takeoff(self, altitude):
        self.calls.append(("takeoff", altitude))
        return True

    def land(self, timeout=30.0):
        self.calls.append(("land",))
        return True

    def camera_control(self, tilt, pan):
        self.calls.append(("camera_control", tilt, pan))

    def snapshot(self):
        self.calls.append(("snapshot",))

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        self.calls.append(("move_velocity", vx, vy, vz, vyaw))

    def delay(self, seconds):
        self.calls.append(("delay", seconds))

    def connect(self):
        return True

    def cleanup(self):
        self.calls.append(("cleanup",))


def build(clock=None):
    supervisor = OdometrySupervisor(
        FlightKinematicsConfig(), TimeoutsConfig(), CalibrationConfig(min_ground_samples=4)
    )
    simulator = KinematicSimulator(
        supervisor, SpeedCalibration(1.0), clock=clock or FakeClock()
    )
    return supervisor, simulator


def test_grounded_drone_does_not_translate():
    clock = FakeClock()
    supervisor, simulator = build(clock)

    simulator.command(0.5, 0.0, 0.0, 0.0)
    clock.advance(1.0)
    simulator.integrate()

    snapshot = supervisor.snapshot()
    assert snapshot.x == pytest.approx(0.0)
    assert snapshot.y == pytest.approx(0.0)


def test_takeoff_climbs_to_the_commanded_altitude():
    clock = FakeClock()
    supervisor, simulator = build(clock)

    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()

    assert supervisor.snapshot().raw_altitude == pytest.approx(1.0)
    assert simulator.airborne


def test_forward_command_displaces_along_the_body_axis():
    clock = FakeClock()
    supervisor, simulator = build(clock)
    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()

    simulator.command(0.2, 0.0, 0.0, 0.0)
    for _ in range(50):
        clock.advance(0.1)
        simulator.integrate()

    snapshot = supervisor.snapshot()
    assert snapshot.x == pytest.approx(0.2 * 5.0, rel=0.05)
    assert snapshot.y == pytest.approx(0.0, abs=1e-9)


def test_lateral_command_displaces_to_the_left():
    clock = FakeClock()
    supervisor, simulator = build(clock)
    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()

    simulator.command(0.0, 0.1, 0.0, 0.0)
    for _ in range(20):
        clock.advance(0.1)
        simulator.integrate()

    assert supervisor.snapshot().y > 0.0


def test_landing_descends_to_the_ground():
    clock = FakeClock()
    supervisor, simulator = build(clock)
    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()

    simulator.land()
    for _ in range(80):
        clock.advance(0.1)
        simulator.integrate()

    assert supervisor.snapshot().raw_altitude == pytest.approx(0.0)
    assert not simulator.airborne


def test_integration_step_is_bounded():
    """A descheduled mission thread must not teleport the simulated drone."""
    clock = FakeClock()
    supervisor, simulator = build(clock)
    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()

    simulator.command(1.0, 0.0, 0.0, 0.0)
    before = supervisor.snapshot().x
    clock.advance(30.0)
    simulator.integrate()

    assert supervisor.snapshot().x - before <= 0.30


def test_initial_state_seeds_ground_calibration():
    """Without seeding, a benchtop run has no odometry and refuses to launch."""
    supervisor, simulator = build()
    simulator.publish_initial_state()
    assert supervisor.calibrate_ground_reference() is True


def test_proxy_routes_commands_into_the_simulator_in_no_fly():
    clock = FakeClock()
    supervisor, simulator = build(clock)
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=True, simulator=simulator)

    proxy.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()
    proxy.move_velocity(vx=0.2)
    clock.advance(1.0)
    simulator.integrate()

    assert supervisor.snapshot().x > 0.0
    # Motor commands must never reach the hardware on the bench.
    assert not [call for call in drone.calls if call[0] in ("takeoff", "move_velocity", "land")]


def test_proxy_still_drives_the_gimbal_and_shutter_in_no_fly():
    """Those are precisely the parts a benchtop run exists to exercise."""
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=True, simulator=None)

    proxy.camera_control(tilt=-80.0)
    proxy.snapshot()

    assert ("camera_control", -80.0, 0.0) in drone.calls
    assert ("snapshot",) in drone.calls


def test_proxy_does_not_sleep_in_no_fly():
    """Benchtop runs used to pay every hardware settling delay for no benefit."""
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=True, simulator=None)
    proxy.delay(5.0)
    assert not [call for call in drone.calls if call[0] == "delay"]


def test_proxy_forwards_everything_when_flying():
    drone = StubDrone()
    proxy = BenchtopDroneProxy(drone, no_fly=False)

    proxy.takeoff(1.5)
    proxy.move_velocity(vx=0.1, vy=-0.2, vz=-0.05)
    proxy.land()
    proxy.delay(0.0)

    kinds = [call[0] for call in drone.calls]
    assert kinds == ["takeoff", "move_velocity", "land", "delay"]


def test_state_advances_without_any_command():
    """Regression: the simulation froze through phases that issue no commands.

    The post-takeoff hover sends nothing, so a simulator driven only by command
    calls never finished its climb. Every altitude-dependent law then ran on its
    fallback path, and the benchtop run silently exercised the wrong code.
    """
    clock = FakeClock()
    supervisor, simulator = build(clock)

    simulator.takeoff(1.0)
    for _ in range(40):
        clock.advance(0.1)
        simulator.integrate()   # no command in between, as a hover would be

    assert supervisor.snapshot().relative_altitude == pytest.approx(1.0, abs=0.01)


def test_integration_is_safe_from_two_threads():
    """Commands arrive on the mission thread; integration runs on the executor."""
    import threading

    supervisor, simulator = build()
    simulator.takeoff(1.0)
    errors = []

    def integrate():
        try:
            for _ in range(400):
                simulator.integrate()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def command():
        try:
            for index in range(400):
                simulator.command(0.1 * (index % 3), 0.0, 0.0, 0.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=integrate), threading.Thread(target=command)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert math.isfinite(supervisor.snapshot().x)
