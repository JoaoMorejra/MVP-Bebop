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

from mvp_mission_bebop.parameters import (
    CalibrationConfig,
    FlightKinematicsConfig,
    TimeoutsConfig,
)

if TYPE_CHECKING:  # pragma: no cover - import kept out of the runtime path
    from nav_msgs.msg import Odometry

logger = logging.getLogger("OdometrySupervisor")

#: Scale factor converting a median absolute deviation into a standard
#: deviation estimate for normally distributed data.
MAD_TO_SIGMA: Final[float] = 1.4826


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
    ) -> None:
        self.kinematics_cfg = kinematics_cfg
        self.timeouts_cfg = timeouts_cfg
        self.calibration_cfg = calibration_cfg or CalibrationConfig()

        self._lock = threading.Lock()
        self.ground_reference_altitude: Optional[float] = None
        self.current_raw_altitude: float = 0.0
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

        self._buffer_z: List[float] = []
        self._buffer_x: List[float] = []
        self._buffer_y: List[float] = []

    # --------------------------------------------------------------- ingestion

    def odometry_callback(self, msg: "Odometry") -> None:
        """Ingest a ``nav_msgs/Odometry`` message. Runs on an executor thread."""
        position = msg.pose.pose.position
        twist = msg.twist.twist.linear
        orientation = msg.pose.pose.orientation

        # Yaw from the quaternion's z-w components. Computed before taking the
        # lock so trigonometry does not extend the critical section.
        siny_cosp = 2.0 * (orientation.w * orientation.z + orientation.x * orientation.y)
        cosy_cosp = 1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        self._store_sample(
            x=float(position.x),
            y=float(position.y),
            z=float(position.z),
            vx=float(twist.x),
            vy=float(twist.y),
            vz=float(twist.z),
            yaw=yaw,
        )

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
    ) -> None:
        """Commit one sample under the lock and buffer it if pre-calibration."""
        with self._lock:
            self.current_x = x
            self.current_y = y
            self.current_raw_altitude = z
            self.current_vx = vx
            self.current_vy = vy
            self.current_vz = vz
            self.current_yaw = yaw
            self.last_odometry_timestamp = time.time()
            self.sample_count += 1

            if self.ground_reference_altitude is None:
                depth = self.calibration_cfg.sample_buffer_size
                self._buffer_z.append(z)
                self._buffer_x.append(x)
                self._buffer_y.append(y)
                if len(self._buffer_z) > depth:
                    del self._buffer_z[: len(self._buffer_z) - depth]
                    del self._buffer_x[: len(self._buffer_x) - depth]
                    del self._buffer_y[: len(self._buffer_y) - depth]

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
            )

    # ------------------------------------------------------------- calibration

    @property
    def altitude_ceiling(self) -> float:
        """Absolute relative-altitude ceiling, in metres.

        Derived on read rather than frozen at construction, so a target altitude
        changed after the supervisor was built is reflected instead of silently
        ignored.
        """
        return (
            self.kinematics_cfg.target_altitude_m
            + self.kinematics_cfg.altitude_ceiling_margin_m
        )

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

        logger.info(
            "Airborne hover origin frozen: x0=%.3f m, y0=%.3f m (relative altitude %.3f m).",
            snap.x,
            snap.y,
            snap.relative_altitude,
        )
        return True

    # ------------------------------------------------------------------ health

    def telemetry_health(self, timeout_sec: Optional[float] = None) -> TelemetryHealth:
        """Classify the odometry stream as never-received, stale, or healthy."""
        timeout = timeout_sec or self.timeouts_cfg.odometry_heartbeat_timeout_sec
        with self._lock:
            last = self.last_odometry_timestamp
            count = self.sample_count

        if count == 0 or last == 0.0:
            return TelemetryHealth.NEVER_RECEIVED
        if (time.time() - last) > timeout:
            return TelemetryHealth.STALE
        return TelemetryHealth.HEALTHY

    def is_telemetry_healthy(self, timeout_sec: Optional[float] = None) -> bool:
        """True only when odometry has been received and is fresh."""
        return self.telemetry_health(timeout_sec).is_healthy

    def is_ceiling_breached(self) -> bool:
        """True when the relative altitude exceeds the safety ceiling."""
        return self.snapshot().relative_altitude > self.altitude_ceiling

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
