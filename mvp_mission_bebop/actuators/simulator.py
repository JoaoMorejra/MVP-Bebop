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
from dataclasses import dataclass
from typing import Callable, Final, Optional

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

#: Altitude, in metres, at which the Bebop firmware ends its launch profile.
#:
#: The firmware runs its own takeoff and hovers here regardless of the altitude
#: the SDK was given -- ``BebopDrone.takeoff`` accepts the argument and the
#: airframe ignores it. The simulator used to honour that argument as a hard
#: setpoint and ramp all the way to it, which made ``--no-fly`` report a
#: successful climb to any requested altitude while the real drone sat at ~1 m.
#: The bench cannot be allowed to pass a manoeuvre the airframe fails: anything
#: above this height has to be earned with a commanded ``vz``.
FIRMWARE_HOVER_ALTITUDE_M: Final[float] = 1.00

#: ARSDK ``ARDrone3.PilotingState.FlyingStateChanged`` values the simulated
#: airframe passes through, as the driver publishes them on
#: ``/bebop/states/flying_state``.
FLYING_STATE_LANDED: Final[int] = 0
FLYING_STATE_TAKINGOFF: Final[int] = 1
FLYING_STATE_HOVERING: Final[int] = 2
FLYING_STATE_FLYING: Final[int] = 3
FLYING_STATE_LANDING: Final[int] = 4

#: World speed, in m/s, above which an airborne Bebop reports ``flying`` rather
#: than ``hovering``. Below it the station-keeping residuals of the altitude
#: governor would otherwise flicker the state.
MOVING_SPEED_MPS: Final[float] = 0.05


@dataclass(frozen=True)
class SimulatedState:
    """One synthetic sample, in the frame of the driver's ``/bebop/odom``.

    Parameters
    ----------
    x, y, z : float
        Position in metres: x forward and y left of the launch heading, z up.
    yaw : float
        Heading in radians, counter-clockwise from the launch heading.
    vx, vy, vz : float
        World-frame velocity in m/s.
    flying_state : int
        ARSDK flying state the airframe would report, see ``FLYING_STATE_*``.
    """

    x: float
    y: float
    z: float
    yaw: float
    vx: float
    vy: float
    vz: float
    flying_state: int


StateListener = Callable[[SimulatedState], None]


def bebop_flying_state(
    airborne: bool,
    launching: bool,
    target_altitude: float,
    z: float,
    speed_mps: float,
) -> int:
    """Map the simulated flight phase onto the ARSDK flying state.

    Parameters
    ----------
    airborne : bool
        Between a takeoff and the completion of its landing.
    launching : bool
        Inside the firmware launch transient.
    target_altitude : float
        Transient setpoint; zero once a landing has been commanded.
    z : float
        Current altitude in metres.
    speed_mps : float
        Magnitude of the world-frame velocity.

    Returns
    -------
    int
        One of the ``FLYING_STATE_*`` values.
    """
    if not airborne:
        return FLYING_STATE_LANDED
    if launching:
        return FLYING_STATE_TAKINGOFF
    if target_altitude <= 0.0 and z > 0.0:
        return FLYING_STATE_LANDING
    return FLYING_STATE_FLYING if speed_mps > MOVING_SPEED_MPS else FLYING_STATE_HOVERING


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
        "_launching",
        "_target_altitude",
        "_state_listener",
        "_lock",
    )

    def __init__(
        self,
        supervisor: OdometrySupervisor,
        calibration: SpeedCalibration,
        *,
        clock: Clock = time.monotonic,
        state_listener: Optional[StateListener] = None,
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
        self._launching: bool = False
        self._target_altitude: float = 0.0
        self._state_listener: Optional[StateListener] = state_listener
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

    def set_state_listener(self, listener: Optional[StateListener]) -> None:
        """Receive every synthetic sample, e.g. to publish it for the station.

        The listener runs on whichever thread emitted the sample and must not
        block. An exception it raises is logged and swallowed: the station
        losing its view of the simulation is no reason to stop simulating.
        """
        self._state_listener = listener

    # ----------------------------------------------------------------- events

    def publish_initial_state(self) -> None:
        """Seed the supervisor so ground calibration has samples to work with.

        Without this the benchtop run has no odometry at all, and the health
        gates -- correctly -- refuse to proceed.
        """
        for _ in range(self._supervisor.calibration_cfg.min_ground_samples + 2):
            self._emit()

    def takeoff(self, altitude_m: float) -> None:
        """Begin the simulated firmware launch profile.

        The requested altitude only ever *caps* the transient; it cannot raise it
        above :data:`FIRMWARE_HOVER_ALTITUDE_M`, because the firmware this stands
        in for does not climb higher on its own. Closing the remaining gap to a
        higher target is Stage 1's job, through commanded ``vz``.
        """
        self.integrate()
        with self._lock:
            self._airborne = True
            self._launching = True
            self._target_altitude = min(max(0.0, altitude_m), FIRMWARE_HOVER_ALTITUDE_M)
            hover = self._target_altitude
        logger.debug(
            "Simulated takeoff from (%.2f, %.2f): firmware levels at %.2f m of the %.2f m requested.",
            self._x,
            self._y,
            hover,
            altitude_m,
        )

    def land(self) -> None:
        """Begin a simulated descent to the ground."""
        self.integrate()
        with self._lock:
            self._launching = False
            self._target_altitude = 0.0
        logger.debug("Simulated landing from %.2f m.", self._z)

    def complete_landing(self) -> bool:
        """Finish a commanded landing at once, as the firmware would, and report it.

        Called when the run ends. The mission confirms touchdown from odometry
        at ``rtl.touchdown_altitude_m`` and exits shortly after; the simulator
        stopped with it mid-descent, so its last report was LANDING (4) and the
        station's bench lock read the airframe as airborne after a completed
        run. A real Bebop finishes the landing on its own and reports LANDED.

        Returns
        -------
        bool
            True when a landing was in progress and has been completed; a
            hovering or grounded airframe is left as it is.
        """
        self.integrate()
        with self._lock:
            landing = self._airborne and self._target_altitude <= 0.0
            if landing:
                self._z = 0.0
                self._vx = self._vy = self._vz = self._vyaw = 0.0
                self._airborne = False
                self._launching = False
        if landing:
            self._emit()
        return landing

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
        if self._launching and self._z < self._target_altitude:
            self._z = min(self._target_altitude, self._z + CLIMB_RATE_MPS * dt)
            if self._z >= self._target_altitude:
                # The launch profile is over. From here the airframe holds
                # altitude and obeys commanded vz -- including the descent the
                # anti-climb governor asks for. While this branch was keyed on
                # ``_airborne`` alone it outranked every command for the whole
                # flight, so any dip below the setpoint was force-climbed at
                # CLIMB_RATE_MPS and the governor was silently cancelled.
                self._launching = False
        elif self._target_altitude <= 0.0 and self._z > 0.0:
            self._z = max(0.0, self._z - DESCENT_RATE_MPS * dt)
            if self._z == 0.0:
                self._airborne = False
                self._launching = False
        elif self._airborne:
            self._z = max(0.0, self._z + self._calibration.to_mps(self._vz, axis="vertical") * dt)

        # Horizontal: body FLU commands rotated into the world frame.
        if self._airborne:
            self._yaw += self._vyaw * YAW_RATE_PER_UNIT * dt
            speed_x = self._calibration.to_mps(self._vx, axis="forward")
            speed_y = self._calibration.to_mps(self._vy, axis="lateral")
            cos_psi = math.cos(self._yaw)
            sin_psi = math.sin(self._yaw)
            self._x += (speed_x * cos_psi - speed_y * sin_psi) * dt
            self._y += (speed_x * sin_psi + speed_y * cos_psi) * dt

    def _emit(self) -> None:
        """Publish the current simulated state through the public ingress."""
        with self._lock:
            state = (self._x, self._y, self._z, self._yaw, self._vx, self._vy,
                     self._vz, self._airborne, self._launching, self._target_altitude)

        x, y, z, yaw, vx, vy, vz, airborne, launching, target_altitude = state
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

        listener = self._state_listener
        if listener is None:
            return
        speed = math.sqrt(world_vx * world_vx + world_vy * world_vy + world_vz * world_vz)
        sample = SimulatedState(
            x=x, y=y, z=z, yaw=yaw, vx=world_vx, vy=world_vy, vz=world_vz,
            flying_state=bebop_flying_state(airborne, launching, target_altitude, z, speed),
        )
        try:
            listener(sample)
        except Exception as exc:  # noqa: BLE001 - observers never stop the airframe
            logger.debug("Simulated state listener failed: %s", exc)
