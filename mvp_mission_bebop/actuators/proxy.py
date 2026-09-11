"""Actuator proxy for real flight and hardware-in-the-loop benchtop execution."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("ActuatorProxy")


class BenchtopDroneProxy:
    """Encapsulates drone actuator commands with support for benchtop (--no-fly) execution.

    In --no-fly mode:
      - Motor commands (takeoff, land, move_velocity) are simulated without propeller actuation.
      - Optical gimbal commands (camera_control) and snapshot triggers are physically executed.
    """

    def __init__(self, drone, no_fly: bool = False) -> None:
        self.drone = drone
        self.no_fly = no_fly

    def flat_trim(self) -> None:
        """Calibrate IMU flat trim."""
        try:
            self.drone.flat_trim()
        except Exception as exc:
            if not self.no_fly:
                raise exc
            logger.debug("[NO-FLY] Simulated flat trim.")

    def takeoff(self, altitude: float) -> bool:
        """Execute autonomous takeoff."""
        if self.no_fly:
            logger.info("[NO-FLY BENCHTOP] Simulated Takeoff to %.2f m. Motors unpowered.", altitude)
            return True
        return self.drone.takeoff(altitude=altitude)

    def land(self) -> bool:
        """Execute landing."""
        if self.no_fly:
            logger.info("[NO-FLY BENCHTOP] Simulated Landing. Motors unpowered.")
            return True
        return self.drone.land()

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        """Actuate camera gimbal. Physically executed in both real flight and benchtop."""
        try:
            self.drone.camera_control(tilt=tilt, pan=pan)
        except Exception as exc:
            if not self.no_fly:
                raise exc
            logger.debug("[NO-FLY] Simulated camera gimbal: tilt=%.1f, pan=%.1f", tilt, pan)

    def snapshot(self) -> None:
        """Trigger onboard camera snapshot."""
        try:
            self.drone.snapshot()
        except Exception as exc:
            if not self.no_fly:
                raise exc
            logger.debug("[NO-FLY] Simulated snapshot trigger.")

    def move_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        vyaw: float = 0.0,
        duration: Optional[float] = None,
    ) -> None:
        """Command normalized velocity vector."""
        if self.no_fly:
            logger.debug(
                "[NO-FLY] move_velocity simulated: vx=%.3f, vy=%.3f, vz=%.3f, vyaw=%.3f",
                vx,
                vy,
                vz,
                vyaw,
            )
            return
        self.drone.move_velocity(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    def delay(self, seconds: float) -> None:
        """Execute non-blocking delay."""
        self.drone.delay(seconds)

    def connect(self) -> bool:
        """Verify driver connectivity."""
        if self.no_fly:
            try:
                if self.drone.connect():
                    return True
            except Exception:
                pass
            logger.info("[NO-FLY BENCHTOP] Simulated benchtop drone proxy active. Motors unpowered.")
            return True
        return self.drone.connect()

    def cleanup(self) -> None:
        """Release underlying drone resources."""
        self.drone.cleanup()
