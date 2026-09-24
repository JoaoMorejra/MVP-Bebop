"""The sonar-spike gate applied to real odometry ingestion.

`inject_synthetic_sample` (the benchtop path) is deliberately exempt -- see
`OdometrySupervisor.odometry_callback`'s docstring for why.
"""

from __future__ import annotations

from mvp_mission_bebop.parameters import CalibrationConfig, FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor


class FakeOdometryMsg:
    def __init__(self, z: float):
        self.pose = type("P", (), {"pose": type("PP", (), {
            "position": type("Pos", (), {"x": 0.0, "y": 0.0, "z": z})(),
            "orientation": type("Ori", (), {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})(),
        })()})()
        self.twist = type("T", (), {"twist": type("TT", (), {
            "linear": type("Lin", (), {"x": 0.0, "y": 0.0, "z": 0.0})(),
        })()})()


def make_supervisor(**overrides):
    calib = CalibrationConfig(
        plausibility_max_speed_mps=0.40,
        plausibility_max_accel_mps2=0.40,
        plausibility_reject_streak=3,
        **overrides,
    )
    return OdometrySupervisor(FlightKinematicsConfig(), TimeoutsConfig(), calib)


def feed(supervisor, z, dt, clock):
    clock.advance(dt)
    supervisor.odometry_callback(FakeOdometryMsg(z))


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_a_single_cycle_sonar_spike_does_not_reach_current_raw_altitude():
    supervisor = make_supervisor()
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    assert supervisor.current_raw_altitude == 1.55

    supervisor.odometry_callback(FakeOdometryMsg(2.75))
    assert supervisor.current_raw_altitude == 1.55, "a false-climb spike reached raw altitude"


def test_a_sustained_new_regime_is_accepted():
    supervisor = make_supervisor()
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    for _ in range(3):
        supervisor.odometry_callback(FakeOdometryMsg(0.05))
    assert supervisor.current_raw_altitude == 0.05


def test_plausibility_can_be_disabled():
    supervisor = make_supervisor(plausibility_enabled=False)
    supervisor.odometry_callback(FakeOdometryMsg(1.55))
    supervisor.odometry_callback(FakeOdometryMsg(2.75))
    assert supervisor.current_raw_altitude == 2.75


def test_synthetic_injection_bypasses_the_filter():
    supervisor = make_supervisor()
    supervisor.inject_synthetic_sample(x=0.0, y=0.0, z=1.55)
    supervisor.inject_synthetic_sample(x=0.0, y=0.0, z=2.75)
    assert supervisor.current_raw_altitude == 2.75
