"""Continuous safety supervisor and kinematic invariant enforcement.

Monitors sensor telemetry heartbeat, enforces altitude safety ceiling,
validates kinematic constraints, and coordinates controlled emergency landings.
"""

from __future__ import annotations

import logging
import time
from typing import Tuple

from mvp_mission_bebop.exceptions import KinematicConstraintViolation
from mvp_mission_bebop.parameters import TimeoutsConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

logger = logging.getLogger("FailsafeSupervisor")


class FailsafeSupervisor:
    """Supervisory observer validating multi-sensor integrity and flight envelope invariants."""

    def __init__(
        self,
        drone_actuator,
        odom_supervisor: OdometrySupervisor,
        timeouts_cfg: TimeoutsConfig,
    ) -> None:
        self.actuator = drone_actuator
        self.odom_supervisor = odom_supervisor
        self.timeouts_cfg = timeouts_cfg
        self.last_valid_frame_timestamp: float = time.time()
        self.failsafe_active: bool = False

    def notify_frame_received(self) -> None:
        """Register fresh video frame arrival timestamp."""
        self.last_valid_frame_timestamp = time.time()

    def assert_kinematics(self, vz: float, vyaw: float) -> None:
        """Enforce kinematic safety invariants.

        1. Absolute zero yaw rate (vyaw == 0.0).
        2. Non-positive vertical velocity (vz <= 0.0).
        """
        if abs(vyaw) > 1e-4:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vyaw={vyaw:.4f}. "
                "Yaw rotation is strictly prohibited during flight."
            )
        if vz > 1e-4:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vz={vz:.4f} > 0. "
                "Positive vertical climbing commands are strictly prohibited."
            )

    def evaluate_system_health(self) -> Tuple[bool, str]:
        """Verify telemetry health, altitude ceiling, and optical stream continuity."""
        if self.failsafe_active:
            return False, "Failsafe already active."

        if not self.odom_supervisor.is_telemetry_healthy():
            return False, "Odometry telemetry stream loss (heartbeat timeout)."

        if self.odom_supervisor.is_ceiling_breached():
            rel_alt = self.odom_supervisor.relative_altitude
            ceiling = self.odom_supervisor.altitude_ceiling
            return False, f"Altitude ceiling breached: {rel_alt:.2f} m > {ceiling:.2f} m."

        frame_age = time.time() - self.last_valid_frame_timestamp
        timeout = self.timeouts_cfg.video_stream_timeout_sec
        if frame_age > timeout:
            return False, f"Camera stream loss: frame age {frame_age:.1f} s > {timeout:.1f} s."

        return True, "Nominal"

    def trigger_emergency_land(self, reason: str) -> None:
        """Execute immediate controlled safe landing. Never cuts motors abruptly."""
        self.failsafe_active = True
        logger.critical("FAILSAFE ENGAGED: %s. Halting actuators and commanding landing.", reason)

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Falha de segurança",
                details={"erro": reason},
                priority="CRITICAL",
                wait=False,
            )
        except Exception as vocal_err:
            logger.debug("Acoustic alert dispatch failure: %s", vocal_err)

        try:
            self.actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            self.actuator.land()
        except Exception as exc:
            logger.critical("Secondary exception during emergency landing execution: %s", exc)
