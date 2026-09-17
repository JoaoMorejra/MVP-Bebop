"""Anti-climb altitude governor.

The Bebop holds altitude with a downward ultrasonic rangefinder. Flying over an
obstacle shortens the measured range, the firmware reads that as having lost
height, and it climbs to compensate -- a drift the mission never commanded and
cannot see in its own velocity log. This governor watches the odometric altitude
instead and asks for descent whenever the drone has gained height it was not
told to gain.

The output is non-positive by construction: the governor can only ever bring the
drone down. Climbing is not a correction it is permitted to make, because the
mission's vertical invariant forbids it.

That one-way rule is no longer the mission default. Horizontal translation costs
the Bebop lift, and a sink is exactly the error this governor has no authority
over -- it computes ``vz = 0.0`` and the airframe goes on descending, which is
how a mission that hovered at 1.55 m arrived at the scene most of a metre low.
:class:`~mvp_mission_bebop.controllers.altitude_hold.AltitudeHoldGovernor`
replaces it and holds both sides of the setpoint.

This class is retained deliberately rather than deleted. It is what
``governor.hold_enabled = false`` selects: the escape hatch for a field session
where the two-sided law misbehaves, and the reference against which its
behaviour above the setpoint is checked. It exposes the same cooperative
interface so the two are interchangeable at every call site.
"""

from __future__ import annotations

import logging
from typing import Optional

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.engine.rate import LoopRate
from mvp_mission_bebop.parameters import AltitudeGovernorConfig

logger = logging.getLogger("AltitudeGovernor")


class AltitudeAntiClimbGovernor:
    """Single-axis governor producing corrective descent, never climb."""

    def __init__(
        self,
        target_altitude: float,
        config: AltitudeGovernorConfig,
        *,
        clock: Optional[object] = None,
    ) -> None:
        self.target_altitude = target_altitude
        self.config = config

        # Filtered derivative and an explicit dt. The previous implementation
        # took a raw difference against a wall clock sampled twice per call, and
        # updated its derivative memory on every invocation including those
        # suppressed by the deadband -- so the first sample after crossing the
        # threshold differentiated a step and produced a spike, applying full
        # descent authority the instant the drone drifted a few centimetres.
        self._pid = FilteredPID(
            PIDGains(
                kp=config.kp,
                ki=0.0,
                kd=config.kd,
                output_limits=(-config.max_descent_speed, 0.0),
                derivative_cutoff_hz=2.0,
            ),
            setpoint=0.0,
        )
        self._active = False
        self._error_m = 0.0
        self._clock = clock

    @property
    def engaged(self) -> bool:
        """True while the governor is actively commanding descent."""
        return self._active

    @property
    def climbing(self) -> bool:
        """Always False. This governor has no ascent authority at all."""
        return False

    @property
    def altitude_error_m(self) -> float:
        """Last measured ``target - altitude``, positive when the drone is low."""
        return self._error_m

    @property
    def climb_authority(self) -> float:
        """Zero: selecting this governor is what declines climb authority."""
        return 0.0

    def horizontal_scale(self) -> float:
        """Always unity.

        The two-sided governor throttles translation when its altitude loop is
        losing, because translation is the disturbance. This one cannot act on a
        sink at all, so throttling would cost speed without buying altitude.
        Present so the two governors are interchangeable at every call site.
        """
        return 1.0

    def compute_vz(self, current_relative_alt: float, dt: Optional[float] = None) -> float:
        """Corrective vertical velocity command, always ``<= 0``.

        Parameters
        ----------
        current_relative_alt : float
            Altitude above the calibrated ground reference, in metres.
        dt : Optional[float]
            Elapsed interval. Callers running a paced loop should pass the value
            from ``LoopRate.tick``; when omitted the nominal period is assumed,
            which keeps the signature compatible with existing call sites.
        """
        interval = LoopRate.clamp_interval(dt) if dt is not None else LoopRate.clamp_interval(1.0 / 15.0)
        excess = current_relative_alt - self.target_altitude
        self._error_m = -excess

        if excess <= self.config.deadband_m:
            if self._active:
                logger.debug(
                    "Altitude governor released at %.2f m (target %.2f m).",
                    current_relative_alt,
                    self.target_altitude,
                )
                self._active = False
            # Keep the filter primed with the current measurement so re-engaging
            # differentiates a real trend rather than the deadband edge itself.
            self._pid.update(0.0, interval)
            return 0.0

        if not self._active:
            self._active = True
            logger.info(
                "Altitude governor engaged: %.2f m exceeds target %.2f m by %.2f m.",
                current_relative_alt,
                self.target_altitude,
                excess,
            )

        command = self._pid.update(excess, interval)
        return max(-self.config.max_descent_speed, min(0.0, command))

    def reset(self) -> None:
        """Clear the derivative memory and release the governor."""
        self._pid.reset()
        self._active = False
        self._error_m = 0.0
