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

**Two laws live in this module.** :class:`RTLGuidanceController`, described
above, closes on an *estimate* of where the launch origin is; it is what flies
when no marker detector can be built, and it is the best this airframe can do
from proprioception alone. :class:`ArucoCenteringController`, at the bottom of
the file, closes on a *measurement* of where the landing pad actually is, taken
through the camera. That distinction is the whole argument for the second law:
every estimator the first one can be handed integrates something -- commands, or
apparent motion -- so its error grows without bound over a mission and nothing on
the airframe observes that it has. The marker fix integrates nothing. Its error
is a property of the camera calibration rather than of the flight so far, which
is what makes a centimetre-class landing a reachable objective instead of a
hopeful one.

Both are free of ROS, of hardware, and of wall-clock reads: they take a state
and a ``dt`` and return a command, so the flight mathematics is exercisable in a
unit test at whatever rate the test chooses.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Final, Optional, Sequence

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

        longitudinal_mps, note = self._compute_longitudinal(ey, dt, speed_cap)
        lateral_mps = self._compute_lateral(ex, dt, speed_cap)

        # Convert once, at the boundary, then render onto the quantized channel.
        vx = -ey * 0.0469
        vy =  ex * 0.0469

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


# ════════════════════════════════════════════════════ ArUco terminal guidance
#
# Everything below closes the return leg on a *landmark* rather than on a
# coordinate. The law above navigates to where the mission believes the origin
# is; this one navigates to where the camera can see that it actually is.
#
# The two are complementary rather than redundant. Dead reckoning and optical
# flow are both open-loop with respect to the ground -- one integrates the
# commands that were sent, the other integrates apparent motion -- so their error
# grows with the length of the mission and nothing on the airframe bounds it. A
# marker observation does not integrate anything: it is a metric, absolute,
# drift-free fix on the pad, and its error is a property of the camera
# calibration rather than of how long the drone has been flying.


#: Smallest radial error, in metres, that the settle test will accept as a
#: tolerance. A tolerance at or below zero is unsatisfiable and would hold a
#: converged aircraft airborne until its window expired.
_MIN_CENTERING_TOLERANCE_M: Final[float] = 1e-3


@dataclass(frozen=True)
class MarkerObservation:
    """One accepted sighting of the landing marker, in the camera frame.

    Units are metres throughout, and the frame is OpenCV's camera frame rather
    than ROS's: **+X right** across the image, **+Y down** the image, **+Z
    forward** along the optical axis. That is the frame
    ``cv2.aruco.estimatePoseSingleMarkers`` returns translation in -- and the
    frame its ``cv2.solvePnP`` replacement returns too -- so the projection in
    :func:`project_marker_to_body` is written against it directly rather than
    against a re-labelled copy that would only ever be one sign error away from
    flying the aircraft in the wrong direction.

    This type exists so that the boundary
    between "what the SDK returned" and "what the control law consumes" is a
    single, validated, immutable object: the SDK's ``pose_estimate`` returns a
    three-tuple whose members are independently nullable and whose ID is not
    checked against anything, and a control law should not be the place where
    that is discovered.
    """

    #: Marker identity as reported by the detector, already checked against the
    #: configured target by :meth:`from_pose_estimate`.
    marker_id: int
    #: Lateral offset along the camera's +X axis: positive when the marker lies
    #: to the *right* of the optical axis.
    x_cam_m: float
    #: Offset along the camera's +Y axis: positive when the marker lies *below*
    #: the optical axis in the image.
    y_cam_m: float
    #: Range along the optical axis (+Z). Always positive for a marker the
    #: camera can see.
    z_cam_m: float
    #: Planar marker yaw in degrees, as computed by the SDK from the corner
    #: vertices. Carried for telemetry only: the mission pins ``vyaw`` at zero,
    #: so there is no channel that could act on it.
    yaw_deg: Optional[float] = None

    @property
    def slant_range_m(self) -> float:
        """Straight-line distance from the camera to the marker, metres."""
        return math.sqrt(self.x_cam_m**2 + self.y_cam_m**2 + self.z_cam_m**2)

    @classmethod
    def from_pose_estimate(
        cls,
        marker_id: Optional[object],
        translation: Optional[Sequence[float]],
        yaw_deg: Optional[float],
        *,
        expected_id: int,
    ) -> Optional["MarkerObservation"]:
        """Validate one ``Aruco.pose_estimate`` return, or reject it.

        The SDK's contract is ``(id, tvec, yaw)`` where every member is ``None``
        when nothing was detected, ``id`` is whichever marker happened to be
        first in the detector's output, and ``tvec`` is a NumPy array rather than
        a Python sequence. Each of those is a way for a bad frame to reach a
        control loop, so each is checked here:

        * **No detection.** ``id`` or ``tvec`` is ``None``; nothing to fly on.
        * **Wrong marker.** ``id`` is a marker from the configured dictionary
          that is not the landing pad. Rejected outright rather than downweighted
          -- the consequence of accepting it is a landing at the wrong place, and
          Stage 5 is the last stage, so nothing downstream would catch it.
        * **Degenerate pose.** A non-finite component, or a non-positive range
          along the optical axis. ``solvePnP`` can return a mirrored solution for
          a marker seen near-edge-on, and a negative ``z`` is that solution
          announcing itself: the marker is not behind the camera, the pose is
          wrong. Flying a PD law on it inverts both error channels.

        Returns
        -------
        Optional[MarkerObservation]
            The validated sighting, or ``None`` when the frame carries no usable
            observation of the configured marker.
        """
        if marker_id is None or translation is None:
            return None

        try:
            identity = int(marker_id)
        except (TypeError, ValueError):
            return None
        if identity != int(expected_id):
            return None

        try:
            x_cam, y_cam, z_cam = (float(translation[0]), float(translation[1]), float(translation[2]))
        except (TypeError, ValueError, IndexError):
            return None

        if not all(math.isfinite(value) for value in (x_cam, y_cam, z_cam)):
            return None
        if z_cam <= 0.0:
            return None

        planar_yaw: Optional[float] = None
        if yaw_deg is not None:
            try:
                candidate = float(yaw_deg)
            except (TypeError, ValueError):
                candidate = float("nan")
            if math.isfinite(candidate):
                planar_yaw = candidate

        return cls(
            marker_id=identity,
            x_cam_m=x_cam,
            y_cam_m=y_cam,
            z_cam_m=z_cam,
            yaw_deg=planar_yaw,
        )


@dataclass(frozen=True)
class CenteringCommand:
    """One cycle of the ArUco centering law: what to fly, and why."""

    #: Along-track command, normalized. Positive is forward.
    vx: float
    #: Cross-track command, normalized. Positive is left, in body FLU.
    vy: float
    #: Body-frame along-track error, metres: positive when the marker lies ahead
    #: of the airframe.
    ex_body_m: float
    #: Body-frame cross-track error, metres: positive when the marker lies to the
    #: left of the airframe.
    ey_body_m: float
    #: Planar range from the airframe to the marker, metres.
    radial_error_m: float
    #: Straight-line camera-to-marker range this cycle, metres. Zero while the
    #: marker is not being observed.
    slant_range_m: float
    #: Commanded translational speed this cycle, m/s, before normalization.
    commanded_speed_mps: float
    #: True when a validated observation of the target marker drove this cycle.
    tracking: bool
    #: Consecutive cycles the marker has been missing.
    lost_frames: int
    #: True when the radial error is inside the configured tolerance *this*
    #: cycle. Instantaneous, and on its own not sufficient to land: a drone
    #: crossing the centre at speed satisfies it.
    within_tolerance: bool
    #: True when the airframe has been inside the tolerance at residual speed for
    #: ``centering_settle_cycles`` consecutive cycles. This is the landing gate.
    settled: bool
    #: Marker identity driving this cycle, or ``None`` while it is not visible.
    marker_id: Optional[int]
    note: str


def project_marker_to_body(
    x_cam_m: float, y_cam_m: float, z_cam_m: float, camera_tilt_deg: float
) -> tuple[float, float]:
    """Resolve a camera-frame marker offset into body-FLU horizontal errors.

    The gimbal pitches the camera down by ``|camera_tilt_deg|`` from the nose and
    does not roll or pan, so the camera frame is the body frame rotated about the
    body-y axis by that depression angle. Writing ``t`` for the depression, the
    camera axes expressed in body FLU are

    ==========  ====================================
    camera +X   ``(0, -1, 0)``    -- image right is body right, i.e. ``-y``
    camera +Y   ``(-sin t, 0, -cos t)`` -- image down
    camera +Z   ``(cos t, 0, -sin t)``  -- optical axis, forward and down
    ==========  ====================================

    so a marker at camera coordinates ``(x, y, z)`` sits at body offset
    ``x * X + y * Y + z * Z``, whose horizontal components are

    .. math::

        e_x = z \\cos t - y \\sin t \\qquad e_y = -x

    Two sanity checks on the signs, both of which are flight-critical:

    * **Marker to the right** of the optical axis means ``x > 0``, so ``e_y < 0``
      and the lateral channel commands ``vy < 0`` -- rightward in body FLU.
    * **Marker below** the optical centre means ``y > 0``, which drives ``e_x``
      negative and commands ``vx < 0`` -- reverse. That is correct for a
      downward-and-forward-looking camera: a target lower in the image is behind
      the point the optical axis meets the ground, therefore behind the drone.

    At ``t = 90`` degrees (true nadir) this degenerates to ``e_x = -y``,
    ``e_y = -x``, which is the familiar pure-nadir mapping and is what makes the
    tilt a configuration choice rather than a structural assumption.

    Parameters
    ----------
    x_cam_m, y_cam_m, z_cam_m : float
        Marker translation in the OpenCV camera frame, metres.
    camera_tilt_deg : float
        Commanded gimbal tilt in degrees, negative below the horizon. Only the
        magnitude is used, so a configuration that states the depression as a
        positive angle behaves identically.

    Returns
    -------
    Tuple[float, float]
        ``(ex_body_m, ey_body_m)`` -- forward and left error, metres.
    """
    depression = math.radians(abs(camera_tilt_deg))
    ex_body = z_cam_m * math.cos(depression) - y_cam_m * math.sin(depression)
    ey_body = -x_cam_m
    return ex_body, ey_body


class ArucoCenteringController:
    """Closed-loop centering of the airframe over the landing marker.

    The law is a per-axis filtered PD on the body-frame horizontal error,
    saturated onto a centering speed ceiling, jerk-limited, converted to the
    actuator's normalized domain once at the output, and rendered through the
    sigma-delta shaper so that sub-quantum demands survive the driver's
    ``int8(v * 100)`` truncation without a hard velocity floor.

    Three properties are worth stating explicitly because each of them is a
    failure this class exists to prevent.

    *It never rotates.* ``vyaw`` is not an output of this class at all. Yaw
    would be the obvious way to null the marker's planar orientation, and it is
    exactly what must not happen: the Bebop's odometry is optical flow, the
    mission's altitude and dead-reckoning estimates ride on it, and a rotation
    during the terminal phase corrupts all of them at the moment they matter
    most. The marker's yaw is carried in telemetry and acted on by nothing.

    *Its speed ceiling bounds the resultant, not the axes.* ``max_centering_speed``
    is a statement about how fast the aircraft may move over the pad, and two
    axes each clipped at that value move sqrt(2) times faster than it on a
    diagonal. Scaling the pair also keeps the commanded direction pointing at
    the marker; clipping one railed axis turns a straight approach into a
    dog-leg.

    *It stops before it claims to have arrived.* Being inside the tolerance for
    one cycle is satisfied by a drone flying through the centre at cruise speed.
    The settle counter advances only while the airframe is both inside the
    tolerance and commanding residual speed, and it is cleared outright the
    moment either condition fails.

    *It does not fly on stale observations.* A missing frame within
    ``lost_frames_tolerance`` holds station and keeps the PD state, because
    detection over a moving airframe drops frames routinely and restarting the
    loop on each one would make the phase a sequence of restarts. Beyond the
    tolerance the settle counter is cleared: a settlement claim assembled from
    observations the camera is no longer making is precisely the claim that puts
    an aircraft down somewhere it was not looking.

    All internal computation is in metres and metres per second; the boundary
    with the actuator's normalized units is
    :class:`~mvp_mission_bebop.estimation.calibration.SpeedCalibration` and it is
    crossed exactly once, at the output.
    """

    def __init__(
        self,
        rtl_cfg: ReturnToLaunchConfig,
        kinematics_cfg: FlightKinematicsConfig,
        calibration: Optional[SpeedCalibration] = None,
    ) -> None:
        self.rtl_cfg = rtl_cfg
        self.kinematics_cfg = kinematics_cfg
        self.calibration = calibration or SpeedCalibration(kinematics_cfg.normalized_to_mps)

        #: Gimbal depression the projection is built against. Held as state
        #: rather than read per cycle so that a mid-phase configuration edit
        #: cannot change the frame the PD state was accumulated in.
        self._tilt_deg: float = rtl_cfg.camera_tilt_deg

        # The ceiling arrives normalized because that is the domain the GCS
        # parameter sheet speaks; the law works in m/s.
        self._max_speed_mps: float = abs(
            self.calibration.to_mps(rtl_cfg.max_centering_speed)
        )
        if self._max_speed_mps <= 0.0:
            raise ValueError(
                f"rtl.max_centering_speed must be non-zero, got "
                f"{rtl_cfg.max_centering_speed!r}; a centering law with no speed "
                f"authority cannot converge and would hold the aircraft over the "
                f"marker until its window expired"
            )

        self._tolerance_m: float = max(
            _MIN_CENTERING_TOLERANCE_M, abs(rtl_cfg.centering_tolerance_m)
        )
        # The deadband suppresses pixel-scale jitter; it must stay strictly
        # inside the convergence gate or the law would stop correcting while
        # still outside the tolerance it is gated on, and the phase would run to
        # its timeout with the aircraft parked just off centre.
        self._deadband_m: float = min(abs(rtl_cfg.deadband_m), 0.5 * self._tolerance_m)
        self._settle_target: int = max(1, int(rtl_cfg.centering_settle_cycles))
        self._lost_tolerance: int = max(0, int(rtl_cfg.lost_frames_tolerance))
        # Residual-speed gate for the settle test, reusing the same threshold the
        # odometric settlement detector applies. One notion of "stopped" for the
        # whole return leg.
        self._settle_speed_mps: float = abs(rtl_cfg.settle_max_speed_mps)

        self._longitudinal = FilteredPID(
            PIDGains(
                kp=rtl_cfg.centering_kp_x,
                ki=0.0,
                kd=rtl_cfg.centering_kd_x,
                output_limits=(-self._max_speed_mps, self._max_speed_mps),
                error_deadband=self._deadband_m,
            )
        )
        self._lateral = FilteredPID(
            PIDGains(
                kp=rtl_cfg.centering_kp_y,
                ki=0.0,
                kd=rtl_cfg.centering_kd_y,
                output_limits=(-self._max_speed_mps, self._max_speed_mps),
                error_deadband=self._deadband_m,
            )
        )

        self._longitudinal_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._max_speed_mps,
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )
        self._lateral_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=self._max_speed_mps,
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

        self._settle_cycles: int = 0
        self._lost_frames: int = 0
        self._elapsed: float = 0.0

    # ------------------------------------------------------------- properties

    @property
    def max_speed_mps(self) -> float:
        """Centering speed ceiling in physical units."""
        return self._max_speed_mps

    @property
    def tolerance_m(self) -> float:
        """Radial error inside which the airframe counts as centred."""
        return self._tolerance_m

    @property
    def deadband_m(self) -> float:
        """Per-axis error below which the law commands nothing."""
        return self._deadband_m

    @property
    def settle_cycles(self) -> int:
        """Consecutive qualifying cycles accumulated toward the landing gate."""
        return self._settle_cycles

    @property
    def lost_frames(self) -> int:
        """Consecutive cycles without a validated observation."""
        return self._lost_frames

    @property
    def elapsed_sec(self) -> float:
        """Phase time accumulated from the supplied ``dt`` values."""
        return self._elapsed

    # ------------------------------------------------------------------ cycle

    def reset(self) -> None:
        """Clear all controller state. Call once before engaging centering."""
        self._longitudinal.reset()
        self._lateral.reset()
        self._longitudinal_profile.reset()
        self._lateral_profile.reset()
        self._shaper_x.reset()
        self._shaper_y.reset()
        self._settle_cycles = 0
        self._lost_frames = 0
        self._elapsed = 0.0

    def update(self, observation: Optional[MarkerObservation], dt: float) -> CenteringCommand:
        """Produce one cycle of centering guidance.

        Parameters
        ----------
        observation : Optional[MarkerObservation]
            The validated sighting for this cycle, or ``None`` when the frame
            carried no usable observation of the configured marker -- no frame at
            all, no detection, or a detection the validator rejected. All three
            are the same thing to this law and are handled identically.
        dt : float
            True elapsed interval, in seconds, from
            :meth:`~mvp_mission_bebop.engine.rate.LoopRate.tick`. A non-positive
            interval is absorbed without advancing any state.

        Returns
        -------
        CenteringCommand
            Normalized ``vx``/``vy`` plus the full reasoning behind them.
            ``vz`` and ``vyaw`` are deliberately absent: the vertical axis
            belongs to the altitude governor for the whole of this phase, and
            rotation is prohibited outright.
        """
        self._elapsed += max(0.0, dt)

        if observation is None:
            return self._coast(dt)

        self._lost_frames = 0

        ex_body, ey_body = project_marker_to_body(
            observation.x_cam_m, observation.y_cam_m, observation.z_cam_m, self._tilt_deg
        )
        radial = math.hypot(ex_body, ey_body)
        within = radial <= self._tolerance_m

        if within:
            # Inside the gate the objective changes from "reduce the error" to
            # "come to rest", and those are different commands. Driving the
            # profile to zero rather than letting the PD trickle along is what
            # makes the settle test reachable: with the deadband suppressing the
            # residual error the PD output is already nil, but the *profile*
            # still carries whatever velocity the approach left in it, and the
            # airframe with it.
            longitudinal_mps = self._longitudinal_profile.step(0.0, dt)
            lateral_mps = self._lateral_profile.step(0.0, dt)
            note = f"centred: radial {radial:.3f} m <= {self._tolerance_m:.3f} m"
        else:
            # Measurement is the airframe's displacement *from* the marker, which
            # is the negation of the error vector pointing at it, against a zero
            # setpoint. The sign convention is the same one the odometric law
            # above uses, deliberately: one reading of "error" across the module.
            longitudinal_demand = self._longitudinal.update(-ex_body, dt)
            lateral_demand = self._lateral.update(-ey_body, dt)
            # Saturated as a vector, not per axis. Clipping the two channels
            # independently lets the resultant reach sqrt(2) times the ceiling on
            # a diagonal approach -- 0.113 against a configured 0.08 -- and it
            # also rotates the commanded direction away from the marker whenever
            # exactly one axis is railed, so the aircraft crabs in on a dog-leg
            # instead of a straight line. Scaling preserves the heading.
            longitudinal_target, lateral_target = self._saturate_pair(
                longitudinal_demand, lateral_demand
            )
            longitudinal_mps = self._longitudinal_profile.step(longitudinal_target, dt)
            lateral_mps = self._lateral_profile.step(lateral_target, dt)
            note = f"centering: radial {radial:.3f} m, target <= {self._tolerance_m:.3f} m"

        # The two profiles are independent second-order systems, so even with
        # in-envelope targets their outputs can transiently combine above the
        # ceiling while they track at different rates. Bound the resultant and
        # then reconcile each profile's state with what was actually emitted --
        # the same reconciliation JerkLimitedProfile performs internally when its
        # own velocity clamp truncates a step, and for the same reason: a
        # profile whose stored velocity is not the one the actuator received
        # measures its next jerk against a command that never existed.
        longitudinal_mps, lateral_mps = self._reconcile(longitudinal_mps, lateral_mps)

        speed_mps = math.hypot(longitudinal_mps, lateral_mps)
        # Both conditions, every cycle. Inside the tolerance but still moving is
        # a drone crossing the centre, not a drone over it.
        if within and speed_mps <= self._settle_speed_mps:
            self._settle_cycles += 1
        else:
            self._settle_cycles = 0
        settled = self._settle_cycles >= self._settle_target

        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)

        return CenteringCommand(
            vx=vx,
            vy=vy,
            ex_body_m=ex_body,
            ey_body_m=ey_body,
            radial_error_m=radial,
            slant_range_m=observation.slant_range_m,
            commanded_speed_mps=speed_mps,
            tracking=True,
            lost_frames=0,
            within_tolerance=within,
            settled=settled,
            marker_id=observation.marker_id,
            note=note,
        )

    # ---------------------------------------------------------------- helpers

    def _coast(self, dt: float) -> CenteringCommand:
        """Hold station through a cycle that carried no usable observation.

        The airframe is brought to rest rather than left on its last command,
        because the Bebop latches the last Twist it received indefinitely: doing
        nothing here is not "hold position", it is "keep flying the correction
        computed for a marker position nobody can currently see".
        """
        self._lost_frames += 1
        longitudinal_mps = self._longitudinal_profile.step(0.0, dt)
        lateral_mps = self._lateral_profile.step(0.0, dt)

        tolerated = self._lost_frames <= self._lost_tolerance
        if not tolerated:
            # Past the tolerance the PD state describes a world the camera is no
            # longer confirming. Clearing it stops a stale derivative from firing
            # into the first frame that comes back.
            self._settle_cycles = 0
            self._longitudinal.reset()
            self._lateral.reset()
            note = f"marker lost for {self._lost_frames} frames: holding station"
        else:
            note = f"marker absent ({self._lost_frames}/{self._lost_tolerance}): coasting"

        vx = self._shaper_x.shape(self.calibration.to_normalized(longitudinal_mps), dt)
        vy = self._shaper_y.shape(self.calibration.to_normalized(lateral_mps), dt)

        return CenteringCommand(
            vx=vx,
            vy=vy,
            ex_body_m=0.0,
            ey_body_m=0.0,
            radial_error_m=float("inf"),
            slant_range_m=0.0,
            commanded_speed_mps=math.hypot(longitudinal_mps, lateral_mps),
            tracking=False,
            lost_frames=self._lost_frames,
            within_tolerance=False,
            # A landing is never authorized on a cycle the marker was not seen.
            # The counter is preserved while inside the tolerated window so a
            # single dropped frame does not discard a near-complete settlement,
            # but the gate itself requires a live observation.
            settled=False,
            marker_id=None,
            note=note,
        )

    def _saturate_pair(self, longitudinal_mps: float, lateral_mps: float) -> tuple[float, float]:
        """Scale a velocity demand onto the centering speed ceiling.

        The ceiling bounds the *resultant* translational speed, so the pair is
        scaled rather than clipped: a uniform scale leaves the commanded
        direction pointing at the marker, while an independent clip on each axis
        rotates it toward the diagonal.
        """
        magnitude = math.hypot(longitudinal_mps, lateral_mps)
        if magnitude <= self._max_speed_mps or magnitude <= 0.0:
            return longitudinal_mps, lateral_mps
        scale = self._max_speed_mps / magnitude
        return longitudinal_mps * scale, lateral_mps * scale

    def _reconcile(self, longitudinal_mps: float, lateral_mps: float) -> tuple[float, float]:
        """Bound the emitted resultant and re-seed the profiles onto it."""
        bounded_x, bounded_y = self._saturate_pair(longitudinal_mps, lateral_mps)
        if bounded_x != longitudinal_mps or bounded_y != lateral_mps:
            self._longitudinal_profile.reset(
                bounded_x, self._longitudinal_profile.acceleration
            )
            self._lateral_profile.reset(bounded_y, self._lateral_profile.acceleration)
        return bounded_x, bounded_y
