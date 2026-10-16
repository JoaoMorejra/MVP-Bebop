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

*Near the pad edge the law is retuned, not replaced.* Leaving or re-entering the
takeoff pad moves the sonar echo by the pad height, and the firmware answers by
moving the airframe (``estimation.platform_monitor``). While the platform
monitor reports the guard active, the governor runs with the
``PlatformConfig.guard_*`` authority, gain and deadband, adds the edge
feedforward, renders a sub-quantum demand as pulses rather than an exact zero,
and stops translation outright while the vertical error is large. The limits
reach the output through the same jerk-limited profile, so neither the
activation nor the release is a step. Only this governor carries the guard: the
descent-only :class:`~mvp_mission_bebop.controllers.anti_climb.AltitudeAntiClimbGovernor`
(``governor.hold_enabled = false``) cannot climb against a sink and is left
exactly as it was.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Callable, Final, Optional, Tuple

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.engine.rate import LoopRate
from mvp_mission_bebop.parameters import AltitudeGovernorConfig, PlatformConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mvp_mission_bebop.estimation.calibration import SpeedCalibration
    from mvp_mission_bebop.estimation.platform_monitor import PlatformReport

logger = logging.getLogger("AltitudeHold")

#: Nominal control period assumed when a caller omits ``dt``. Matches the
#: historical behaviour of the anti-climb governor so a call site that has not
#: been updated behaves identically.
_NOMINAL_PERIOD_SEC: float = 1.0 / 15.0

#: Vertical command quantum of the driver, ``int8(v * 100)``. The edge guard's
#: shaper emits pulses of exactly this amplitude.
_VERTICAL_QUANTUM: Final[float] = 0.01


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
        guard_source: Optional[Callable[[], "PlatformReport"]] = None,
        platform_cfg: Optional[PlatformConfig] = None,
        calibration: Optional["SpeedCalibration"] = None,
        descent_demand: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Build the governor.

        Parameters
        ----------
        target_altitude : float
            Setpoint in the control frame, metres above the launch point.
        config : AltitudeGovernorConfig
            Global gains, limits and deadbands.
        clock : Optional[object]
            Unused; kept for constructor compatibility with the anti-climb
            governor.
        guard_source : Optional[Callable[[], PlatformReport]]
            Returns the platform monitor's latest report; read at the start of
            every :meth:`compute_vz`. ``None`` disables the edge guard.
        platform_cfg : Optional[PlatformConfig]
            Guard authority, gain, deadband, stop thresholds and feedforward.
            The guard is configured only with a source, this section, and a
            known ``edge_ahead_m``; otherwise the governor is exactly the
            unguarded law.
        calibration : Optional[SpeedCalibration]
            Converts the edge feedforward from m/s into a normalized vertical
            command. ``None`` takes the two as equal.
        descent_demand : Optional[Callable[[], bool]]
            True while the airframe is re-entering the pad above the safety
            ceiling (``OdometrySupervisor.reentry_descent_demanded``). Inside
            the guard, in ``ENTERING`` only, the demand then becomes the full
            ``guard_descent_cap`` instead of the proportional law. ``None``
            disables it.

        Raises
        ------
        ValueError
            If the guard is configured with limits that leave no authority
            (``guard_descent_cap`` and ``guard_climb_authority`` both zero).
        """
        self.target_altitude = target_altitude
        self.config = config

        # The deadband is applied outside the controller, by subtracting it from
        # the error, so the PID sees a signal that is already continuous at the
        # band edge. Feeding a raw error into ``error_deadband`` instead would
        # reproduce the step the anti-climb governor was separately patched for.
        self._global_gains = PIDGains(
            kp=config.kp,
            ki=config.ki,
            kd=config.kd,
            output_limits=(-abs(config.max_descent_speed), abs(config.max_climb_speed)),
            integral_limits=(-abs(config.integral_limit), abs(config.integral_limit)),
            derivative_cutoff_hz=config.derivative_cutoff_hz,
        )
        self._pid = FilteredPID(self._global_gains, setpoint=0.0)

        self._guard_source = guard_source
        self._platform = platform_cfg
        self._calibration = calibration
        self._descent_demand = descent_demand
        self._forcing_descent = False
        self._guard_configured = (
            guard_source is not None
            and platform_cfg is not None
            and platform_cfg.edge_ahead_m is not None
        )
        envelope = (abs(config.max_descent_speed), abs(config.max_climb_speed))
        self._guard_gains: Optional[PIDGains] = None
        if self._guard_configured and platform_cfg is not None:
            guard_limits = (abs(platform_cfg.guard_descent_cap), abs(platform_cfg.guard_climb_authority))
            self._guard_gains = PIDGains(
                kp=config.kp * platform_cfg.guard_kp_scale,
                ki=config.ki,
                kd=config.kd,
                output_limits=(-guard_limits[0], guard_limits[1]),
                integral_limits=(-abs(config.integral_limit), abs(config.integral_limit)),
                derivative_cutoff_hz=config.derivative_cutoff_hz,
            )
            envelope = (max(envelope[0], guard_limits[0]), max(envelope[1], guard_limits[1]))
        #: ``(descent, climb)`` the output may ever reach. With the guard
        #: configured it spans both limit sets, so a release ramps down through
        #: the profile instead of being clipped in one cycle.
        self._envelope: Tuple[float, float] = envelope

        self._profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(envelope[0], envelope[1], 1e-3),
                max_accel=max(config.max_accel_mps2, 1e-3),
                max_jerk=max(config.max_jerk_mps3, 1e-3),
            )
        )
        self._shaper = QuantizedCommandShaper(_VERTICAL_QUANTUM, _VERTICAL_QUANTUM)
        self._active = False
        self._climbing = False
        self._error_m = 0.0
        self._clock = clock
        self._guard_engaged = False
        self._translation_stopped = False

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
    def guard_engaged(self) -> bool:
        """True while the platform edge guard governs the vertical axis."""
        return self._guard_engaged

    @property
    def gains(self) -> PIDGains:
        """PID gains and output limits in force."""
        return self._pid.gains

    @property
    def deadbands(self) -> Tuple[float, float]:
        """``(above, below)`` deadbands in force, metres."""
        if self._guard_engaged and self._platform is not None:
            band = self._platform.guard_deadband_m
            return band, band
        return self.config.deadband_m, self.config.climb_deadband_m

    @property
    def climb_authority(self) -> float:
        """Ascent ceiling this governor may ever ask for, in normalized units.

        The failsafe is handed this to size its altitude-hold window, so the
        window and the controller cannot disagree about how much climb is on the
        table.
        """
        return self.config.climb_authority

    # ------------------------------------------------------------------ cycle

    def compute_vz(
        self,
        current_relative_alt: float,
        dt: Optional[float] = None,
        vx_commanded: float = 0.0,
    ) -> float:
        """Corrective vertical velocity command, in normalized units.

        Parameters
        ----------
        current_relative_alt : float
            Altitude above the calibrated ground reference, in metres.
        dt : Optional[float]
            Elapsed interval. Callers running a paced loop should pass the value
            from ``LoopRate.tick``; when omitted the nominal period is assumed,
            which keeps the signature compatible with every existing call site.
        vx_commanded : float
            Magnitude of the horizontal velocity demand being issued this same
            cycle, normalized to the driver's [-1, 1] scale. The name is
            historical: in the lateral flight pattern the callers pass the
            norm of the ``(vx, vy)`` demand, and only ``abs`` is used. Multiplied by
            ``config.feedforward_gain`` to anticipate the sink translation
            induces, ahead of the integral term's reactive correction.
            Defaults to zero, which reproduces this method's behaviour before
            the parameter existed.

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
        report = self._read_guard()
        try:
            self._engage(report is not None and report.guard_active)
            feedforward = cfg.feedforward_gain * abs(vx_commanded)
            if self._guard_engaged and report is not None:
                feedforward += self._edge_feedforward(report)
            if self._reentry_descent(report):
                return self._force_descent(current_relative_alt, interval)
            if self._forcing_descent:
                self._forcing_descent = False
                logger.info("[PLATFORM reentry_descent released]")
            return self._regulate(current_relative_alt, interval, feedforward)
        except BaseException:
            # Whatever failed, the next cycle must not inherit guard authority
            # it did not re-establish.
            self._engage(False)
            raise

    def _regulate(self, current_relative_alt: float, interval: float, feedforward: float) -> float:
        """One cycle of the law under the limits currently in force."""
        if not math.isfinite(current_relative_alt):
            # An absent measurement, not a large one. Coasting on the profile's
            # way to zero is the only safe reading of "I do not know where I am".
            logger.warning(
                "Non-finite altitude %r; holding the vertical axis at rest.",
                current_relative_alt,
            )
            return self._release(interval, feedforward, reason="altitude unavailable")

        error = self.target_altitude - current_relative_alt
        self._error_m = error
        if self._guard_engaged and self._platform is not None:
            # Translation is the disturbance the vertical loop is losing to;
            # near the edge it stops outright until the error is back under the
            # resume threshold.
            magnitude = abs(error)
            if magnitude > self._platform.guard_stop_error_m:
                self._translation_stopped = True
            elif magnitude < self._platform.guard_resume_error_m:
                self._translation_stopped = False

        # Asymmetric deadband, measured from the edge rather than from the
        # setpoint. Subtracting the band keeps the demand continuous where it
        # engages: a controller that jumps to ``kp * deadband`` the instant it
        # crosses the threshold is a step input to the airframe every time the
        # altitude wanders across the boundary, which is precisely the limit
        # cycle this is supposed to prevent.
        above, below = self.deadbands
        if error > below:
            excursion = error - below
        elif error < -above:
            excursion = error + above
        else:
            return self._release(interval, feedforward)

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
        command = self._clamp(self._profile.step(self._bound(demand + feedforward), interval))

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
        return self._render(command, interval)

    def _release(self, interval: float, feedforward: float = 0.0, reason: str = "") -> float:
        """Inside the deadband: unwind toward rest, still honouring feedforward."""
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
        command = self._clamp(self._profile.step(self._bound(feedforward), interval))
        return self._render(command, interval)

    # ------------------------------------------------------------ edge guard

    def _read_guard(self) -> Optional["PlatformReport"]:
        """The platform report, or ``None`` when the guard is not configured.

        A fault in the source reads as "no guard": the global law is the one
        that has flown, and a broken monitor must not be able to change it.
        """
        if not self._guard_configured or self._guard_source is None:
            return None
        try:
            return self._guard_source()
        except Exception as exc:  # noqa: BLE001 - the guard is an addition, never a dependency
            logger.debug("Platform guard source failed (%s); flying the global law.", exc)
            return None

    def _engage(self, engaged: bool) -> None:
        """Switch the gains in force without resetting the controller state."""
        if engaged == self._guard_engaged:
            return
        self._guard_engaged = engaged
        if engaged and self._guard_gains is not None:
            self._pid.retune(self._guard_gains)
            logger.info(
                "Platform edge guard engaged: vertical authority [-%.2f, +%.2f], kp %.2f.",
                -self._guard_gains.output_limits[0],
                self._guard_gains.output_limits[1],
                self._guard_gains.kp,
            )
        else:
            self._pid.retune(self._global_gains)
            self._shaper.reset()
            self._translation_stopped = False
            if self._guard_gains is not None:
                logger.info("Platform edge guard released: global vertical limits restored.")

    def _reentry_descent(self, report: Optional["PlatformReport"]) -> bool:
        """Whether this cycle answers a re-entry ceiling breach with the full descent."""
        from mvp_mission_bebop.estimation.platform_monitor import PlatformState

        if not self._guard_engaged or report is None or report.state is not PlatformState.ENTERING:
            return False
        if self._descent_demand is None:
            return False
        try:
            return bool(self._descent_demand())
        except Exception as exc:  # noqa: BLE001 - a broken source falls back to the law
            logger.debug("Re-entry descent source failed (%s); flying the guard law.", exc)
            return False

    def _force_descent(self, current_relative_alt: float, interval: float) -> float:
        """Full guard descent against a re-entry false climb.

        Re-entering the pad, the sonar reading drops by the pad height and the
        firmware climbs on its own; above the ceiling the proportional law is
        not enough and the emergency landing is withheld (see
        ``OdometrySupervisor.is_ceiling_breached``), so the whole descent
        authority of the guard is spent. The command still ramps through the
        jerk-limited profile, and the PID keeps tracking the error so the law
        resumes from a primed state once the altitude is back under the
        ceiling.
        """
        if not self._forcing_descent:
            self._forcing_descent = True
            logger.warning(
                "[PLATFORM reentry_descent z_rel=%.3f vz=%.3f] Re-entry climb above the "
                "ceiling: commanding the full guard descent.",
                current_relative_alt,
                -self._limits()[0],
            )
        if math.isfinite(current_relative_alt):
            error = self.target_altitude - current_relative_alt
            self._error_m = error
            if self._platform is not None and abs(error) > self._platform.guard_stop_error_m:
                self._translation_stopped = True
            self._pid.update(-error, interval)
        self._active = True
        self._climbing = False
        descent, _climb = self._limits()
        command = self._clamp(self._profile.step(self._bound(-descent), interval))
        return self._render(command, interval)

    def _edge_feedforward(self, report: "PlatformReport") -> float:
        """Anticipatory vertical command after a pad-edge transition, normalized."""
        from mvp_mission_bebop.estimation.platform_monitor import PlatformState

        platform = self._platform
        seconds = report.seconds_since_transition
        if platform is None or seconds is None or seconds >= platform.feedforward_sec:
            return 0.0
        if report.state is PlatformState.LEAVING and platform.sag_feedforward_mps > 0.0:
            mps = platform.sag_feedforward_mps
        elif report.state is PlatformState.ENTERING and platform.rise_feedforward_mps > 0.0:
            mps = -platform.rise_feedforward_mps
        else:
            return 0.0
        if self._calibration is None:
            return mps
        return self._calibration.to_normalized(mps, axis="vertical")

    def _limits(self) -> Tuple[float, float]:
        """``(descent, climb)`` in force this cycle."""
        low, high = self._pid.gains.output_limits
        return -low, high

    def _bound(self, target: float) -> float:
        """Bound the profile's target to the limits in force.

        Only with the guard configured: the profile then spans both limit sets,
        and bounding its target rather than its output is what lets a release
        ramp down instead of stepping. Unconfigured, the profile is sized to the
        global limits exactly as before and does its own bounding.
        """
        if not self._guard_configured:
            return target
        descent, climb = self._limits()
        return max(-descent, min(climb, target))

    def _clamp(self, command: float) -> float:
        """Saturate the profiled command onto the output envelope."""
        descent, climb = self._envelope
        return max(-descent, min(climb, command))

    def _render(self, command: float, interval: float) -> float:
        """Shape a sub-quantum demand into pulses inside the guard.

        The firmware's own sonar hover engages only on an exactly zero ``gaz``,
        and the driver transmits any demand under one percent as that zero.
        Inside the guard the shaper emits the demand as +/-0.01 pulses whose
        density reproduces it, bounded to the limits in force.
        """
        if not (
            self._guard_engaged
            and self._platform is not None
            and self._platform.guard_vertical_shaper
        ):
            return command
        descent, climb = self._limits()
        return max(-descent, min(climb, self._shaper.shape(command, interval)))

    # ------------------------------------------------- horizontal cooperation

    def horizontal_scale(self) -> float:
        """How much of the commanded horizontal speed the altitude error permits.

        Unity while the altitude is holding, tapering to
        ``min_horizontal_scale`` as the error approaches
        ``horizontal_throttle_error_m``. Never zero outside the platform edge
        guard: a stage that stops translating cannot finish, and the vertical
        axis would then be left to resolve the error alone anyway -- which is
        what it is already doing. Inside the guard it is zero from
        ``guard_stop_error_m`` until the error is back under
        ``guard_resume_error_m``.

        This is the "lower horizontal flight velocity if it improves altitude
        stability" authority, spent where it is actually needed rather than as a
        blanket reduction that would cost every phase whether or not its altitude
        was in trouble.
        """
        cfg = self.config
        if not cfg.hold_enabled:
            return 1.0
        if self._guard_engaged and self._translation_stopped:
            # The one exception to "never zero": at the pad edge the error is
            # the firmware answering a sonar step, and translating on carries
            # the airframe further into it. Released below
            # ``guard_resume_error_m`` or when the guard closes.
            return 0.0

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
        self._shaper.reset()
        self._active = False
        self._climbing = False
        self._error_m = 0.0
        self._translation_stopped = False
