"""Stage 1 acquires the target altitude in both directions, in both profiles (section 3.6).

The firmware ends its launch profile about one metre above the launch surface
and ignores the altitude it is given. On the pad top the target in the control
frame is ``target_altitude_m - platform.height_m``, always within
``[0.30, 1.00]`` (D1), so the correction there is a descent; from the floor it
can be a climb.
"""

from __future__ import annotations

import time
import types

import numpy as np
import pytest

from mvp_mission_bebop.actuators import simulator as simulator_module
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.simulator import FIRMWARE_HOVER_ALTITUDE_M, KinematicSimulator
from mvp_mission_bebop.blackboard import MissionBlackboard
from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.launch_envelope import altitude_envelope
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import takeoff as takeoff_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor
from test_takeoff import ClimbContext, StubDrone, climb_params


def pad_params(target_floor: float, pad: float) -> MissionParameters:
    params = climb_params(target=target_floor)
    params.platform.height_m = pad
    params.platform.venue_ceiling_agl_m = 2.5
    params.kinematics.takeoff_settle_min_samples = 3
    return params


def hovering_context(monkeypatch, params: MissionParameters, *, hover_m: float = FIRMWARE_HOVER_ALTITUDE_M):
    """A context whose airframe ended the native launch at ``hover_m`` above the launch surface."""
    monkeypatch.setattr(simulator_module, "CLIMB_RATE_MPS", 40.0)
    odom = OdometrySupervisor(params.kinematics, params.timeouts, params.calibration, params.platform)
    calibration = SpeedCalibration(params.kinematics.normalized_to_mps)
    simulator = KinematicSimulator(odom, calibration)
    drone = BenchtopDroneProxy(StubDrone(), no_fly=True, simulator=simulator)
    simulator.publish_initial_state()
    assert odom.calibrate_ground_reference()
    drone.takeoff(altitude=FIRMWARE_HOVER_ALTITUDE_M)
    give_up_at = time.monotonic() + 2.0
    while time.monotonic() < give_up_at:
        simulator.integrate()
        if odom.snapshot().relative_altitude >= FIRMWARE_HOVER_ALTITUDE_M - 1e-3:
            break
        time.sleep(0.002)
    if hover_m != FIRMWARE_HOVER_ALTITUDE_M:
        # The firmware's own overshoot of the launch profile (field symptom A).
        with simulator._lock:
            simulator._z = hover_m
        simulator.integrate()
    failsafe = FailsafeSupervisor(drone, odom, params.timeouts)
    ctx = ClimbContext(params, drone, odom, failsafe)
    ctx.governor = AltitudeHoldGovernor(target_altitude=params.relative_target_altitude_m, config=params.governor)
    ctx.blackboard = MissionBlackboard()
    return ctx, simulator


def recorded_vz(monkeypatch, ctx):
    commanded = []
    original = ctx.drone.move_velocity

    def record(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=None):
        commanded.append((vx, vy, vz, vyaw))
        original(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    monkeypatch.setattr(ctx.drone, "move_velocity", record)
    return commanded


@pytest.mark.parametrize("hover_m", [1.0, 1.10, 1.25])
def test_on_the_pad_top_the_airframe_descends_from_the_native_height_to_the_target(monkeypatch, hover_m):
    params = pad_params(1.8, 1.0)
    ctx, _ = hovering_context(monkeypatch, params, hover_m=hover_m)
    commanded = recorded_vz(monkeypatch, ctx)

    status = TakeoffStep()._acquire_altitude(ctx)

    altitude = ctx.odom_supervisor.snapshot().relative_altitude
    assert status is StepStatus.SUCCESS
    assert altitude == pytest.approx(0.8, abs=params.governor.deadband_m + 0.02)
    assert not ctx.failsafe.failsafe_active, "a ceiling breach or a health fault landed the airframe"
    assert commanded and all(vx == 0.0 and vy == 0.0 and vyaw == 0.0 for vx, vy, _vz, vyaw in commanded)
    assert min(vz for _vx, _vy, vz, _vyaw in commanded) < 0.0
    assert commanded[-1][2] == 0.0, "the held command must be released on completion"


def test_from_the_floor_the_airframe_climbs_from_the_native_height_to_the_target(monkeypatch):
    params = pad_params(1.8, 0.0)
    ctx, _ = hovering_context(monkeypatch, params)

    status = TakeoffStep()._acquire_altitude(ctx)

    assert status is StepStatus.SUCCESS
    assert ctx.odom_supervisor.snapshot().relative_altitude == pytest.approx(1.8, abs=params.kinematics.climb_deadband_m)
    assert ctx.failsafe.climb_authorized is False


def test_a_target_inside_the_deadband_commands_nothing(monkeypatch):
    params = pad_params(1.0, 0.0)
    ctx, _ = hovering_context(monkeypatch, params)
    commanded = recorded_vz(monkeypatch, ctx)

    assert TakeoffStep()._acquire_altitude(ctx) is StepStatus.SUCCESS
    assert all(vz == 0.0 for _vx, _vy, vz, _vyaw in commanded)


def test_a_descent_that_does_not_converge_continues_with_a_warning(monkeypatch):
    params = pad_params(1.8, 1.0)
    params.kinematics.climb_timeout_sec = 0.3
    ctx, _ = hovering_context(monkeypatch, params)
    monkeypatch.setattr(ctx.governor, "compute_vz", lambda *_args, **_kwargs: 0.0)

    assert TakeoffStep()._acquire_altitude(ctx) is StepStatus.SUCCESS
    assert not ctx.failsafe.failsafe_active


def test_an_abort_during_the_descent_stops_it(monkeypatch):
    params = pad_params(1.8, 1.0)
    ctx, _ = hovering_context(monkeypatch, params)
    ctx.emergency_event.set()

    assert TakeoffStep()._acquire_altitude(ctx) is StepStatus.ABORTED


@pytest.mark.parametrize("mode", list(MissionMode))
def test_both_profiles_acquire_the_altitude(monkeypatch, mode):
    step = TakeoffStep(mode=mode)
    calls = []
    monkeypatch.setattr(step, "_confirm_takeoff", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_stabilize", lambda ctx: StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_acquire_altitude", lambda ctx: calls.append("acquire") or StepStatus.SUCCESS)
    monkeypatch.setattr(step, "_finalize", lambda ctx: StepStatus.SUCCESS)
    ctx = types.SimpleNamespace(blackboard=MissionBlackboard())

    assert step._flight_sequence(ctx) is StepStatus.SUCCESS
    assert calls == ["acquire"]


def test_on_the_pad_top_every_accepted_target_is_at_or_below_the_native_height():
    """The climb branch only ever runs from the floor (D1 keeps z* in [0.30, 1.00] on a 1 m pad)."""
    params = pad_params(1.8, 1.0)
    low, high = altitude_envelope(params)
    for target in np.arange(low, high + 1e-9, 0.05):
        params.kinematics.target_altitude_m = float(target)
        relative = params.relative_target_altitude_m
        assert 0.30 - 1e-9 <= relative <= FIRMWARE_HOVER_ALTITUDE_M + 1e-9


@pytest.mark.parametrize(
    "pad, ground, surface", [(1.0, False, "platform"), (1.0, True, "ground"), (0.0, False, "ground")]
)
@pytest.mark.parametrize("mode", list(MissionMode))
def test_the_takeoff_milestone_states_the_surface_and_the_floor_target(monkeypatch, mode, pad, ground, surface):
    emitted = []
    monkeypatch.setattr(takeoff_module, "emit_milestone", lambda key, payload=None: emitted.append((key, payload)))
    monkeypatch.setattr(takeoff_module, "takeoff_confirmed", lambda *args, **kwargs: True)
    params = MissionParameters()
    params.platform.height_m = pad
    params.platform.takeoff_from_ground = ground
    params.kinematics.target_altitude_m = 1.8
    ctx = types.SimpleNamespace(params=params, flying_state=2, blackboard=MissionBlackboard())
    step = TakeoffStep(mode=mode)
    step._takeoff_called = False

    step._call_takeoff_once_confirmed(ctx, 1.0)

    (key, payload), = emitted
    assert key == "mission.takeoff"
    assert payload["takeoff_surface"] == surface
    assert payload["target_altitude_m"] == pytest.approx(1.8)
    assert "native" not in payload
