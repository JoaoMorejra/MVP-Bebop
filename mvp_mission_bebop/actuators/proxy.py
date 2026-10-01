"""Actuator proxy for real flight and benchtop (``--no-fly``) execution."""

from __future__ import annotations

import logging
from typing import Any, Callable, Final, FrozenSet, Optional, Protocol

from mvp_mission_bebop.actuators.simulator import KinematicSimulator
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker

logger = logging.getLogger("ActuatorProxy")


class DroneActuator(Protocol):
    """The subset of the Nectar drone API this mission uses.

    Declared structurally rather than importing ``BebopDrone`` so the proxy can
    be exercised against a stub, and so the dependency runs one way.
    """

    def flat_trim(self) -> None: ...
    def takeoff(self, altitude: float) -> bool: ...
    def land(self, timeout: float = ...) -> bool: ...
    def camera_control(self, tilt: float, pan: float) -> None: ...
    def snapshot(self) -> None: ...
    def move_velocity(
        self,
        vx: float = ...,
        vy: float = ...,
        vz: float = ...,
        vyaw: float = ...,
        duration: Optional[float] = ...,
    ) -> None: ...
    def delay(self, seconds: float) -> None: ...
    def connect(self) -> bool: ...
    def cleanup(self) -> None: ...


#: Raw drone methods that can spin the rotors. Under ``--no-fly`` none of them
#: may be reached, by any path.
ARMING_METHODS: Final[FrozenSet[str]] = frozenset(
    {"takeoff", "move_velocity", "move_to", "rtl", "flip", "arm"}
)


class _NoFlyInterlock:
    """The raw drone as a no-fly proxy holds it: arming methods refuse to run.

    The proxy's own no-fly branches return before the raw drone is called, but
    that is behaviour, and behaviour regresses. This makes the guarantee
    structural: whatever reaches ``proxy.drone.takeoff`` or
    ``proxy.drone.move_velocity`` -- a new branch, a step holding the raw
    handle, a refactor -- raises instead of publishing on ``/bebop/takeoff`` or
    ``/bebop/cmd_vel``. Everything else is delegated unchanged.
    """

    __slots__ = ("_drone",)

    def __init__(self, drone: "DroneActuator") -> None:
        object.__setattr__(self, "_drone", drone)

    def __getattr__(self, name: str) -> Any:
        if name in ARMING_METHODS:
            return self._refuse(name)
        return getattr(self._drone, name)

    @staticmethod
    def _refuse(name: str) -> Callable[..., Any]:
        def refused(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(f"no-fly interlock: raw drone {name}() refused, motors must stay unpowered")

        return refused


class BenchtopDroneProxy:
    """Wraps the drone so a mission can run with motors unpowered.

    In ``--no-fly`` mode motor commands are diverted into a kinematic
    simulation, and the raw drone is held behind :class:`_NoFlyInterlock`, so
    no path can reach its takeoff or velocity publishers: any attempt raises
    ``RuntimeError``. ``no_fly`` is fixed at construction.

    What stays physical on the bench, deliberately: the gimbal
    (``camera_control`` on ``/bebop/move_camera``), the IMU flat trim
    (``/bebop/flattrim``, a ground calibration) and the photo trigger
    (``/bebop/photo``). None of them spins a rotor; they are the parts a
    benchtop run is meant to exercise on the real aircraft.
    """

    def __init__(
        self,
        drone: DroneActuator,
        no_fly: bool = False,
        simulator: Optional[KinematicSimulator] = None,
        motion_tracker: Optional[DeadReckoningTracker] = None,
    ) -> None:
        self._no_fly: bool = bool(no_fly)
        self.drone = _NoFlyInterlock(drone) if self._no_fly else drone
        self.simulator = simulator
        # Dead reckoning is fed here rather than by the steps, and that is the
        # point: every velocity command in the mission passes through this
        # method, so the aggregate cannot silently lose a leg because some stage
        # forgot to report its own motion. A tracker that is only as complete as
        # the discipline of five separate control loops is not a tracker.
        self.motion_tracker = motion_tracker
        #: Whether :meth:`connect` found the driver; ``None`` before it ran.
        self.driver_reachable: Optional[bool] = None

    @property
    def no_fly(self) -> bool:
        """Whether motors are unpowered for this run. Read-only by design."""
        return self._no_fly

    def flat_trim(self) -> None:
        """Calibrate IMU flat trim. The drone must be on a level surface."""
        try:
            self.drone.flat_trim()
        except Exception as exc:  # noqa: BLE001
            if not self.no_fly:
                raise
            logger.debug("[NO-FLY] Simulated flat trim (%s).", exc)

    def takeoff(self, altitude: float) -> bool:
        """Execute autonomous takeoff."""
        if self.no_fly:
            logger.info("[NO-FLY] Simulated takeoff to %.2f m. Motors unpowered.", altitude)
            if self.simulator is not None:
                self.simulator.takeoff(altitude)
            return True
        return self.drone.takeoff(altitude=altitude)

    def land(self) -> bool:
        """Command landing.

        The underlying ``BebopDrone.land`` is fire-and-forget: it publishes an
        ``Empty`` message and returns immediately with no acknowledgement, so a
        ``True`` here means "commanded", never "landed".
        """
        # A landing ends translation whatever the last Twist said, so the
        # aggregate must stop accruing displacement under it. Without this the
        # tracker goes on crediting the final held command for the whole descent.
        if self.motion_tracker is not None:
            self.motion_tracker.command(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)

        if self.no_fly:
            logger.info("[NO-FLY] Simulated landing. Motors unpowered.")
            if self.simulator is not None:
                self.simulator.land()
            return True
        return self.drone.land()

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        """Actuate the camera gimbal. Physically executed in both modes."""
        try:
            self.drone.camera_control(tilt=tilt, pan=pan)
        except Exception as exc:  # noqa: BLE001
            if not self.no_fly:
                raise
            logger.debug("[NO-FLY] Simulated gimbal: tilt=%.1f, pan=%.1f (%s).", tilt, pan, exc)

    def snapshot(self) -> None:
        """Trigger the onboard camera. Physically executed in both modes.

        Returns nothing because the airframe reports nothing: the SDK publishes
        a boolean and receives no path, timestamp, or acknowledgement in return.
        """
        try:
            self.drone.snapshot()
        except Exception as exc:  # noqa: BLE001
            if not self.no_fly:
                raise
            logger.debug("[NO-FLY] Simulated snapshot trigger (%s).", exc)

    def move_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        vyaw: float = 0.0,
        duration: Optional[float] = None,
    ) -> None:
        """Command a normalized body-frame velocity.

        Components are normalized to [-1, 1], not metres per second: the driver
        clamps and publishes them as a Twist that the firmware interprets as a
        throttle fraction. The command is latched until another arrives.

        ``vyaw`` is forced to zero here unconditionally. The mission's vertical
        invariant is that the airframe never rotates -- the Bebop derives its
        odometry from optical flow, and any yaw rate corrupts the horizontal
        position estimate every guidance law in this package depends on. Every
        guidance law was audited and none currently emits a nonzero ``vyaw``;
        this clamp is defense in depth against a future regression, applied at
        the single point every velocity command in the mission passes through,
        rather than trusted to hold at every call site independently.
        """
        if vyaw != 0.0:
            logger.error(
                "Rejected a nonzero vyaw=%.4f from a velocity command; the yaw "
                "invariant forbids rotation for the whole mission. Forcing to 0.0.",
                vyaw,
            )
            vyaw = 0.0

        if self.motion_tracker is not None:
            self.motion_tracker.command(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

        if self.no_fly:
            if self.simulator is not None:
                self.simulator.command(vx, vy, vz, vyaw)
            logger.debug(
                "[NO-FLY] move_velocity: vx=%.3f, vy=%.3f, vz=%.3f, vyaw=%.3f", vx, vy, vz, vyaw
            )
            return
        self.drone.move_velocity(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    def delay(self, seconds: float) -> None:
        """Block for the given duration, or advance the simulation instead.

        Under ``--no-fly`` this is a no-op beyond advancing simulated state. The
        previous implementation always slept for real, so a benchtop run paid
        every hardware settling delay -- including the three seconds
        ``BebopDrone.takeoff`` sleeps internally -- for no benefit.
        """
        if self.no_fly:
            if self.simulator is not None:
                self.simulator.integrate()
            return
        self.drone.delay(seconds)

    def connect(
        self,
        graph_probe: Optional[Callable[[], bool]] = None,
        restart_daemon: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Verify driver connectivity through the SDK, recovering a stale daemon once.

        Parameters
        ----------
        graph_probe : callable, optional
            True when the mission's own node sees the driver in the live graph
            (``driver_discovery.driver_in_graph``). Asked first.
        restart_daemon : callable, optional
            Restarts the ``ros2`` daemon (``driver_discovery.restart_ros2_daemon``).

        Returns
        -------
        bool
            Whether the mission may proceed: the SDK found the driver, or, under
            ``--no-fly``, always. :attr:`driver_reachable` says which.

        Notes
        -----
        The SDK checks with ``ros2 node list``, answered by the daemon's cached
        graph. When the live graph shows the driver and the SDK does not, the
        daemon is stale: it is restarted once and the SDK asked again. On the
        bench, a driver absent from the live graph skips the SDK check.
        """
        seen = graph_probe() if graph_probe is not None else None
        if self.no_fly and seen is False:
            self.driver_reachable = False
            logger.info("[NO-FLY] No driver in the graph. Benchtop proxy active. Motors unpowered.")
            return True

        reachable = self._sdk_connect()
        if not reachable and seen:
            logger.warning("ros2 daemon stale: the driver is in the graph but the SDK does not see it; restarting it.")
            if restart_daemon is not None:
                restart_daemon()
            reachable = self._sdk_connect()
        self.driver_reachable = reachable

        if self.no_fly:
            if not reachable:
                logger.info("[NO-FLY] Driver unreachable; continuing on the bench.")
            logger.info("[NO-FLY] Benchtop proxy active. Motors unpowered.")
            return True
        return reachable

    def _sdk_connect(self) -> bool:
        try:
            return bool(self.drone.connect())
        except Exception as exc:  # noqa: BLE001
            if not self.no_fly:
                raise
            logger.debug("[NO-FLY] SDK connect raised (%s).", exc)
            return False

    def cleanup(self) -> None:
        """Release the underlying drone resources."""
        self.drone.cleanup()
