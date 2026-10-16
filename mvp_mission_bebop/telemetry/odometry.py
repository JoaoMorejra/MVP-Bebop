"""Odometry ingestion, ground calibration, and body-frame projections.

The Bebop exposes no telemetry through the Nectar SDK: ``BebopDrone`` owns nine
publishers and not a single subscriber, and ``is_armed`` / ``flight_mode`` /
``is_fcu_connected`` all fall through to the base class's ``None``. The only
state feedback the airframe offers is ``/bebop/odom``, published at roughly
15 Hz by the C++ driver (``bebop_driver_node.cpp:148-151``). Everything this
package knows about where the drone is comes through this module.

Two properties matter for the rest of the system:

*Consistency.* :meth:`OdometrySupervisor.snapshot` captures the entire state
under a single lock acquisition and returns an immutable value. Control loops
previously took the lock five times per iteration through separate accessors and
could interleave a callback between them, mixing a position from one sample with
a velocity from the next.

*Single ownership.* Nothing outside this class writes its fields. Benchtop
simulation feeds it through :meth:`inject_synthetic_sample` rather than reaching
into the private lock, which is what the old RTL step did.
"""

from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, List, Optional, Sequence, Tuple

from mvp_mission_bebop.estimation.altitude_plausibility import (
    AltitudePlausibilityFilter,
    PlausibilityLimits,
)
from mvp_mission_bebop.estimation.platform_monitor import (
    UNKNOWN_REPORT,
    PlatformMonitor,
    PlatformReport,
    PlatformState,
    outbound_track_m,
)
from mvp_mission_bebop.estimation.vertical_estimator import (
    VerticalStateEstimator,
    normalize_altitude_estimator,
)
from mvp_mission_bebop.parameters import (
    CalibrationConfig,
    FlightKinematicsConfig,
    PlatformConfig,
    TimeoutsConfig,
)

if TYPE_CHECKING:  # pragma: no cover - import kept out of the runtime path
    from nav_msgs.msg import Odometry
    from std_msgs.msg import Float32

logger = logging.getLogger("OdometrySupervisor")

#: Scale factor converting a median absolute deviation into a standard
#: deviation estimate for normally distributed data.
MAD_TO_SIGMA: Final[float] = 1.4826

#: Period of the ``[ALT_PROBE ...]`` log line, in seconds.
PROBE_PERIOD_SEC: Final[float] = 1.0


class TelemetryHealth(Enum):
    """Health of the odometry stream.

    ``NEVER_RECEIVED`` is distinct from ``STALE`` on purpose. The previous
    implementation reported "no odometry has ever arrived" as healthy, so a
    ``/bebop/odom`` topic that never started passed every health gate and the
    mission flew a closed-loop manoeuvre against a position that was structurally
    zero.
    """

    NEVER_RECEIVED = "never_received"
    STALE = "stale"
    HEALTHY = "healthy"

    @property
    def is_healthy(self) -> bool:
        """True only for a stream that is both present and fresh."""
        return self is TelemetryHealth.HEALTHY


@dataclass(frozen=True)
class OdometrySnapshot:
    """Immutable, internally consistent view of the drone's odometric state.

    All fields come from one lock acquisition, so derived quantities cannot mix
    samples. Distances are metres, velocities metres per second, ``yaw`` radians.
    """

    x: float
    y: float
    raw_altitude: float
    relative_altitude: float
    vx: float
    vy: float
    vz: float
    yaw: float
    timestamp: float
    sample_count: int
    ground_reference: Optional[float]
    takeoff_x: Optional[float]
    takeoff_y: Optional[float]
    #: Height of the launch point above the floor, ``platform.height_m``.
    #: ``relative_altitude`` is measured from the launch point.
    floor_offset_m: float = 0.0
    #: Position along the outbound track from the hover origin frozen by
    #: :meth:`OdometrySupervisor.freeze_hover_takeoff_origin`, metres, positive
    #: to the left of it. ``None`` before that origin exists.
    along_track_m: Optional[float] = None
    #: :class:`~mvp_mission_bebop.estimation.platform_monitor.PlatformState`
    #: value of the most recent sample.
    platform_state: str = "unknown"
    #: True while the platform edge guard is active.
    guard_active: bool = False
    #: Last finite ``/bebop/states/altitude``: the firmware's altitude above the
    #: takeoff point (ARSDK ``AltitudeChanged``). ``None`` until one arrives.
    firmware_altitude_m: Optional[float] = None

    @property
    def height_above_floor_m(self) -> float:
        """Height above the floor the visual targets lie on, in metres.

        ``relative_altitude`` is referenced to the ground calibration taken
        where the airframe rested before takeoff; on a pad that is the pad
        top. The ground projection and the IBVS range need the height above
        the plane the target lies on instead. The vertical loop, the ceiling
        and the touchdown keep using ``relative_altitude``.
        """
        return self.relative_altitude + self.floor_offset_m

    @property
    def horizontal_speed(self) -> float:
        """Ground speed magnitude in the horizontal plane, m/s."""
        return math.hypot(self.vx, self.vy)

    @property
    def speed(self) -> float:
        """Full three-dimensional speed magnitude, m/s."""
        return math.sqrt(self.vx * self.vx + self.vy * self.vy + self.vz * self.vz)

    @property
    def has_launch_origin(self) -> bool:
        """True once a horizontal return target has been frozen."""
        return self.takeoff_x is not None and self.takeoff_y is not None

    @property
    def is_calibrated(self) -> bool:
        """True once a ground altitude reference has been established."""
        return self.ground_reference is not None

    def body_frame_launch_error(self) -> Tuple[float, float, float]:
        """Vector from the current position to the launch origin, in body FLU.

        Returns
        -------
        Tuple[float, float, float]
            ``(ex_body, ey_body, distance)`` where ``ex_body`` is positive when
            the origin lies ahead of the nose, ``ey_body`` positive when it lies
            to the left, and ``distance`` is the planar Euclidean range. All
            zeros when no launch origin has been frozen yet.
        """
        if self.takeoff_x is None or self.takeoff_y is None:
            return 0.0, 0.0, 0.0

        dx = self.takeoff_x - self.x
        dy = self.takeoff_y - self.y
        cos_psi = math.cos(self.yaw)
        sin_psi = math.sin(self.yaw)
        ex_body = cos_psi * dx + sin_psi * dy
        ey_body = -sin_psi * dx + cos_psi * dy
        return ex_body, ey_body, math.hypot(dx, dy)


def robust_center(samples: Sequence[float], outlier_sigma: float = 3.0) -> Tuple[float, float]:
    """Estimate a location parameter with median/MAD outlier rejection.

    A plain arithmetic mean lets one spurious altitude reading bias the ground
    reference that every later altitude measurement is differenced against. The
    median is unaffected by up to half the samples being wrong, and the MAD gives
    a dispersion estimate that is itself robust, so the inlier gate does not
    widen just because an outlier is present.

    Parameters
    ----------
    samples : Sequence[float]
        Raw observations. Must not be empty.
    outlier_sigma : float
        Inlier half-width, in robust sigmas from the median.

    Returns
    -------
    Tuple[float, float]
        ``(center, dispersion)`` -- the mean of the retained inliers and the
        robust sigma estimate. Dispersion is zero when the samples are identical.
    """
    if not samples:
        raise ValueError("robust_center requires at least one sample")
    if len(samples) == 1:
        return float(samples[0]), 0.0

    median = statistics.median(samples)
    mad = statistics.median([abs(value - median) for value in samples])
    sigma = MAD_TO_SIGMA * mad

    if sigma <= 0.0:
        # Degenerate spread (identical samples, or more than half identical):
        # the median is already the best available estimate.
        return float(median), 0.0

    threshold = outlier_sigma * sigma
    inliers = [value for value in samples if abs(value - median) <= threshold]
    if not inliers:
        return float(median), float(sigma)
    return float(statistics.fmean(inliers)), float(sigma)


class OdometrySupervisor:
    """Owns odometry state, ground calibration, and body-frame projections."""

    def __init__(
        self,
        kinematics_cfg: FlightKinematicsConfig,
        timeouts_cfg: TimeoutsConfig,
        calibration_cfg: Optional[CalibrationConfig] = None,
        platform_cfg: Optional[PlatformConfig] = None,
    ) -> None:
        self.kinematics_cfg = kinematics_cfg
        self.timeouts_cfg = timeouts_cfg
        self.calibration_cfg = calibration_cfg or CalibrationConfig()
        self.platform_cfg = platform_cfg or PlatformConfig()
        mode = normalize_altitude_estimator(self.calibration_cfg.altitude_estimator)
        #: ``None`` in ``"legacy"`` mode, which keeps the position gate unchanged.
        self._vertical: Optional[VerticalStateEstimator] = (
            VerticalStateEstimator(self.calibration_cfg) if mode == "vz_gated" else None
        )
        self._vertical_lock = threading.Lock()
        self._vz_timeline: Optional[float] = None
        self._last_header_stamp: Optional[float] = None
        self._last_arrival: Optional[float] = None
        self._datum_flagged = False

        self._lock = threading.Lock()
        self.ground_reference_altitude: Optional[float] = None
        self.current_raw_altitude: float = 0.0
        self._altitude_filter = AltitudePlausibilityFilter(
            PlausibilityLimits(
                max_speed_mps=self.calibration_cfg.plausibility_max_speed_mps,
                max_accel_mps2=self.calibration_cfg.plausibility_max_accel_mps2,
                reject_streak=self.calibration_cfg.plausibility_reject_streak,
            )
        )
        self._last_filter_timestamp: Optional[float] = None
        self.current_x: float = 0.0
        self.current_y: float = 0.0
        self.current_vx: float = 0.0
        self.current_vy: float = 0.0
        self.current_vz: float = 0.0
        self.current_yaw: float = 0.0
        self.takeoff_x: Optional[float] = None
        self.takeoff_y: Optional[float] = None
        self.last_odometry_timestamp: float = 0.0
        self.sample_count: int = 0
        self._firmware_altitude: Optional[float] = None
        #: Monotonic arrival instant of :attr:`OdometrySnapshot.firmware_altitude_m`.
        self.firmware_altitude_stamp: Optional[float] = None
        self._last_probe: Optional[float] = None
        #: Classifies the along-track position against the pad edge; advanced
        #: on every stored sample.
        self.platform_monitor = PlatformMonitor(self.platform_cfg)
        self._platform_lock = threading.Lock()
        self._platform_report: PlatformReport = UNKNOWN_REPORT
        self._last_platform_update: Optional[float] = None
        self._hover_origin_frozen = False

        self._buffer_z: List[float] = []
        self._buffer_x: List[float] = []
        self._buffer_y: List[float] = []
        self._ceiling_breach_streak: int = 0
        #: Monotonic instant the current run of samples above the ceiling began.
        self._ceiling_breach_started: Optional[float] = None
        #: Whether the re-entry grace was in force at the last breach check,
        #: so its start and its expiry are each logged once.
        self._reentry_grace_logged = False

    # --------------------------------------------------------------- ingestion

    def odometry_callback(self, msg: "Odometry") -> None:
        """Ingest a ``nav_msgs/Odometry`` message. Runs on an executor thread.

        This is the only ingestion path gated by the altitude plausibility
        filter (``estimation.altitude_plausibility``). ``inject_synthetic_sample``,
        used by the benchtop kinematic simulator, is deliberately exempt: the
        simulator is a controlled physical model that cannot produce the
        sonar-dropout artefact the filter exists to reject, and several tests
        drive it with intentional step changes that must keep working.
        """
        position = msg.pose.pose.position
        twist = msg.twist.twist.linear
        orientation = msg.pose.pose.orientation

        # Yaw from the quaternion's z-w components. Computed before taking the
        # lock so trigonometry does not extend the critical section.
        siny_cosp = 2.0 * (orientation.w * orientation.z + orientation.x * orientation.y)
        cosy_cosp = 1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        sample = (
            float(position.x),
            float(position.y),
            float(position.z),
            float(twist.x),
            float(twist.y),
            float(twist.z),
            yaw,
        )

        # Reject the message rather than store a non-finite component.
        #
        # A NaN is not a large error, it is the absence of a measurement, and
        # nothing downstream can represent that. It propagates silently and
        # inverts the meaning of every guard it passes: ``min``/``max`` resolve
        # it to whichever bound is the first operand, so a NaN altitude turns
        # the ceiling check into "not breached" and a NaN position turns a
        # guidance clamp into full authority. Dropping the sample instead leaves
        # the last good one in place and lets the heartbeat call it stale if the
        # condition persists, which is the failure the supervisor already knows
        # how to handle.
        if not all(math.isfinite(component) for component in sample):
            logger.warning("Discarding odometry sample with non-finite components: %r", sample)
            return

        z = sample[2]
        if self._vertical is not None:
            z = self._estimate_altitude(msg, z, sample[5])
        elif self.calibration_cfg.plausibility_enabled:
            now = time.monotonic()
            dt = 0.0 if self._last_filter_timestamp is None else now - self._last_filter_timestamp
            self._last_filter_timestamp = now
            if dt <= 0.0:
                # First sample, or two callbacks landing on one clock tick:
                # nothing to gate against yet. Seed the filter rather than
                # reject, since a fresh filter has no trusted value to fall
                # back on.
                self._altitude_filter.reset(z)
            else:
                z = self._altitude_filter.update(z, dt)

        self._store_sample(
            x=sample[0],
            y=sample[1],
            z=z,
            vx=sample[3],
            vy=sample[4],
            vz=sample[5],
            yaw=sample[6],
            raw_z=sample[2],
        )

    def _estimate_altitude(self, msg: "Odometry", pose_z: float, vz: float) -> float:
        """Altitude from the gated vertical speed (``altitude_estimator = "vz_gated"``).

        Integrated on ``header.stamp``, which is when the driver produced the
        sample; a zero or non-increasing stamp falls back to the arrival
        interval on the monotonic clock. The estimator is seeded with the first
        ``pose.z``, so the stored altitude, and the ground calibration taken
        from it, stay in the odometry frame.
        """
        arrival = time.monotonic()
        header = None
        try:
            stamp = msg.header.stamp
            header = float(stamp.sec) + float(stamp.nanosec) * 1e-9
        except (AttributeError, TypeError, ValueError):
            header = None
        if header is not None and not (math.isfinite(header) and header > 0.0):
            header = None

        with self._vertical_lock:
            estimator = self._vertical
            if estimator is None:
                return pose_z
            previous = self._last_header_stamp
            advancing = header is not None and (previous is None or header > previous)
            if self._vz_timeline is None:
                self._vz_timeline = 0.0
            elif advancing and previous is not None:
                self._vz_timeline += header - previous
            elif self._last_arrival is not None:
                self._vz_timeline += max(0.0, arrival - self._last_arrival)
            if advancing:
                self._last_header_stamp = header
            self._last_arrival = arrival

            if not estimator.seeded:
                estimator.reset(pose_z)
            return estimator.update_velocity(vz, self._vz_timeline)

    def altitude_state_callback(self, msg: "Float32") -> None:
        """Ingest ``/bebop/states/altitude``. Runs on an executor thread.

        The driver publishes NaN while the aircraft link is down; that is the
        absence of a measurement and is dropped, leaving the last value and its
        arrival instant in place.
        """
        try:
            value = float(msg.data)
        except (AttributeError, TypeError, ValueError):
            return
        if not math.isfinite(value):
            return
        now = time.monotonic()
        with self._lock:
            self._firmware_altitude = value
            self.firmware_altitude_stamp = now
            ground = self.ground_reference_altitude

        anchored = self._vertical is not None and self.calibration_cfg.altitude_anchor_enabled
        if not anchored or ground is None:
            return
        # The firmware reports altitude above the takeoff point; the estimate
        # lives in the odometry frame, offset by the calibrated ground reference.
        with self._vertical_lock:
            self._vertical.update_anchor(value + ground, now)
            disagreement = self._vertical.datum_disagreement
        if disagreement and not self._datum_flagged:
            logger.warning(
                "[ALT_PROBE datum_disagreement] states/altitude has disagreed with the vz-gated "
                "estimate by more than %.2f m for 3 s; the anchor is not correcting.",
                self.calibration_cfg.altitude_anchor_gate_m,
            )
        self._datum_flagged = disagreement

    def inject_synthetic_sample(
        self,
        *,
        x: float,
        y: float,
        z: float,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        yaw: float = 0.0,
    ) -> None:
        """Feed a simulated sample, as the benchtop kinematic simulator does.

        Exists so ``--no-fly`` execution never has to reach through the private
        lock to mutate state, which would make this class multi-writer and let a
        live ``/bebop/odom`` publisher fight the simulator over the same fields.
        """
        self._store_sample(x=x, y=y, z=z, vx=vx, vy=vy, vz=vz, yaw=yaw)

    def _store_sample(
        self,
        *,
        x: float,
        y: float,
        z: float,
        vx: float,
        vy: float,
        vz: float,
        yaw: float,
        raw_z: Optional[float] = None,
    ) -> None:
        """Commit one sample under the lock and buffer it if pre-calibration.

        The platform monitor is advanced here rather than in the ROS callback,
        so the benchtop simulator, which feeds :meth:`inject_synthetic_sample`,
        exercises it exactly as the live odometry does. Its update runs between
        two acquisitions of the state lock, so a transition it logs never
        extends the critical section that snapshots wait on.
        """
        now = time.monotonic()
        with self._lock:
            ground = self.ground_reference_altitude
            origin = (self.takeoff_x, self.takeoff_y) if self._hover_origin_frozen else None
            previous = self._last_platform_update

        rel_alt = z if ground is None else z - ground
        along_track: Optional[float] = None
        if origin is not None and origin[0] is not None and origin[1] is not None:
            try:
                along_track = outbound_track_m(origin[0] - x, origin[1] - y, yaw)
            except ValueError:
                along_track = None
        with self._platform_lock:
            report = self.platform_monitor.update(
                along_track, rel_alt, 0.0 if previous is None else now - previous
            )

        probe = None
        with self._lock:
            self._last_platform_update = now
            self._platform_report = report
            self.current_x = x
            self.current_y = y
            self.current_raw_altitude = z
            self.current_vx = vx
            self.current_vy = vy
            self.current_vz = vz
            self.current_yaw = yaw
            self.last_odometry_timestamp = now
            self.sample_count += 1
            rel_alt = z if self.ground_reference_altitude is None else z - self.ground_reference_altitude
            if rel_alt > self.altitude_ceiling:
                if self._ceiling_breach_streak == 0:
                    self._ceiling_breach_started = now
                self._ceiling_breach_streak += 1
            else:
                self._ceiling_breach_streak = 0
                self._ceiling_breach_started = None

            if self.ground_reference_altitude is None:
                depth = self.calibration_cfg.sample_buffer_size
                self._buffer_z.append(z)
                self._buffer_x.append(x)
                self._buffer_y.append(y)
                if len(self._buffer_z) > depth:
                    del self._buffer_z[: len(self._buffer_z) - depth]
                    del self._buffer_x[: len(self._buffer_x) - depth]
                    del self._buffer_y[: len(self._buffer_y) - depth]

            if self.calibration_cfg.altitude_probe_enabled and (
                self._last_probe is None or now - self._last_probe >= PROBE_PERIOD_SEC - 1e-6
            ):
                self._last_probe = now
                raw = z if raw_z is None else raw_z
                ground = self.ground_reference_altitude
                raw_rel = raw if ground is None else raw - ground
                probe = (now, raw_rel, rel_alt, self._firmware_altitude, vz, report)

        if probe is not None:
            self._log_probe(*probe)

    def _log_probe(
        self,
        now: float,
        z_rel: float,
        z_est: float,
        firmware_altitude: Optional[float],
        vz: float,
        report: PlatformReport,
    ) -> None:
        """Emit one ``[ALT_PROBE ...]`` line. Logged outside the lock."""
        fw_alt = "na" if firmware_altitude is None else f"{firmware_altitude:.3f}"
        along = report.along_track_m
        logger.info(
            "[ALT_PROBE t=%.2f s=%s state=%s z_rel=%.3f z_est=%.3f fw_alt=%s vz=%.3f guard=%d]",
            now,
            "na" if along is None else f"{along:.3f}",
            report.state.value,
            z_rel,
            z_est,
            fw_alt,
            vz,
            int(report.guard_active),
        )

    def override_along_track(self, along_track_m: float, ttl_sec: float) -> None:
        """Forward an externally observed along-track position to the platform monitor.

        Serialized with the monitor's own updates, which run on the odometry
        thread. See :meth:`PlatformMonitor.override_along_track`.
        """
        with self._platform_lock:
            self.platform_monitor.override_along_track(along_track_m, ttl_sec)

    def platform_report(self) -> PlatformReport:
        """The platform monitor's verdict on the most recent sample.

        The edge guard's source: the altitude governor, the failsafe and the
        ceiling all read the same report.
        """
        with self._lock:
            return self._platform_report

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> OdometrySnapshot:
        """Capture the full odometric state in one lock acquisition."""
        with self._lock:
            ground = self.ground_reference_altitude
            raw_altitude = self.current_raw_altitude
            return OdometrySnapshot(
                x=self.current_x,
                y=self.current_y,
                raw_altitude=raw_altitude,
                relative_altitude=raw_altitude if ground is None else raw_altitude - ground,
                vx=self.current_vx,
                vy=self.current_vy,
                vz=self.current_vz,
                yaw=self.current_yaw,
                timestamp=self.last_odometry_timestamp,
                sample_count=self.sample_count,
                ground_reference=ground,
                takeoff_x=self.takeoff_x,
                takeoff_y=self.takeoff_y,
                floor_offset_m=max(0.0, float(self.platform_cfg.height_m)),
                along_track_m=self._platform_report.along_track_m,
                platform_state=self._platform_report.state.value,
                guard_active=self._platform_report.guard_active,
                firmware_altitude_m=self._firmware_altitude,
            )

    # ------------------------------------------------------------- calibration

    @property
    def altitude_ceiling(self) -> float:
        """Absolute relative-altitude ceiling, in metres.

        Derived on read rather than frozen at construction, so a target altitude
        changed after the supervisor was built is reflected instead of silently
        ignored.

        Measured in the control frame, above the launch point: the target is
        ``target_altitude_m - platform.height_m``. While the platform edge guard
        is active ``platform.ceiling_allowance_m`` is added -- re-entering the
        pad reads as a sudden loss of height and the firmware climbs before the
        guard can take it back -- and a known venue ceiling caps the result at
        ``venue_ceiling_agl_m - platform.height_m`` in every case.

        Never takes the state lock: :meth:`_store_sample` reads it while
        holding it.
        """
        platform = self.platform_cfg
        height = max(0.0, float(platform.height_m))
        ceiling = (
            self.kinematics_cfg.target_altitude_m
            - height
            + self.kinematics_cfg.altitude_ceiling_margin_m
        )
        if self._platform_report.guard_active:
            ceiling += max(0.0, float(platform.ceiling_allowance_m))
        venue = platform.venue_ceiling_agl_m
        if isinstance(venue, (int, float)) and not isinstance(venue, bool) and math.isfinite(venue):
            ceiling = min(ceiling, float(venue) - height)
        return ceiling

    def calibrate_ground_reference(self) -> bool:
        """Freeze the ground-level reference (x0, y0, z0) with outlier rejection.

        Returns
        -------
        bool
            True when a calibration was accepted. False when too few samples
            arrived or the altitude buffer is too dispersed to trust, in which
            case no reference is set and the caller must decide whether to
            continue.
        """
        cfg = self.calibration_cfg

        with self._lock:
            samples_z = list(self._buffer_z)
            samples_x = list(self._buffer_x)
            samples_y = list(self._buffer_y)
            fallback = (self.current_raw_altitude, self.current_x, self.current_y)
            observed = self.sample_count

        if observed == 0:
            logger.error(
                "Ground calibration rejected: no odometry samples received. "
                "Verify /bebop/odom is being published."
            )
            return False

        if len(samples_z) < cfg.min_ground_samples:
            logger.warning(
                "Ground calibration on %d samples (minimum %d). Using instantaneous reading; "
                "z0 confidence is reduced.",
                len(samples_z),
                cfg.min_ground_samples,
            )
            z0, x0, y0 = fallback
            dispersion = 0.0
        else:
            z0, dispersion = robust_center(samples_z, cfg.mad_outlier_sigma)
            x0, _ = robust_center(samples_x, cfg.mad_outlier_sigma)
            y0, _ = robust_center(samples_y, cfg.mad_outlier_sigma)

            if dispersion > cfg.max_ground_dispersion_m:
                logger.error(
                    "Ground calibration rejected: altitude dispersion %.3f m exceeds %.3f m. "
                    "Surface is not level or the sensor has not settled.",
                    dispersion,
                    cfg.max_ground_dispersion_m,
                )
                return False

        with self._lock:
            self.ground_reference_altitude = z0
            self.takeoff_x = x0
            self.takeoff_y = y0
            self._ceiling_breach_streak = 0
            self._ceiling_breach_started = None

        logger.info(
            "Ground reference calibrated from %d samples: x0=%.3f m, y0=%.3f m, z0=%.3f m "
            "(robust sigma=%.4f m). Altitude ceiling: %.3f m.",
            len(samples_z),
            x0,
            y0,
            z0,
            dispersion,
            self.altitude_ceiling,
        )
        return True

    def freeze_hover_takeoff_origin(self) -> bool:
        """Adopt the stabilized airborne position as the definitive return target.

        Kept deliberately separate from :meth:`calibrate_ground_reference`: the
        ground reference must be measured on the ground, but the horizontal
        origin must be measured *after* the lift-off transient, or ground-effect
        turbulence is baked into the coordinate the drone later returns to.
        """
        snap = self.snapshot()

        with self._lock:
            self.takeoff_x = snap.x
            self.takeoff_y = snap.y
            self._hover_origin_frozen = True

        logger.info(
            "Airborne hover origin frozen: x0=%.3f m, y0=%.3f m (relative altitude %.3f m).",
            snap.x,
            snap.y,
            snap.relative_altitude,
        )
        return True

    # ------------------------------------------------------------------ health

    def telemetry_health(self, timeout_sec: Optional[float] = None) -> TelemetryHealth:
        """Classify the odometry stream as never-received, stale, or healthy.

        Timed on ``time.monotonic``. The heartbeat used the wall clock on both
        the write and the read, against a three-second timeout -- so a forward
        NTP correction larger than that made healthy odometry read as STALE, and
        ``evaluate_system_health`` turns STALE into an emergency landing. A
        backward step did the opposite and masked a genuine stall. This is the
        same defect ``engine.rate`` documents as its reason for moving
        ``Deadline`` off the wall clock; the reasoning applies identically here.
        """
        timeout = timeout_sec or self.timeouts_cfg.odometry_heartbeat_timeout_sec
        with self._lock:
            last = self.last_odometry_timestamp
            count = self.sample_count

        if count == 0 or last == 0.0:
            return TelemetryHealth.NEVER_RECEIVED
        if (time.monotonic() - last) > timeout:
            return TelemetryHealth.STALE
        return TelemetryHealth.HEALTHY

    def is_telemetry_healthy(self, timeout_sec: Optional[float] = None) -> bool:
        """True only when odometry has been received and is fresh."""
        return self.telemetry_health(timeout_sec).is_healthy

    def is_ceiling_breached(self) -> bool:
        """True when the relative altitude exceeds the safety ceiling for consecutive samples.

        One exception, re-entering the takeoff pad: while the platform monitor
        reports ``ENTERING``, a breach younger than
        ``platform.reentry_breach_grace_sec`` is not reported. The sonar step at
        the pad edge reads as a sudden loss of height and the firmware climbs on
        its own; an emergency landing there sets the airframe down on the edge
        of a pad a metre above the floor. The breach drives the full guard
        descent instead (:meth:`reentry_descent_demanded`). It is reported as
        usual once the grace expires, or as soon as the airframe is no longer
        ``ENTERING`` -- over the pad top or back off it -- with the ceiling
        still exceeded.
        """
        streak_threshold = max(1, getattr(self.kinematics_cfg, "ceiling_breach_streak", 3))
        now = time.monotonic()
        with self._lock:
            breached = self._ceiling_breach_streak >= streak_threshold
            inhibited = breached and self._reentry_grace_active(now)
            altitude = self.current_raw_altitude - (self.ground_reference_altitude or 0.0)
            ceiling = self.altitude_ceiling
            started = self._ceiling_breach_started
            announce = inhibited != self._reentry_grace_logged and (inhibited or breached)
            self._reentry_grace_logged = inhibited
        if announce:
            logger.warning(
                "[PLATFORM reentry_breach %s z_rel=%.3f ceiling=%.3f elapsed=%.2f grace=%.2f]",
                "inhibited" if inhibited else "reported",
                altitude,
                ceiling,
                0.0 if started is None else now - started,
                self.platform_cfg.reentry_breach_grace_sec,
            )
        return breached and not inhibited

    def reentry_descent_demanded(self) -> bool:
        """True while re-entering the pad above the ceiling, inside the grace.

        The altitude governor answers it with the full guard descent
        (``platform.guard_descent_cap``) instead of its proportional law.
        Counts from the first sample above the ceiling, not from the debounced
        breach, so the descent starts with the climb rather than three samples
        into it.
        """
        now = time.monotonic()
        with self._lock:
            return self._ceiling_breach_streak > 0 and self._reentry_grace_active(now)

    def _reentry_grace_active(self, now: float) -> bool:
        """Whether a breach in progress is inside the re-entry grace. Lock held."""
        if self._platform_report.state is not PlatformState.ENTERING:
            return False
        if self.platform_cfg.edge_ahead_m is None or self._ceiling_breach_started is None:
            return False
        grace = self.platform_cfg.reentry_breach_grace_sec
        return grace > 0.0 and now - self._ceiling_breach_started < grace

    @property
    def ceiling_breach_streak(self) -> int:
        """Current count of consecutive samples above the safety ceiling."""
        with self._lock:
            return self._ceiling_breach_streak

    # ------------------------------------------------ backwards-compatible API

    @property
    def relative_altitude(self) -> float:
        """Current altitude above the calibrated ground reference, in metres."""
        return self.snapshot().relative_altitude

    def get_current_horizontal_speed(self) -> float:
        """Instantaneous horizontal ground speed magnitude, m/s."""
        return self.snapshot().horizontal_speed

    def is_hover_settled(self, max_speed_mps: float = 0.04) -> bool:
        """True when horizontal motion is below the given threshold."""
        return self.snapshot().horizontal_speed <= max_speed_mps

    def get_body_frame_launch_error(self) -> Tuple[float, float, float]:
        """Body-frame (FLU) error vector toward the launch origin."""
        return self.snapshot().body_frame_launch_error()
