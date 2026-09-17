"""Two-sided altitude hold: pinning the airframe to its operating height.

The predecessor to this controller,
:class:`~mvp_mission_bebop.controllers.anti_climb.AltitudeAntiClimbGovernor`,
could ask for descent and nothing else. That was not an oversight; it was the
mission's vertical invariant expressed in code, and it was written against a
real failure: the Bebop holds height with a downward ultrasonic rangefinder, so
flying over an obstacle shortens the measured range, the firmware reads that as
lost height, and it climbs. A governor that can only descend cancels that drift
and cannot itself cause one.

It is the wrong shape for the failure observed next. Horizontal translation costs
the Bebop lift: the airframe pitches into a forward command, the thrust vector
tilts, and the firmware does not make up the vertical component. The drone sinks
for as long as the translation lasts. Against a descent-only governor that sink
is *structurally invisible* -- the altitude error has the one sign the governor
has no authority over, so it computes ``vz = 0.0`` and the airframe goes on
sinking. A mission that held 1.55 m to the centimetre in hover reached the
accident scene most of a metre low, and nothing in the velocity log said why,
because nothing in the velocity log was wrong.

So the vertical axis gets a real regulator. Four things make it one rather than a
sign flip on the old law:

*The error is two-sided and the deadbands are not.* Drifting up walks toward the
safety ceiling; drifting down a few centimetres does not. The band above the
setpoint is therefore tight and the one below it is wider, and the error is
measured from the *edge* of whichever band it left, so the command is continuous
across the boundary instead of stepping into it.

*There is integral action.* A translation-induced sink is a sustained
disturbance, and proportional action alone answers a sustained disturbance with
a standing offset -- the drone would stabilize low rather than on target, which
is the complaint restated rather than fixed. The integral is what actually pins
it. It is bounded hard, and it leaks while the altitude sits inside the
deadband, so a bank earned against one translation cannot drive an overshoot
after that translation ends.

*The output is profiled.* An altitude loop that steps its command excites the
oscillation the requirement rules out. Acceleration and jerk on ``vz`` are
bounded exactly as they are on the horizontal axes.

*Climbing is authorized, not assumed.* The controller computes a positive ``vz``;
whether one reaches the wire is the failsafe's decision, through
:meth:`~mvp_mission_bebop.telemetry.failsafe.FailsafeSupervisor.altitude_hold_window`.
Keeping the two separate is what stops "the altitude loop may climb a little"
from quietly becoming "anything may climb".

The controller also answers a question it is uniquely placed to answer: how hard
the drone should be translating. Translation is the disturbance, so when the
vertical loop is losing, the cheapest correction available is to translate less
hard. :meth:`horizontal_scale` reports that, and the steps apply it to their
speed *demand* before profiling, so the throttling is a change of intent rather
than a clamp bolted onto a shaped command.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.engine.rate import LoopRate
from mvp_mission_bebop.parameters import AltitudeGovernorConfig

logger = logging.getLogger("AltitudeHold")

#: Nominal control period assumed when a caller omits ``dt``. Matches the
#: historical behaviour of the anti-climb governor so a call site that has not
#: been updated behaves identically.
_NOMINAL_PERIOD_SEC: float = 1.0 / 15.0


class AltitudeHoldGovernor:
    """Regulates relative altitude onto the mission's operating height.

    Drop-in for :class:`
    ~mvp_mission_bebop.controllers.anti_climb.AltitudeAntiClimbGovernor`: same
    constructor shape, same ``compute_vz(altitude, dt)`` signature, same
    normalized output units. The difference is that the output may be positive,
    bounded by ``config.max_climb_speed``, and that it has integral action.
    """

    def __init__(
        self,
        target_altitude: float,
        config: AltitudeGovernorConfig,
        *,
        clock: Optional[object] = None,
    ) -> None:
        self.target_altitude = target_altitude
        self.config = config

        # The deadband is applied outside the controller, by subtracting it from
        # the error, so the PID sees a signal that is already continuous at the
        # band edge. Feeding a raw error into ``error_deadband`` instead would
        # reproduce the step the anti-climb governor was separately patched for.
        self._pid = FilteredPID(
            PIDGains(
                kp=config.kp,
                ki=config.ki,
                kd=config.kd,
                output_limits=(-abs(config.max_descent_speed), abs(config.max_climb_speed)),
                integral_limits=(-abs(config.integral_limit), abs(config.integral_limit)),
                derivative_cutoff_hz=config.derivative_cutoff_hz,
            ),
            setpoint=0.0,
        )
        self._profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(
                    abs(config.max_descent_speed), abs(config.max_climb_speed), 1e-3
                ),
                max_accel=max(config.max_accel_mps2, 1e-3),
                max_jerk=max(config.max_jerk_mps3, 1e-3),
            )
        )
        self._active = False
        self._climbing = False
        self._error_m = 0.0
        self._clock = clock

    # ------------------------------------------------------------- properties

    @property
    def engaged(self) -> bool:
        """True while the governor is actively correcting the altitude."""
        return self._active

    @property
    def climbing(self) -> bool:
        """True while the correction in force is an ascent."""
        return self._climbing

    @property
    def altitude_error_m(self) -> float:
        """Last measured ``target - altitude``, positive when the drone is low."""
        return self._error_m

    @property
    def climb_authority(self) -> float:
        """Ascent ceiling this governor may ever ask for, in normalized units.

        The failsafe is handed this to size its altitude-hold window, so the
        window and the controller cannot disagree about how much climb is on the
        table.
        """
        return self.config.climb_authority

    # ------------------------------------------------------------------ cycle

    def compute_vz(self, current_relative_alt: float, dt: Optional[float] = None) -> float:
        """Corrective vertical velocity command, in normalized units.

        Parameters
        ----------
        current_relative_alt : float
            Altitude above the calibrated ground reference, in metres.
        dt : Optional[float]
            Elapsed interval. Callers running a paced loop should pass the value
            from ``LoopRate.tick``; when omitted the nominal period is assumed,
            which keeps the signature compatible with every existing call site.

        Returns
        -------
        float
            Normalized ``vz``, bounded by ``-max_descent_speed`` below and
            ``+max_climb_speed`` above. Positive values are a *request*: the
            failsafe suppresses them unless an altitude-hold window is open.
        """
        interval = LoopRate.clamp_interval(
            dt if dt is not None else _NOMINAL_PERIOD_SEC
        )
        cfg = self.config

        if not math.isfinite(current_relative_alt):
            # An absent measurement, not a large one. Coasting on the profile's
            # way to zero is the only safe reading of "I do not know where I am".
            logger.warning(
                "Non-finite altitude %r; holding the vertical axis at rest.",
                current_relative_alt,
            )
            return self._release(interval, reason="altitude unavailable")

        error = self.target_altitude - current_relative_alt
        self._error_m = error

        # Asymmetric deadband, measured from the edge rather than from the
        # setpoint. Subtracting the band keeps the demand continuous where it
        # engages: a controller that jumps to ``kp * deadband`` the instant it
        # crosses the threshold is a step input to the airframe every time the
        # altitude wanders across the boundary, which is precisely the limit
        # cycle this is supposed to prevent.
        if error > cfg.climb_deadband_m:
            excursion = error - cfg.climb_deadband_m
        elif error < -cfg.deadband_m:
            excursion = error + cfg.deadband_m
        else:
            return self._release(interval)

        if not self._active:
            self._active = True
            logger.info(
                "Altitude hold engaged at %.2f m against a %.2f m target (%+.2f m).",
                current_relative_alt,
                self.target_altitude,
                error,
            )

        # Sign convention: the PID's error is ``setpoint - measurement`` with the
        # setpoint at zero, so feeding ``-excursion`` in as the measurement makes
        # a positive excursion -- the drone is low -- produce a positive, i.e.
        # ascending, demand.
        demand = self._pid.update(-excursion, interval)
        command = self._profile.step(demand, interval)
        command = max(-abs(cfg.max_descent_speed), min(abs(cfg.max_climb_speed), command))

        climbing = command > 0.0
        if climbing and not self._climbing:
            logger.info(
                "Altitude hold is climbing: %.2f m is %.2f m below the %.2f m setpoint. "
                "Translation-induced sink is being corrected.",
                current_relative_alt,
                error,
                self.target_altitude,
            )
        self._climbing = climbing
        return command

    def _release(self, interval: float, reason: str = "") -> float:
        """Inside the deadband: unwind toward rest without abandoning the state."""
        if self._active:
            logger.debug(
                "Altitude hold released at %+.3f m of error%s.",
                self._error_m,
                f" ({reason})" if reason else "",
            )
            self._active = False
            self._climbing = False

        # Keep the derivative filter primed against the live measurement so that
        # re-engaging differentiates a real trend rather than the band edge --
        # the defect the anti-climb governor was separately fixed for.
        self._pid.update(0.0, interval)

        # And bleed the integral. A bank earned against a translation that has
        # since ended would otherwise still be there when the band is next left,
        # overshooting in whichever direction it was last needed.
        leak = self.config.integral_leak_sec
        if leak > 0.0:
            self._pid.bleed_integral(math.exp(-interval / leak))

        # Ramp the command down rather than dropping it: a step to zero is as
        # much of a disturbance as a step away from it.
        return self._profile.step(0.0, interval)

    # ------------------------------------------------- horizontal cooperation

    def horizontal_scale(self) -> float:
        """How much of the commanded horizontal speed the altitude error permits.

        Unity while the altitude is holding, tapering to
        ``min_horizontal_scale`` as the error approaches
        ``horizontal_throttle_error_m``. Never zero: a stage that stops
        translating cannot finish, and the vertical axis would then be left to
        resolve the error alone anyway -- which is what it is already doing.

        This is the "lower horizontal flight velocity if it improves altitude
        stability" authority, spent where it is actually needed rather than as a
        blanket reduction that would cost every phase whether or not its altitude
        was in trouble.
        """
        cfg = self.config
        if not cfg.hold_enabled:
            return 1.0

        magnitude = abs(self._error_m)
        band = max(cfg.deadband_m, cfg.climb_deadband_m)
        ceiling = max(cfg.horizontal_throttle_error_m, band + 1e-6)

        if magnitude <= band:
            return 1.0
        if magnitude >= ceiling:
            return max(0.0, min(1.0, cfg.min_horizontal_scale))

        floor = max(0.0, min(1.0, cfg.min_horizontal_scale))
        ramp = 1.0 - (magnitude - band) / (ceiling - band)
        return floor + (1.0 - floor) * ramp

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        """Clear controller state and release the governor."""
        self._pid.reset()
        self._profile.reset()
        self._active = False
        self._climbing = False
        self._error_m = 0.0
