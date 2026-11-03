"""Advanced Stage 5 end to end at -70 deg: search, centring, descent, touchdown.

A kinematic plant closes the loop on the commands the step transmits and
reports the marker only while its quiet zone is inside the frame, so a
centring or a descent that drives the pad off the bottom edge loses it as the
camera does. The two heading events of the 2026-11-03 flights are replayed:

* 06:14 -- the odometric heading drifts 24 deg with the airframe still. The
  terminal phases must not react: the landing still happens over the pad.
* 05:50 -- the airframe yaws 45 deg in 1.2 s at about 1 m in the descent
  without any yaw command. The marker error stays exact in the body frame;
  the descent must keep tracking and land on the pad.

Every transmitted ``vyaw`` must be zero.
"""

from __future__ import annotations

import functools
import math
from typing import List, Optional

import pytest

from mvp_mission_bebop.controllers.rtl_guidance import (
    ADVANCED_RETURN_TILT_DEG,
    FRAME_BOTTOM_TAN,
    QUIET_ZONE_HALF_FRACTION,
    MarkerObservation,
    visibility_standoff_m,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps import rtl as rtl_module
from mvp_mission_bebop.steps.base import StepStatus
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from test_aruco_rtl import Ctx, Odometry

#: Tangent of the half horizontal field of view of the 856x480 crop (80 deg).
FRAME_SIDE_TAN = math.tan(math.radians(40.0))


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture
def simulated_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(rtl_module, "LoopRate", functools.partial(LoopRate, clock=fake.clock, sleeper=fake.sleep))
    monkeypatch.setattr(rtl_module, "Deadline", functools.partial(Deadline, clock=fake.clock))
    monkeypatch.setattr(rtl_module, "_GIMBAL_SETTLE_TIMEOUT_SEC", 0.0)
    return fake


class HoldGovernor:
    """Proportional altitude hold at the centring height."""

    engaged = True
    climbing = False
    altitude_error_m = 0.0

    def __init__(self, plant: "Plant", target_m: float = 1.20) -> None:
        self._plant = plant
        self._target = target_m

    def compute_vz(self, _altitude, _dt=None, vx_commanded=0.0):
        return max(-0.1, min(0.1, 0.8 * (self._target - self._plant.h)))

    @staticmethod
    def horizontal_scale():
        return 1.0


class Plant(Odometry):
    """Marker position relative to the CoG in the body frame, and the CoG height over the pad.

    Doubles as the odometry the step reads: ``relative_altitude`` is the
    height, ``vx``/``vy`` the body velocity actually flown, ``yaw`` the
    firmware's heading estimate (which may drift independently).
    """

    def __init__(self, drone, cfg, *, ex: float, ey: float, h: float, hz: float) -> None:
        super().__init__(drone, h, period=1.0 / hz)
        self.cfg = cfg
        self.ex, self.ey, self.h = ex, ey, h
        self.period = 1.0 / hz
        self.consumed = 0
        self.yaw = 0.0
        self.heading_drift_deg = 0.0
        self.drift_rate_deg_s = 0.0
        self.yaw_burst_deg = 0.0
        self.yaw_burst_rate_deg_s = 0.0
        self.yaw_burst_trigger_h: Optional[float] = None
        self.yawed_deg = 0.0
        self.land_state: Optional[tuple] = None
        self.lost_cycles = 0

    def advance(self) -> None:
        commands = self.drone_commands()
        for vx, vy, vz, vyaw in commands[self.consumed :]:
            assert vyaw == 0.0
            if self._drone.land_calls:
                self.h = max(0.0, self.h - 0.9 * self.period)
                continue
            self.ex -= vx * self.period
            self.ey -= vy * self.period
            self.h = max(0.0, self.h + vz * self.period)
            self.vx, self.vy = vx, vy
            self.heading_drift_deg += self.drift_rate_deg_s * self.period
            if (
                self.yaw_burst_trigger_h is not None
                and self.h <= self.yaw_burst_trigger_h
                and self.yawed_deg < self.yaw_burst_deg
            ):
                step = math.radians(self.yaw_burst_rate_deg_s * self.period)
                self.yawed_deg += math.degrees(step)
                # The airframe turns nose-right; a fixed marker turns left in the body frame.
                c, s = math.cos(step), math.sin(step)
                self.ex, self.ey = c * self.ex - s * self.ey, s * self.ex + c * self.ey
        self.consumed = len(commands)
        self.yaw = math.radians(self.heading_drift_deg)
        self.relative_altitude = self.h

    def drone_commands(self) -> List[tuple]:
        return self._drone.commands

    def snapshot(self):
        self.advance()
        return self

    def observe(self, _frame) -> Optional[MarkerObservation]:
        self.advance()
        tilt = math.radians(abs(ADVANCED_RETURN_TILT_DEG))
        s_lens = self.ex - self.cfg.camera_offset_forward_m
        y_l = self.cfg.camera_offset_left_m
        quiet = QUIET_ZONE_HALF_FRACTION * self.cfg.tag_size

        def cam(forward: float) -> tuple:
            return -math.sin(tilt) * forward + math.cos(tilt) * self.h, math.cos(tilt) * forward + math.sin(tilt) * self.h

        near_y, near_z = cam(s_lens - quiet)
        far_y, far_z = cam(s_lens + quiet)
        y_cam, z_cam = cam(s_lens)
        x_cam = y_l - self.ey
        visible = (
            self.h > 0.05
            and near_z > 0.0
            and far_z > 0.0
            and near_y / near_z <= FRAME_BOTTOM_TAN
            and far_y / far_z >= -FRAME_BOTTOM_TAN
            and (abs(x_cam) + quiet) / z_cam <= FRAME_SIDE_TAN
        )
        if not visible:
            self.lost_cycles += 1
            return None
        planar_yaw = 90.0 + self.yawed_deg
        return MarkerObservation(marker_id=0, x_cam_m=x_cam, y_cam_m=y_cam, z_cam_m=z_cam, yaw_deg=planar_yaw)

    @property
    def target_id(self) -> int:
        return 0

    @property
    def radial_m(self) -> float:
        return math.hypot(self.ex, self.ey)


def fly(*, drift_deg_s: float = 0.0, yaw_burst_deg: float = 0.0, ex: float = 0.55, ey: float = -0.40):
    ctx = Ctx(altitude=1.20)
    cfg = ctx.params.rtl
    cfg.timeout_sec = 6.0
    cfg.centering_timeout_sec = 25.0
    cfg.descent_timeout_sec = 25.0
    cfg.camera_offset_forward_m = 0.0
    plant = Plant(ctx.drone, cfg, ex=ex, ey=ey, h=1.20, hz=ctx.params.kinematics.control_loop_hz)
    plant.drift_rate_deg_s = drift_deg_s
    if yaw_burst_deg:
        plant.yaw_burst_deg = yaw_burst_deg
        plant.yaw_burst_rate_deg_s = yaw_burst_deg / 1.2
        plant.yaw_burst_trigger_h = 1.0
    ctx.odom_supervisor = plant
    ctx.failsafe.odom_supervisor = plant
    ctx.governor = HoldGovernor(plant)

    land = ctx.drone.land

    def recorded_land():
        if plant.land_state is None:
            plant.land_state = (plant.h, plant.ex, plant.ey)
        return land()

    ctx.drone.land = recorded_land
    status = ClosedLoopRTLStep(mode=MissionMode.ADVANCED, sensor=plant).execute(ctx)
    return status, ctx, plant


def _assert_landed_on_the_pad(status, ctx, plant, *, tolerance_m: float) -> None:
    assert status is StepStatus.SUCCESS
    assert ctx.drone.land_calls > 0 and plant.land_state is not None
    h, ex, ey = plant.land_state
    assert h <= ctx.params.rtl.commit_height_m + 0.05, f"landing started at {h:.2f} m"
    standoff = visibility_standoff_m(h, ADVANCED_RETURN_TILT_DEG, ctx.params.rtl.tag_size, 0.0)
    assert math.hypot(ex, ey) <= standoff + tolerance_m, f"landed {math.hypot(ex, ey):.3f} m from the marker"
    assert all(vyaw == 0.0 for *_rest, vyaw in ctx.drone.commands)


def test_advanced_return_lands_on_the_pad_at_minus_70(simulated_time):
    status, ctx, plant = fly()
    _assert_landed_on_the_pad(status, ctx, plant, tolerance_m=0.06)
    assert all(t == pytest.approx(ADVANCED_RETURN_TILT_DEG) for t in ctx.drone.tilts)


def test_odometric_heading_drift_does_not_cut_the_landing_short(simulated_time):
    """06:14 replay: the firmware heading drifts 10 deg/s with the airframe still."""
    status, ctx, plant = fly(drift_deg_s=10.0)
    assert abs(plant.heading_drift_deg) > 24.0
    _assert_landed_on_the_pad(status, ctx, plant, tolerance_m=0.06)


def test_an_uncommanded_yaw_in_the_descent_still_lands_on_the_pad(simulated_time):
    """05:50 replay: 45 deg of airframe yaw in 1.2 s at 1 m, with zero yaw commanded."""
    status, ctx, plant = fly(yaw_burst_deg=45.0)
    assert plant.yawed_deg >= 44.0
    _assert_landed_on_the_pad(status, ctx, plant, tolerance_m=0.08)
