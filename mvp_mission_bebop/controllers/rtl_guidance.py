"""Return-to-launch guidance law.

Replaces the previous RTL, which had no control law at all. Its ``kp`` and
``kd`` fields were declared and never read; what actually flew was a three-regime
piecewise function of displacement with no acceleration limit, a lateral channel
that was bang-bang over most of its range, and an anti-stall floor that made
deceleration impossible -- the drone reached the arrival radius still moving,
because the floor forbade anything gentler than 3.5% throttle.

The law here is feedforward plus feedback in physical units:

*Feedforward* is the braking profile ``v = sqrt(2 a d)``: the fastest the drone
may travel and still stop within the distance remaining. This is a statement
about whether the vehicle *can* stop, which a ramp defined on displacement is
not.

*Feedback* is a filtered PD on the along-track and cross-track errors. It
requests a velocity; the braking profile caps it. Near the origin the PD
dominates and the approach is gentle; far away the cap saturates at cruise.

*Shaping* applies a jerk limit, then converts to the actuator's normalized
domain, then renders sub-threshold demands through a sigma-delta modulator so
slow motion survives the driver's int8 quantization without a hard floor.

``vyaw`` is never assigned anything but zero, structurally rather than by
assertion, so the return leg cannot rotate.

*Reference* is where the origin is, and it is now a parameter rather than an
assumption. The law used to read ``/bebop/odom`` directly and had no way to be
told otherwise. It is handed a :class:`ReturnReference` instead, which the step
builds from whichever estimator the mission trusts -- the aggregated motion
sequence by default, odometry when dead reckoning is unavailable. The guidance
mathematics is identical either way, which is the point: choosing an estimator
is a mission decision and not a control-law one.

``vx`` is backward-only with one bounded exception. Holding it strictly
non-positive was the original rule, and it deadlocked: a braking profile plus
odometry lag almost always leaves the drone a little *past* the origin, and with
forward flight prohibited the along-track axis was pinned at zero, the distance
stopped changing, and arrival could never confirm. The drone hovered at the
takeoff point until the return window expired. The invariant now permits a
forward trim capped at ``overshoot_recovery_speed`` and only within
``overshoot_recovery_radius_m`` -- enough to null a terminal overshoot, far too
little to constitute forward flight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Final, Optional

from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.convergence import (
    SettlementCriteria,
    SettlementDetector,
    SettlementReport,
)
from mvp_mission_bebop.parameters import (
    AltitudeGovernorConfig,
    FlightKinematicsConfig,
    ReturnToLaunchConfig,
)
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot

logger = logging.getLogger("RTLGuidance")

#: Speed cap during station keeping, as a fraction of the cruise cap. The hold
#: only has to cancel residual drift, and a slow cap keeps the correction from
#: exciting the airframe just before touchdown.
STATION_KEEPING_SPEED_RATIO: Final[float] = 0.40


class RTLPhase(str, Enum):
    """Stage of the return sequence.

    String-valued so it can be logged and compared without conversion.
    """

    NAVIGATING = "navigating"
    STATION_KEEPING = "station_keeping"
    TOUCHDOWN = "touchdown"
    COMPLETE = "complete"


@dataclass(frozen=True)
class ReturnReference:
    """Where the launch origin is, and how that was worked out.

    Both estimators available to this mission answer the same question and are
    wrong in different ways, so the law consumes a common shape rather than
    either of them directly.

    ``/bebop/odom`` is an optical-flow estimate. It is metric and it closes the
    loop on what the airframe actually did, which is exactly what a guidance law
    wants -- and it drifts, silently, over a mission spent looking at featureless
    tarmac from 1.55 m at the tilt angles the approach flies. Nothing on this
    platform cross-checks it.

    Dead reckoning integrates the commands the mission itself transmitted. It
    cannot drift, because it is not an estimate of the world; it is an exact
    record of intent, and it is wrong only to the extent that the speed
    calibration behind it is wrong -- which is a fixed, measurable error rather
    than a growing one. Its weakness is the mirror image: it believes the drone
    obeyed, so wind, a firmware refusal or a physical obstruction are invisible
    to it.
    """

    #: Along-track error in body FLU: positive when the origin lies ahead.
    ex_body_m: float
    #: Cross-track error in body FLU: positive when the origin lies to the left.
    ey_body_m: float
    #: Planar range to the origin, metres.
    distance_m: float
    #: Estimated position in the launch frame, metres. Fed to the settlement
    #: detector, whose dispersion term is a statement about this estimator's
    #: own steadiness and must therefore come from the same source as the error.
    x_m: float
    y_m: float
    #: Which estimator produced it, for the flight log.
    source: str = "odometry"

    @classmethod
    def from_snapshot(cls, snapshot: OdometrySnapshot) -> "ReturnReference":
        """Build from odometry, reproducing the historical behaviour exactly."""
        ex, ey, distance = snapshot.body_frame_launch_error()
        return cls(
            ex_body_m=ex,
            ey_body_m=ey,
            distance_m=distance,
            x_m=snapshot.x,
            y_m=snapshot.y,
            source="odometry",
        )


@dataclass(frozen=True)
class GuidanceCommand:
    """One cycle's output: normalized actuator commands plus the reasoning."""

    vx: float
    vy: float
    vz: float
    vyaw: float
    #: Planar range to the launch origin, metres.
    distance_m: float
    #: Along-track error in body FLU: negative when the origin lies behind.
    ex_body_m: float
    #: Cross-track error in body FLU: positive when the origin lies to the left.
    ey_body_m: float
    #: Physical along-track speed the profile settled on, m/s.
    target_speed_mps: float
    phase: RTLPhase
    #: Full multivariate settlement: inside the radius, low dispersion, low mean
    #: speed, sustained across the window.
    arrived: bool
    #: The weaker, and decisive, landing criterion: inside the arrival radius at
    #: low horizontal speed for ``landing_commit_dwell_sec``. Settlement is the
    #: better evidence when it is available; this is what guarantees the drone
    #: reaches the ground when it is not.
    ready_to_land: bool
    note: str
    #: Estimator the return vector came from this cycle.
    reference_source: str = "odometry"

    @property
    def is_stationary(self) -> bool:
        """True when no translation is being commanded this cycle."""
        return self.vx == 0.0 and self.vy == 0.0


class RTLGuidanceController:
    """Closed-loop backward navigation to the launch origin.

    All internal computation is in metres and metres per second. Conversion to
    the Bebop's normalized command domain happens once, at the output, through
    :class:`~mvp_mission_bebop.estimation.calibration.SpeedCalibration`.
    """

    def __init__(
        self,
        rtl_cfg: ReturnToLaunchConfig,
        kinematics_cfg: FlightKinematicsConfig,
        calibration: Optional[SpeedCalibration] = None,
        governor_cfg: Optional[AltitudeGovernorConfig] = None,
    ) -> None:
        self.rtl_cfg = rtl_cfg
        self.kinematics_cfg = kinematics_cfg
        self.calibration = calibration or SpeedCalibration(kinematics_cfg.normalized_to_mps)

        # How much corrective ascent the vertical command may carry through this
        # law. Zero unless a governor configuration says otherwise, which is
        # what keeps the historical descent-only behaviour the default for any
        # caller that does not pass one.
        #
        # This is not the enforcement point -- the failsafe's altitude-hold
        # window is, and it has the final say at the actuator boundary. It is
        # here because a guidance law that silently discards half of a command
        # it was handed is a law that cannot be reasoned about: the return leg
        # translates, translation costs lift, and passing the governor's trim
        # through is the difference between arriving home at the operating
        # altitude and arriving home low.
        self._climb_ceiling = (
            0.0 if governor_cfg is None else max(0.0, governor_cfg.climb_authority)
        )

        # Configuration arrives in normalized units for historical reasons (the
        # names are part of the GCS contract); the law works in m/s.
        self._cruise_mps = self.calibration.to_mps(rtl_cfg.max_speed)
        self._max_lateral_mps = self.calibration.to_mps(rtl_cfg.max_lateral_speed)

        self._longitudinal = FilteredPID(
            PIDGains(
                kp=rtl_cfg.kp,
                ki=0.0,
                kd=rtl_cfg.kd,
                output_limits=(-self._cruise_mps, self._cruise_mps),
                error_deadband=rtl_cfg.deadband_m,
            )
        )
        self._lateral = FilteredPID(
            PIDGains(
                kp=rtl_cfg.lateral_kp,
                ki=0.0,
                kd=rtl_cfg.lateral_kd,
                output_limits=(-self._max_lateral_mps, self._max_lateral_mps),
                error_deadband=rtl_cfg.deadband_m,
            )
        )

        self._longitudinal_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._cruise_mps,
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )
        self._lateral_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._max_lateral_mps,
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )

        self._shaper_x = QuantizedCommandShaper(
            rtl_cfg.min_effective_speed, rtl_cfg.quantization_step
        )
        self._shaper_y = QuantizedCommandShaper(
            rtl_cfg.min_effective_speed, rtl_cfg.quantization_step
        )

        self._settlement = SettlementDetector(
            SettlementCriteria(
                window_sec=rtl_cfg.settle_window_sec,
                min_samples=rtl_cfg.settle_min_samples,
                max_speed=rtl_cfg.settle_max_speed_mps,
                max_position_sigma=rtl_cfg.settle_max_position_sigma_m,
                max_distance=rtl_cfg.arrival_radius_m,
            )
        )
        self._last_report: Optional[SettlementReport] = None
        self._overshoot_logged = False
        #: Continuous time spent inside the arrival radius at low speed.
        self._commit_dwell: float = 0.0
        # Mission time accumulated from the supplied dt. The controller must not
        # read a wall clock: doing so is what makes a control law untestable and
        # couples it to how fast the loop happens to run, which is the same
        # defect that rules out the SDK's PIDController for this path.
        self._elapsed: float = 0.0

    # ------------------------------------------------------------- properties

    @property
    def climb_ceiling(self) -> float:
        """Corrective ascent this law will pass through, in normalized units."""
        return self._climb_ceiling

    @property
    def cruise_speed_mps(self) -> float:
        """Cruise speed cap in physical units."""
        return self._cruise_mps

    @property
    def settlement(self) -> Optional[SettlementReport]:
        """Most recent settlement evaluation, or ``None`` before the first cycle."""
        return self._last_report

    @property
    def elapsed_sec(self) -> float:
        """Mission time accumulated from the supplied ``dt`` values."""
        return self._elapsed

    # ------------------------------------------------------------------ cycle

    def reset(self) -> None:
        """Clear all controller state. Call once before engaging the return leg."""
        self._longitudinal.reset()
        self._lateral.reset()
        self._longitudinal_profile.reset()
        self._lateral_profile.reset()
        self._shaper_x.reset()
        self._shaper_y.reset()
        self._settlement.reset()
        self._last_report = None
        self._overshoot_logged = False
        self._commit_dwell = 0.0
        self._elapsed = 0.0

    def compute(
        self,
        snapshot: OdometrySnapshot,
        vz_command: float,
        dt: float,
        *,
        phase: RTLPhase = RTLPhase.NAVIGATING,
        reference: Optional[ReturnReference] = None,
    ) -> GuidanceCommand:
        """Produce one cycle of guidance.

        Parameters
        ----------
        snapshot : OdometrySnapshot
            Consistent odometric state, captured under a single lock.
        vz_command : float
            Vertical command from the altitude governor, in normalized units.
            Passed through rather than recomputed, and saturated here onto
            whatever ascent the configured governor authorizes -- nothing at all
            unless one was supplied -- so a misbehaving governor cannot turn the
            return leg into a climb.
        dt : float
            True elapsed interval from :meth:`LoopRate.tick`.
        phase : RTLPhase
            ``NAVIGATING`` for the return leg, ``STATION_KEEPING`` for the
            damping hold over the origin.
        reference : Optional[ReturnReference]
            Where the origin is. Omitted, it is derived from ``snapshot``, which
            is what every call site did implicitly before this parameter
            existed; supplied, it lets the mission navigate on the aggregated
            motion sequence instead.
        """
        self._elapsed += dt
        if reference is None:
            reference = ReturnReference.from_snapshot(snapshot)
        ex, ey, distance = reference.ex_body_m, reference.ey_body_m, reference.distance_m

        speed_cap = self._cruise_mps
        if phase is RTLPhase.STATION_KEEPING:
            speed_cap *= STATION_KEEPING_SPEED_RATIO

        longitudinal_mps, note = self._compute_longitudinal(ex, dt, speed_cap)
        lateral_mps = self._compute_lateral(ey, dt, speed_cap)

        # Convert once, at the boundary, then render onto the quantized channel.
        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)

        # Position from the reference, speed from odometry. They are not
        # interchangeable and the split is deliberate: the dispersion term is
        # asking how steady the *estimate* is, so it has to be fed the estimate
        # actually being navigated on, while speed is a measurement of the
        # airframe that dead reckoning cannot supply -- it only knows what was
        # asked for, and "did the drone stop" is precisely the question a
        # commanded velocity cannot answer.
        report = self._settlement.update(
            x=reference.x_m,
            y=reference.y_m,
            speed=snapshot.horizontal_speed,
            distance=distance,
            timestamp=self._elapsed,
        )
        self._last_report = report

        # The landing commit runs alongside settlement rather than inside it.
        # Settlement answers "has the vehicle converged?", which is the right
        # question for a guidance law and the wrong one for deciding to land:
        # its positional-sigma term depends on odometry quality the platform
        # does not guarantee at hover, so a drone sitting over the origin could
        # fail it indefinitely. This asks only "is it over the origin and slow?"
        if distance <= self.rtl_cfg.arrival_radius_m and (
            snapshot.horizontal_speed <= self.rtl_cfg.settle_max_speed_mps
        ):
            self._commit_dwell += dt
        else:
            self._commit_dwell = 0.0
        ready_to_land = self._commit_dwell >= self.rtl_cfg.landing_commit_dwell_sec

        return GuidanceCommand(
            # Backward-only, except for the bounded terminal overshoot trim,
            # which is capped in _compute_longitudinal and can never exceed
            # ``overshoot_recovery_speed``.
            vx=max(-1.0, min(self.rtl_cfg.overshoot_recovery_speed, vx)),
            vy=vy,
            # Saturated onto the authorized band. Descent is unbounded here and
            # bounded at the actuator by the governor's own cap; ascent is
            # bounded to exactly what altitude hold was configured to allow,
            # which is zero for any caller that did not ask for it.
            vz=min(self._climb_ceiling, vz_command),
            vyaw=0.0,
            distance_m=distance,
            ex_body_m=ex,
            ey_body_m=ey,
            target_speed_mps=longitudinal_mps,
            phase=phase,
            arrived=report.settled,
            ready_to_land=ready_to_land,
            note=note if note else report.reason,
            reference_source=reference.source,
        )

    # ---------------------------------------------------------------- channels

    def _compute_longitudinal(self, ex: float, dt: float, speed_cap: float) -> tuple[float, str]:
        """Along-track law: PD demand, capped by what can still be stopped."""
        if abs(ex) < self.rtl_cfg.deadband_m:
            return self._longitudinal_profile.step(0.0, dt), ""

        # The controlled variable is the drone's displacement from the origin
        # along body-x, which is the negation of the error vector pointing at it.
        demand = self._longitudinal.update(-ex, dt)

        # Feedforward ceiling: the fastest speed from which the remaining
        # distance is still enough to come to rest.
        ceiling = braking_velocity(
            abs(ex),
            self.rtl_cfg.max_accel_mps2,
            cruise_velocity=speed_cap,
            arrival_tolerance=self.rtl_cfg.deadband_m,
        )
        target = max(-ceiling, min(ceiling, demand))

        note = ""
        if target > 0.0:
            # The origin is ahead of the nose: the drone overshot it. A braking
            # profile plus odometry lag makes that close to inevitable in the
            # terminal regime, so it has to be recoverable.
            #
            # It used to be unrecoverable. The axis was held at exactly zero and
            # the code relied on "the arrival window" to absorb the residual --
            # but the arrival window is a test on distance, and with the axis
            # held the distance never changes. An overshoot larger than the
            # arrival radius was therefore a fixed point: the drone hovered a
            # few tens of centimetres past the origin, never confirmed arrival,
            # and only reached the ground when the whole return window expired.
            # In flight this read as returning to the takeoff point and never
            # landing.
            #
            # The backward-only rule protects a straight reverse line with no
            # yaw, which is what keeps the optical-flow estimate trustworthy. A
            # bounded forward trim over the last metre does not threaten that.
            # Hovering off-target until a timeout does.
            recovery_cap = self.calibration.to_mps(self.rtl_cfg.overshoot_recovery_speed)
            if ex <= self.rtl_cfg.overshoot_recovery_radius_m:
                target = min(target, recovery_cap)
                note = f"overshoot recovery ({ex:+.2f} m ahead)"
            else:
                # Further ahead than an overshoot explains. Something is wrong
                # with the origin or the odometry, and creeping forward on a bad
                # estimate is not the answer.
                target = 0.0
                note = f"overshoot {ex:.2f} m ahead, beyond recovery range: axis held"
            if not self._overshoot_logged:
                logger.warning(
                    "Launch origin is %.2f m ahead of the nose. %s",
                    ex,
                    (
                        f"Applying bounded forward recovery at up to {recovery_cap:.3f} m/s."
                        if ex <= self.rtl_cfg.overshoot_recovery_radius_m
                        else "Beyond the recovery range; holding the along-track axis."
                    ),
                )
                self._overshoot_logged = True

        return self._longitudinal_profile.step(target, dt), note

    def _compute_lateral(self, ey: float, dt: float, speed_cap: float) -> float:
        """Cross-track law: filtered PD, jerk-limited."""
        if abs(ey) < self.rtl_cfg.deadband_m:
            return self._lateral_profile.step(0.0, dt)

        lateral_cap = min(self._max_lateral_mps, speed_cap)
        demand = self._lateral.update(-ey, dt)
        target = max(-lateral_cap, min(lateral_cap, demand))
        return self._lateral_profile.step(target, dt)
