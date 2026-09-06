"""Active Anti-Climb Altitude Governor.

Prevents ultrasound-induced altitude climb when flying over ground obstacles.
Monitors relative altitude and computes a non-positive vertical velocity (vz <= 0).
"""

from __future__ import annotations

import logging
import time

from mvp_mission_bebop.parameters import AltitudeGovernorConfig

logger = logging.getLogger("AltitudeAntiClimbGovernor")


class AltitudeAntiClimbGovernor:
    """SISO controller generating corrective downward velocity when altitude exceeds target."""

    def __init__(self, target_altitude: float, config: AltitudeGovernorConfig) -> None:
        self.target_altitude = target_altitude
        self.config = config
        self._last_error: float = 0.0
        self._last_time: float = time.time()

    def compute_vz(self, current_relative_alt: float) -> float:
        """Compute corrective vertical velocity command (vz <= 0.0)."""
        alt_error = current_relative_alt - self.target_altitude
        if alt_error > self.config.deadband_m:
            dt = max(1e-3, time.time() - self._last_time)
            d_error = (alt_error - self._last_error) / dt
            vz_correction = -(self.config.kp * alt_error + self.config.kd * d_error)
            vz_cmd = max(-self.config.max_descent_speed, min(0.0, vz_correction))
            logger.debug(
                "Anti-Climb Governor active: alt=%.2fm > target=%.2fm. vz=%.2f",
                current_relative_alt,
                self.target_altitude,
                vz_cmd,
            )
        else:
            vz_cmd = 0.0

        self._last_error = alt_error
        self._last_time = time.time()
        return vz_cmd

    def reset(self) -> None:
        """Reset internal derivative states."""
        self._last_error = 0.0
        self._last_time = time.time()
