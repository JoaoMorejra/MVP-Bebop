"""Continuous safety supervisor and kinematic invariant enforcement.

Monitors sensor telemetry heartbeat, enforces altitude safety ceiling,
validates kinematic constraints, and coordinates controlled emergency landings.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final, Tuple

from mvp_mission_bebop.exceptions import KinematicConstraintViolation
from mvp_mission_bebop.parameters import TimeoutsConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor, TelemetryHealth

if TYPE_CHECKING:  # pragma: no cover - avoids a circular import at runtime
    from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy

logger = logging.getLogger("FailsafeSupervisor")

#: Numerical slack below which a command counts as exactly zero.
KINEMATIC_TOLERANCE: Final[float] = 1e-4


class FailsafeSupervisor:
    """Supervisory observer validating multi-sensor integrity and flight envelope invariants."""

    def __init__(
        self,
        drone_actuator: "BenchtopDroneProxy",
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

    def clamp_kinematics(self, vz: float, vyaw: float) -> Tuple[float, float]:
        """Saturate a command onto the kinematic invariants.

        This is the guard for the normal command path. Its counterpart
        :meth:`assert_kinematics` raises, which is correct for a contract
        violation but wrong as a flight-time guard: a single ``vz`` of 1e-3
        arriving from a rounding error used to propagate out of the step, through
        the pipeline's generic exception handler, and into an emergency landing.
        A safety supervisor that ends the mission over a sign error is a
        liability rather than a protection.

        Returns
        -------
        Tuple[float, float]
            ``(vz, vyaw)`` saturated to ``vz <= 0`` and ``vyaw == 0``.
        """
        safe_vz = min(0.0, vz)
        if vz > KINEMATIC_TOLERANCE:
            logger.warning(
                "Climb command vz=%.4f suppressed; vertical authority is descent-only.", vz
            )
        if abs(vyaw) > KINEMATIC_TOLERANCE:
            logger.warning(
                "Yaw command vyaw=%.4f suppressed; rotation is prohibited in flight.", vyaw
            )
        return safe_vz, 0.0

    def assert_kinematics(self, vz: float, vyaw: float) -> None:
        """Assert the kinematic safety invariants, raising on violation.

        Reserved for checkpoints and tests. Use :meth:`clamp_kinematics` on the
        command path.

        1. Absolute zero yaw rate (vyaw == 0.0).
        2. Non-positive vertical velocity (vz <= 0.0).
        """
        if abs(vyaw) > KINEMATIC_TOLERANCE:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vyaw={vyaw:.4f}. "
                "Yaw rotation is strictly prohibited during flight."
            )
        if vz > KINEMATIC_TOLERANCE:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vz={vz:.4f} > 0. "
                "Positive vertical climbing commands are strictly prohibited."
            )

    def evaluate_system_health(self) -> Tuple[bool, str]:
        """Verify telemetry health, altitude ceiling, and optical stream continuity."""
        if self.failsafe_active:
            return False, "Failsafe already active."

        health = self.odom_supervisor.telemetry_health()
        if health is TelemetryHealth.NEVER_RECEIVED:
            return False, "No odometry has ever been received. Verify /bebop/odom is publishing."
        if health is TelemetryHealth.STALE:
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
