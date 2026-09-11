"""Actuator proxy for real flight and benchtop (``--no-fly``) execution."""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from mvp_mission_bebop.actuators.simulator import KinematicSimulator

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


class BenchtopDroneProxy:
    """Wraps the drone so a mission can run with motors unpowered.

    In ``--no-fly`` mode motor commands are diverted into a kinematic
    simulation, while the gimbal and snapshot paths still drive real hardware --
    those are the parts a benchtop run is meant to exercise.
    """

    def __init__(
        self,
        drone: DroneActuator,
        no_fly: bool = False,
        simulator: Optional[KinematicSimulator] = None,
    ) -> None:
        self.drone = drone
        self.no_fly = no_fly
        self.simulator = simulator

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
        """
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

    def connect(self) -> bool:
        """Verify driver connectivity."""
        if self.no_fly:
            try:
                if self.drone.connect():
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("[NO-FLY] Driver unreachable (%s); continuing on the bench.", exc)
            logger.info("[NO-FLY] Benchtop proxy active. Motors unpowered.")
            return True
        return self.drone.connect()

    def cleanup(self) -> None:
        """Release the underlying drone resources."""
        self.drone.cleanup()
