"""Two vertical references: the control loop's, and the floor's.

``relative_altitude`` is measured from the ground reference calibrated before
takeoff, which on a 1.0 m pad is the top of the pad. Everything that regulates
the vertical axis stays in that frame; everything that says something about the
floor -- the operator's 1.8 m, the IBVS ground projection -- must not.
"""

from __future__ import annotations

import json
import os
import threading
import time
import types

import pytest

from mvp_mission_bebop.actuators import simulator as simulator_module
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.simulator import KinematicSimulator
from mvp_mission_bebop.controllers.geometry import CameraIntrinsics, project_to_ground
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, OdometrySupervisor


class RecordingDrone:
    """Records the altitude handed to ``takeoff``; the proxy is not under test here."""

    no_fly = True

    def __init__(self) -> None:
        self.takeoff_altitudes = []

    def takeoff(self, altitude):
        self.takeoff_altitudes.append(altitude)
        return True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        pass

    def land(self):
        return True


class StubNectarDrone:
    def takeoff(self, altitude):
        return True

    def land(self, timeout=10.0):
        return True

    def move_velocity(self, vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        pass

    def camera_control(self, tilt, pan):
        pass


class StepContext:
    """The attribute surface Stage 1 touches in these tests."""

    def __init__(self, params, drone, odom_supervisor, failsafe=None):
        self.params = params
        self.drone = drone
        self.odom_supervisor = odom_supervisor
        self.failsafe = failsafe
        self.flying_state = None
        self.emergency_event = threading.Event()
        self.stage_jump_event = threading.Event()
        self.speed_calibration = SpeedCalibration(params.kinematics.normalized_to_mps)

    def interrupted(self):
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def grab_frame(self, timeout_sec=1.0):
        return None


def pad_params(target: float = 1.8, height: float = 1.0) -> MissionParameters:
    params = MissionParameters()
    params.kinematics.target_altitude_m = target
    params.kinematics.altitude_ceiling_margin_m = 0.6
    params.platform.height_m = height
    params.no_fly = True
    return params


def test_takeoff_is_commanded_with_the_altitude_above_the_pad():
    params = pad_params()
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    drone = RecordingDrone()
    ctx = StepContext(params, drone, odom)

    assert TakeoffStep()._command_takeoff(ctx) is StepStatus.SUCCESS
    assert drone.takeoff_altitudes == [pytest.approx(0.8)]


def test_takeoff_without_a_pad_still_commands_the_configured_target():
    params = pad_params(target=1.55, height=0.0)
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    drone = RecordingDrone()
    TakeoffStep()._command_takeoff(StepContext(params, drone, odom))
    assert drone.takeoff_altitudes == [1.55]


def test_the_takeoff_milestone_still_speaks_the_height_above_the_floor(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        "mvp_mission_bebop.steps.takeoff.emit_milestone",
        lambda key, payload=None: emitted.append((key, dict(payload or {}))),
    )
    params = pad_params()
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    TakeoffStep()._call_takeoff_once_confirmed(StepContext(params, RecordingDrone(), odom), 1.0)
    assert emitted == [("mission.takeoff", {"altitude_m": 1.8})]


def firmware_hover_context(monkeypatch, params, firmware_altitude=1.0):
    """Airborne on the pad at the firmware's own launch height, whatever was asked."""
    monkeypatch.setattr(simulator_module, "CLIMB_RATE_MPS", 40.0)
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    simulator = KinematicSimulator(odom, SpeedCalibration(1.0))
    drone = BenchtopDroneProxy(StubNectarDrone(), no_fly=True, simulator=simulator)
    simulator.publish_initial_state()
    assert odom.calibrate_ground_reference()
    simulator.takeoff(firmware_altitude)
    give_up_at = time.monotonic() + 2.0
    while time.monotonic() < give_up_at:
        simulator.integrate()
        if odom.snapshot().relative_altitude >= firmware_altitude - 1e-3:
            break
        time.sleep(0.002)
    failsafe = FailsafeSupervisor(drone, odom, params.timeouts)
    return StepContext(params, drone, odom, failsafe)


def test_a_firmware_hover_above_the_relative_target_does_not_ascend(monkeypatch):
    params = pad_params()
    ctx = firmware_hover_context(monkeypatch, params)
    windows = []
    original = ctx.failsafe.climb_window

    def recording_window(limit):
        windows.append(limit)
        return original(limit)

    ctx.failsafe.climb_window = recording_window

    assert TakeoffStep()._ascend(ctx) is StepStatus.SUCCESS
    assert windows == []
    assert ctx.odom_supervisor.snapshot().relative_altitude == pytest.approx(1.0, abs=0.02)


def test_a_firmware_hover_at_one_metre_stays_under_the_pad_ceiling(monkeypatch):
    params = pad_params()
    ctx = firmware_hover_context(monkeypatch, params)
    TakeoffStep()._ascend(ctx)
    assert ctx.odom_supervisor.altitude_ceiling == pytest.approx(1.4)
    assert not ctx.odom_supervisor.is_ceiling_breached()


# ------------------------------------------------- height above the floor (1.3-3)



def snapshot(relative: float, floor_offset: float = 0.0) -> OdometrySnapshot:
    return OdometrySnapshot(
        x=0.0, y=0.0, raw_altitude=relative, relative_altitude=relative,
        vx=0.0, vy=0.0, vz=0.0, yaw=0.0, timestamp=0.0, sample_count=1,
        ground_reference=0.0, takeoff_x=0.0, takeoff_y=0.0, floor_offset_m=floor_offset,
    )


def test_height_above_floor_adds_the_pad_to_the_relative_altitude():
    assert snapshot(0.8, 1.0).height_above_floor_m == pytest.approx(1.8)
    assert snapshot(1.55).height_above_floor_m == 1.55


def test_the_supervisor_stamps_the_pad_height_on_every_snapshot():
    params = pad_params()
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    for _ in range(params.calibration.min_ground_samples):
        odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.0)
    assert odom.calibrate_ground_reference()
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=0.8)

    snap = odom.snapshot()
    assert snap.floor_offset_m == 1.0
    assert snap.relative_altitude == pytest.approx(0.8)
    assert snap.height_above_floor_m == pytest.approx(1.8)


def test_a_supervisor_without_a_pad_reports_no_floor_offset():
    params = MissionParameters()
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration)
    odom.inject_synthetic_sample(x=0.0, y=0.0, z=1.2)
    assert odom.snapshot().floor_offset_m == 0.0
    assert odom.snapshot().height_above_floor_m == odom.snapshot().relative_altitude


def tracking_ctx(relative: float, floor_offset: float):
    from test_stage_altitude_wiring import Ctx

    ctx = Ctx()
    ctx.odom_supervisor = types.SimpleNamespace(
        snapshot=lambda: snapshot(relative, floor_offset),
        relative_altitude=relative,
        telemetry_health=ctx.odom_supervisor.telemetry_health,
        is_ceiling_breached=ctx.odom_supervisor.is_ceiling_breached,
    )
    ctx.failsafe.odom_supervisor = ctx.odom_supervisor
    altitudes = []
    compute = ctx.visual_controller.compute

    def recording_compute(**kwargs):
        altitudes.append(kwargs["relative_altitude_m"])
        return compute(**kwargs)

    ctx.visual_controller.compute = recording_compute
    return ctx, altitudes


def test_the_visual_approach_is_driven_by_the_height_above_the_floor():
    """IBVS over a pad sees the same geometry as a flight from the floor at that height."""
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    ctx, altitudes = tracking_ctx(relative=0.8, floor_offset=1.0)
    VisualServoingStep().execute(ctx)

    assert altitudes, "the approach never ran the servoing law"
    assert all(value == pytest.approx(1.8) for value in altitudes)


def test_the_ibvs_floor_is_judged_against_the_height_above_the_floor():
    """0.30 m over a 1.0 m pad is 1.30 m over the target: above min_altitude_for_ibvs_m."""
    from mvp_mission_bebop.steps.tracking import VisualServoingStep

    ctx, altitudes = tracking_ctx(relative=0.30, floor_offset=1.0)
    VisualServoingStep().execute(ctx)

    assert altitudes
    assert min(altitudes) > ctx.params.vision.min_altitude_for_ibvs_m
    assert all(value == pytest.approx(1.30) for value in altitudes)


def test_the_target_milestone_projects_from_the_height_above_the_floor():
    from mvp_mission_bebop.steps.search import ForwardSearchStep

    params = MissionParameters()
    ctx = types.SimpleNamespace(
        frame_width=856, frame_height=480, params=params, current_tilt_deg=-20.0
    )
    target = types.SimpleNamespace(center=(500.0, 300.0), class_name="bicycle", confidence=0.9)

    over_pad = ForwardSearchStep._locate(ctx, target, snapshot(0.8, 1.0))
    from_floor = ForwardSearchStep._locate(ctx, target, snapshot(1.8))

    assert over_pad == from_floor
    intrinsics = CameraIntrinsics(856, 480, params.vision.horizontal_fov_deg, params.vision.vertical_fov_deg)
    expected = project_to_ground(intrinsics, (500.0, 300.0), 1.8, -20.0)
    assert over_pad["ground_range_m"] == pytest.approx(round(expected.ground_range_m, 2))


def test_the_forensic_sidecar_records_the_height_above_the_floor(tmp_path):
    from mvp_mission_bebop.blackboard import EvidenceRecord
    from mvp_mission_bebop.context import MissionContext

    fake = types.SimpleNamespace(
        frame_width=856, frame_height=480, current_tilt_deg=-69.0,
        params=MissionParameters(), start_time=time.time(),
    )
    path = os.path.join(str(tmp_path), "accident_metadata_test.json")
    MissionContext._write_metadata(fake, path, EvidenceRecord(), snapshot(0.8, 1.0))

    with open(path, encoding="utf-8") as handle:
        odometry = json.load(handle)["odometry"]
    assert odometry["relative_altitude_m"] == pytest.approx(0.8)
    assert odometry["height_above_floor_m"] == pytest.approx(1.8)
