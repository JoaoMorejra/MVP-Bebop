"""Kinematic simulation of the airframe for benchtop (``--no-fly``) execution.

Simulation used to live inside the RTL step, which reached through the odometry
supervisor's private lock to overwrite ``current_x`` and ``current_y`` mid-loop,
applied a ``sim_dt = dt * 2.5`` fudge factor, and injected a hardcoded 1.50 m
offset so there was something to fly back from. That made the supervisor a
multi-writer object -- with a live ``/bebop/odom`` publisher the callback and
the step fought over the same fields -- and interleaved simulation logic with
flight logic in the same control loop.

Here the simulation is a peer of the real driver. The actuator proxy hands it
every velocity command; it integrates them and publishes synthetic odometry
through the supervisor's public ingestion path. Because it integrates *every*
stage, the drone is genuinely displaced by the Stage 2 cruise by the time the
RTL step runs, so the return leg exercises the real guidance law against a real
accumulated position error rather than against a magic constant.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Final

from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

logger = logging.getLogger("KinematicSimulator")

Clock = Callable[[], float]

#: Yaw rate, in rad/s, produced by a unit normalized yaw command. Nominal: the
#: mission holds vyaw at exactly zero as a hard invariant, so this only matters
#: if that invariant is ever violated, in which case the simulation should show
#: the consequence rather than hide it.
YAW_RATE_PER_UNIT: Final[float] = 1.75

#: Ceiling on a single integration step. A simulated state must not jump because
#: the mission thread was descheduled or blocked on model loading.
MAX_INTEGRATION_STEP_SEC: Final[float] = 0.25

#: Vertical speed, in m/s, of the simulated takeoff and landing transients.
CLIMB_RATE_MPS: Final[float] = 0.60
DESCENT_RATE_MPS: Final[float] = 0.35


class KinematicSimulator:
    """Integrates latched velocity commands into synthetic odometry.

    The Bebop latches the last Twist it received until another arrives and never
    zeroes it on its own, so the simulation integrates the *held* command over
    the interval between calls, which is what the real airframe does.

    Integration is driven by a timer on the executor rather than by the mission
    thread, because real odometry arrives at a steady rate whatever the mission
    is doing. Driving it from command calls alone leaves the simulated state
    frozen through any phase that issues no commands -- the post-takeoff hover,
    for one, which then never finishes its climb and leaves every altitude-
    dependent law running on its fallback path.
    """

    __slots__ = (
        "_supervisor",
        "_calibration",
        "_clock",
        "_x",
        "_y",
        "_z",
        "_yaw",
        "_vx",
        "_vy",
        "_vz",
        "_vyaw",
        "_last_update",
        "_airborne",
        "_target_altitude",
        "_lock",
    )

    def __init__(
        self,
        supervisor: OdometrySupervisor,
        calibration: SpeedCalibration,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._supervisor = supervisor
        self._calibration = calibration
        self._clock = clock

        self._x: float = 0.0
        self._y: float = 0.0
        self._z: float = 0.0
        self._yaw: float = 0.0
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._vz: float = 0.0
        self._vyaw: float = 0.0
        self._last_update: float = clock()
        self._airborne: bool = False
        self._target_altitude: float = 0.0
        # Commands arrive on the mission thread; integration is driven from an
        # executor timer. Both mutate this state.
        self._lock = threading.RLock()

    # ------------------------------------------------------------- properties

    @property
    def airborne(self) -> bool:
        """True between a simulated takeoff and the completion of a landing."""
        return self._airborne

    @property
    def position(self) -> tuple[float, float, float]:
        """Current simulated world position ``(x, y, z)`` in metres."""
        with self._lock:
            return self._x, self._y, self._z

    # ----------------------------------------------------------------- events

    def publish_initial_state(self) -> None:
        """Seed the supervisor so ground calibration has samples to work with.

        Without this the benchtop run has no odometry at all, and the health
        gates -- correctly -- refuse to proceed.
        """
        for _ in range(self._supervisor.calibration_cfg.min_ground_samples + 2):
            self._emit()

    def takeoff(self, altitude_m: float) -> None:
        """Begin a simulated climb to the requested altitude."""
        self.integrate()
        with self._lock:
            self._airborne = True
            self._target_altitude = max(0.0, altitude_m)
        logger.debug("Simulated takeoff to %.2f m from (%.2f, %.2f).", altitude_m, self._x, self._y)

    def land(self) -> None:
        """Begin a simulated descent to the ground."""
        self.integrate()
        with self._lock:
            self._target_altitude = 0.0
        logger.debug("Simulated landing from %.2f m.", self._z)

    def command(self, vx: float, vy: float, vz: float, vyaw: float) -> None:
        """Integrate the previously held command, then latch a new one."""
        self.integrate()
        with self._lock:
            self._vx = vx
            self._vy = vy
            self._vz = vz
            self._vyaw = vyaw

    # -------------------------------------------------------------- mechanics

    def integrate(self) -> None:
        """Advance the simulated state to now and publish a synthetic sample."""
        with self._lock:
            now = self._clock()
            dt = min(MAX_INTEGRATION_STEP_SEC, max(0.0, now - self._last_update))
            self._last_update = now
            if dt <= 0.0:
                return
            self._advance(dt)
        self._emit()

    def _advance(self, dt: float) -> None:
        """Integrate one step. The caller holds the lock."""

        # Vertical: the climb and descent transients dominate the commanded vz,
        # matching a Bebop that runs its own altitude loop during takeoff and
        # landing and ignores velocity commands in those phases.
        if self._airborne and self._z < self._target_altitude:
            self._z = min(self._target_altitude, self._z + CLIMB_RATE_MPS * dt)
        elif self._target_altitude <= 0.0 and self._z > 0.0:
            self._z = max(0.0, self._z - DESCENT_RATE_MPS * dt)
            if self._z == 0.0:
                self._airborne = False
        elif self._airborne:
            self._z = max(0.0, self._z + self._calibration.to_mps(self._vz) * dt)

        # Horizontal: body FLU commands rotated into the world frame.
        if self._airborne:
            self._yaw += self._vyaw * YAW_RATE_PER_UNIT * dt
            speed_x = self._calibration.to_mps(self._vx)
            speed_y = self._calibration.to_mps(self._vy)
            cos_psi = math.cos(self._yaw)
            sin_psi = math.sin(self._yaw)
            self._x += (speed_x * cos_psi - speed_y * sin_psi) * dt
            self._y += (speed_x * sin_psi + speed_y * cos_psi) * dt

    def _emit(self) -> None:
        """Publish the current simulated state through the public ingress."""
        with self._lock:
            state = (self._x, self._y, self._z, self._yaw, self._vx, self._vy,
                     self._vz, self._airborne)

        x, y, z, yaw, vx, vy, vz, airborne = state
        if airborne:
            speed_x = self._calibration.to_mps(vx)
            speed_y = self._calibration.to_mps(vy)
            cos_psi = math.cos(yaw)
            sin_psi = math.sin(yaw)
            world_vx = speed_x * cos_psi - speed_y * sin_psi
            world_vy = speed_x * sin_psi + speed_y * cos_psi
            world_vz = self._calibration.to_mps(vz)
        else:
            world_vx = world_vy = world_vz = 0.0

        self._supervisor.inject_synthetic_sample(
            x=x, y=y, z=z, vx=world_vx, vy=world_vy, vz=world_vz, yaw=yaw
        )
