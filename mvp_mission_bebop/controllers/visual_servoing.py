"""Image-based visual servoing with geometrically coupled approach guidance.

Three phases run in sequence.

**Centering** aligns the target on the vertical optical axis using lateral
velocity alone. Forward motion is locked at zero so the drone cannot overfly a
target it has not yet pointed at.

**Approaching** advances along-track while the gimbal pitches down toward nadir.
The coupling between the two is the substance of this stage, and it is where the
previous implementation was weakest: it forced the gimbal down by at least two
degrees per frame whenever the target sat inside the lateral corridor, and then
derived forward speed from how far that ramp had progressed. Both halves were
therefore open-loop functions of elapsed frames. The vertical PID's output was
discarded whenever it disagreed, so the "PID" on the pitch axis never actually
closed a loop.

Here the camera is pointed at the target by geometry -- the measured pixel
bearing gives the depression of the target directly -- and forward speed comes
from the ground range that the same projection yields. Both are functions of the
current observation, so the loop is closed. As the drone closes, the target
descends in frame, the gimbal follows it down, the depression grows, the range
estimate shrinks, and the approach decelerates. The coupling falls out of the
geometry instead of being imposed on it.

**Nadir** holds the gimbal down and performs fine two-axis positioning over the
target using the same projection.

When the altitude estimate is unusable the controller says so and falls back to
the previous open-loop ramp, which is what ``VisionConfig.ibvs_enabled`` and
``min_altitude_for_ibvs_m`` select between.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from mvp_mission_bebop.controllers.geometry import (
    CameraIntrinsics,
    GroundProjection,
    project_to_ground,
)
from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import (
    FlightKinematicsConfig,
    GimbalConstraintsConfig,
    GimbalPIDConfig,
    LateralPIDConfig,
    VisionConfig,
)

logger = logging.getLogger("VisualServoing")


class TrackingPhase(str, Enum):
    """Sub-phases of the visual approach."""

    CENTERING = "centering"
    APPROACHING = "approaching"
    NADIR = "nadir"


@dataclass(frozen=True)
class ServoCommand:
    """One cycle of visual servoing output."""

    #: Normalized along-track velocity command.
    vx: float
    #: Normalized cross-track velocity command.
    vy: float
    #: Absolute gimbal tilt to command, in degrees (negative is downward).
    tilt_deg: float
    #: Radial pixel error from the frame centre.
    pixel_error: float
    #: Estimated horizontal distance to the target, when geometry allows.
    ground_range_m: Optional[float]
    #: Depression of the target below horizontal, in degrees.
    depression_deg: Optional[float]
    phase: TrackingPhase
    nadir_aligned: bool
    note: str


class VisualServoingController:
    """Couples along-track velocity to gimbal pitch through ground projection."""

    def __init__(
        self,
        gimbal_config: GimbalConstraintsConfig,
        gimbal_pid_cfg: GimbalPIDConfig,
        lateral_pid_cfg: LateralPIDConfig,
        vision_cfg: VisionConfig,
        kinematics_cfg: FlightKinematicsConfig,
        calibration: Optional[SpeedCalibration] = None,
    ) -> None:
        self.gimbal_cfg = gimbal_config
        self.gimbal_pid_cfg = gimbal_pid_cfg
        self.lateral_pid_cfg = lateral_pid_cfg
        self.vision_cfg = vision_cfg
        self.kinematics_cfg = kinematics_cfg
        self.calibration = calibration or SpeedCalibration(kinematics_cfg.normalized_to_mps)

        lateral_limit = abs(lateral_pid_cfg.output_limits[1])
        self._lateral = FilteredPID(
            PIDGains(
                kp=lateral_pid_cfg.kp,
                ki=lateral_pid_cfg.ki,
                kd=lateral_pid_cfg.kd,
                output_limits=(-lateral_limit, lateral_limit),
                output_deadband=lateral_pid_cfg.deadband,
            )
        )
        # Retained for the open-loop fallback path, which still servos the
        # gimbal on normalized vertical pixel error.
        self._gimbal = FilteredPID(
            PIDGains(
                kp=gimbal_pid_cfg.kp,
                ki=gimbal_pid_cfg.ki,
                kd=gimbal_pid_cfg.kd,
                output_limits=gimbal_pid_cfg.output_limits,
            )
        )

        approach_mps = self.calibration.to_mps(kinematics_cfg.max_approach_forward_speed)
        self._forward_profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(approach_mps, 1e-3),
                max_accel=kinematics_cfg.max_accel_mps2,
                max_jerk=kinematics_cfg.max_jerk_mps3,
            )
        )

        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._decentered_frames = 0
        self._nadir_excursion_frames = 0
        self._transitioned_to_approach = False
        self._fallback_warned = False

    # ------------------------------------------------------------- properties

    @property
    def phase(self) -> TrackingPhase:
        """Current phase of the approach."""
        return self._phase

    def pop_transition_to_approach(self) -> bool:
        """Consume the centering-to-approaching transition flag."""
        transitioned = self._transitioned_to_approach
        self._transitioned_to_approach = False
        return transitioned

    def reset(self) -> None:
        """Clear all controller state and return to centering."""
        self._lateral.reset()
        self._gimbal.reset()
        self._forward_profile.reset()
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._decentered_frames = 0
        self._nadir_excursion_frames = 0
        self._transitioned_to_approach = False

    # ------------------------------------------------------------------ cycle

    def compute(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        relative_altitude_m: float,
        dt: float,
    ) -> ServoCommand:
        """Compute one cycle of servoing from a single detection.

        Parameters
        ----------
        target_center : Tuple[float, float]
            Target centroid in pixels.
        frame_dimensions : Tuple[int, int]
            ``(width, height)`` of the frame.
        current_tilt_deg : float
            Gimbal tilt currently commanded, in degrees, negative downward.
        relative_altitude_m : float
            Height above the calibrated ground reference.
        dt : float
            True elapsed interval, from ``LoopRate.tick``.
        """
        width, height = frame_dimensions
        center_x, center_y = width / 2.0, height / 2.0
        err_x = float(target_center[0]) - center_x
        err_y = float(target_center[1]) - center_y
        pixel_error = math.hypot(err_x, err_y)
        norm_err_x = err_x / center_x

        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            horizontal_fov_deg=self.vision_cfg.horizontal_fov_deg,
            vertical_fov_deg=self.vision_cfg.vertical_fov_deg,
        )
        projection = self._project(intrinsics, target_center, relative_altitude_m, current_tilt_deg)

        # Cross-track control is the same in every phase: hold the target on the
        # vertical centreline. Driven on normalized pixel error so the gains
        # tuned in flight carry over unchanged.
        vy = self._lateral_command(norm_err_x, dt)

        if self._phase is TrackingPhase.CENTERING:
            return self._centering(err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt)
        if self._phase is TrackingPhase.APPROACHING:
            return self._approaching(
                err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt
            )
        return self._nadir(err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt)

    # ----------------------------------------------------------------- phases

    def _centering(self, err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt):
        """Hold position and align the target horizontally before advancing."""
        tolerance = self.vision_cfg.optical_center_tolerance_px
        vertical_tolerance = tolerance * self.vision_cfg.centering_vertical_ratio

        # Keep the gimbal near its search attitude, nudging only within a narrow
        # band: a large pitch excursion here would move the target out of frame
        # before the drone has any along-track authority to follow it.
        tilt = self._slew(
            current_tilt_deg,
            self._pointing_tilt(current_tilt_deg, err_y, projection, dt),
            dt,
            lower=self.gimbal_cfg.search_tilt_deg - 5.0,
            upper=self.gimbal_cfg.search_tilt_deg + 5.0,
        )

        if abs(err_x) <= tolerance and abs(err_y) <= vertical_tolerance:
            self._centered_frames += 1
            if self._centered_frames >= self.vision_cfg.confirmation_frames:
                logger.info(
                    "Optical centre locked (err=%.1f, %.1f px). Centering -> approaching.",
                    err_x,
                    err_y,
                )
                self._phase = TrackingPhase.APPROACHING
                self._transitioned_to_approach = True
                self._decentered_frames = 0
        else:
            self._centered_frames = 0

        return ServoCommand(
            vx=0.0,
            vy=vy,
            tilt_deg=tilt,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.CENTERING,
            nadir_aligned=False,
            note="centering",
        )

    def _approaching(self, err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt):
        """Advance along-track with the gimbal tracking the target down to nadir."""
        vision_cfg = self.vision_cfg
        corridor = vision_cfg.optical_center_tolerance_px * vision_cfg.approach_corridor_ratio
        in_corridor = abs(err_x) <= corridor

        tilt = self._slew(
            current_tilt_deg,
            self._pointing_tilt(current_tilt_deg, err_y, projection, dt),
            dt,
            lower=self.gimbal_cfg.nadir_tilt_deg,
            upper=self.gimbal_cfg.search_tilt_deg,
        )

        if in_corridor:
            self._decentered_frames = 0
            target_mps, note = self._approach_speed(projection, tilt)
        else:
            # Lateral safety corridor: stop closing until the target is back on
            # the centreline, otherwise the approach converges on a point the
            # drone is not actually aimed at.
            self._decentered_frames += 1
            target_mps, note = 0.0, f"outside corridor ({err_x:+.0f} px)"
            if self._decentered_frames >= vision_cfg.decentered_frames_to_revert:
                logger.warning(
                    "Target drifted %.1f px off-axis for %d frames. Reverting to centering.",
                    err_x,
                    self._decentered_frames,
                )
                self._phase = TrackingPhase.CENTERING
                self._centered_frames = 0
                self._decentered_frames = 0

        profiled = self._forward_profile.step(target_mps, dt)
        vx = max(0.0, self.calibration.to_normalized(profiled))

        if self._reached_nadir(tilt, projection):
            logger.info(
                "Nadir attitude reached (tilt %.1f deg, range %s). Approaching -> nadir.",
                tilt,
                f"{projection.ground_range_m:.2f} m" if projection else "unavailable",
            )
            self._phase = TrackingPhase.NADIR
            self._nadir_excursion_frames = 0

        return ServoCommand(
            vx=vx,
            vy=vy,
            tilt_deg=tilt,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.APPROACHING,
            nadir_aligned=False,
            note=note,
        )

    def _nadir(self, err_x, err_y, pixel_error, vy, current_tilt_deg, projection, dt):
        """Fine two-axis positioning directly over the target."""
        vision_cfg = self.vision_cfg
        # Slewed rather than assigned: entering this phase from a shallower
        # attitude would otherwise step the gimbal by the whole remaining angle
        # in a single cycle, which is both mechanically abrupt and enough to
        # throw the target out of frame at the moment it matters most.
        tilt = self._slew(
            current_tilt_deg,
            self.gimbal_cfg.nadir_tilt_deg,
            dt,
            lower=self.gimbal_cfg.nadir_tilt_deg,
            upper=self.gimbal_cfg.search_tilt_deg,
        )
        deadband_px = vision_cfg.optical_center_tolerance_px * vision_cfg.nadir_deadband_ratio

        if projection is not None:
            # At nadir the projection's along-track component is the residual
            # offset of the target ahead of (positive) or behind (negative) the
            # drone, so it can be driven to zero directly.
            target_mps = self._fine_speed(projection.forward_m)
        else:
            # Without geometry, fall back to normalized vertical pixel error.
            target_mps = self._fine_speed(
                -err_y / (vision_cfg.vertical_fov_deg or 1.0) * 0.01
            )

        if abs(err_y) < deadband_px:
            target_mps = 0.0
        profiled = self._forward_profile.step(target_mps, dt)
        vx = self.calibration.to_normalized(profiled)

        if abs(err_x) < deadband_px:
            vy = 0.0

        aligned = pixel_error <= (
            vision_cfg.optical_center_tolerance_px * vision_cfg.nadir_alignment_ratio
        )

        # The previous implementation had no exit from this phase: once entered,
        # only a full reset could leave it, so a target that drifted away was
        # tracked forever from a locked-down gimbal.
        if aligned:
            self._nadir_excursion_frames = 0
        else:
            self._nadir_excursion_frames += 1
            if self._nadir_excursion_frames >= vision_cfg.nadir_exit_frames:
                logger.warning(
                    "Target left the nadir window for %d frames (err %.1f px). "
                    "Reverting to approaching.",
                    self._nadir_excursion_frames,
                    pixel_error,
                )
                self._phase = TrackingPhase.APPROACHING
                self._nadir_excursion_frames = 0

        return ServoCommand(
            vx=vx,
            vy=vy,
            tilt_deg=tilt,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.NADIR,
            nadir_aligned=aligned,
            note="nadir",
        )

    # ---------------------------------------------------------------- helpers

    def _project(
        self,
        intrinsics: CameraIntrinsics,
        target_center: Tuple[float, float],
        altitude_m: float,
        tilt_deg: float,
    ) -> Optional[GroundProjection]:
        """Ground projection, or ``None`` when the geometry cannot be trusted."""
        if not self.vision_cfg.ibvs_enabled:
            return None
        if altitude_m < self.vision_cfg.min_altitude_for_ibvs_m:
            if not self._fallback_warned:
                self._fallback_warned = True
                logger.warning(
                    "Relative altitude %.2f m is below the %.2f m needed to trust the ground "
                    "projection. Falling back to the open-loop gimbal ramp.",
                    altitude_m,
                    self.vision_cfg.min_altitude_for_ibvs_m,
                )
            return None
        return project_to_ground(intrinsics, target_center, altitude_m, tilt_deg)

    def _pointing_tilt(
        self,
        current_tilt_deg: float,
        err_y: float,
        projection: Optional[GroundProjection],
        dt: float,
    ) -> float:
        """Tilt that places the optical axis on the target.

        With geometry available this is a direct pointing solution: the target's
        depression is measured, so the gimbal is simply told to look there. The
        open-loop ramp is only the fallback.
        """
        if projection is not None:
            desired = -projection.depression_deg
            gain = self.gimbal_cfg.tracking_gain
            return current_tilt_deg + gain * (desired - current_tilt_deg)

        # Fallback: servo on normalized vertical pixel error, with the legacy
        # guarantee of monotonic progress toward nadir.
        correction = self._gimbal.update(-err_y / max(1.0, abs(err_y) + 1.0), dt)
        return current_tilt_deg + min(correction, -self.vision_cfg.legacy_gimbal_ramp_deg)

    def _slew(
        self, current_deg: float, desired_deg: float, dt: float, *, lower: float, upper: float
    ) -> float:
        """Rate-limit and bound a gimbal command.

        ``max_slew_limit_deg`` was declared in the configuration and never read.
        Without it the pitch axis could be commanded to step arbitrarily far
        between cycles, which is both mechanically abrupt and enough to throw the
        target out of frame in one move.
        """
        max_step = self.gimbal_cfg.max_slew_limit_deg * dt
        bounded = max(current_deg - max_step, min(current_deg + max_step, desired_deg))
        return max(lower, min(upper, bounded))

    def _approach_speed(
        self, projection: Optional[GroundProjection], tilt_deg: float
    ) -> Tuple[float, str]:
        """Along-track speed demand for the approach phase."""
        cap = self.calibration.to_mps(self.kinematics_cfg.max_approach_forward_speed)

        if projection is None:
            # Open-loop fallback: speed decays with how far the gimbal ramp has
            # progressed toward nadir, which is what the previous version did.
            span = self.gimbal_cfg.search_tilt_deg - self.gimbal_cfg.nadir_tilt_deg
            progress = 1.0 if span <= 0.0 else (tilt_deg - self.gimbal_cfg.nadir_tilt_deg) / span
            return cap * max(0.20, min(1.0, progress)), "open-loop ramp (no geometry)"

        # Closed-loop: brake against the measured ground range so the drone
        # arrives over the target at rest rather than at a speed dictated by how
        # many frames the gimbal ramp has consumed.
        speed = braking_velocity(
            projection.ground_range_m,
            self.kinematics_cfg.max_accel_mps2,
            cruise_velocity=cap,
            arrival_tolerance=self.vision_cfg.nadir_range_threshold_m,
        )
        return speed, f"range {projection.ground_range_m:.2f} m"

    def _fine_speed(self, forward_m: float) -> float:
        """Along-track demand for fine positioning at nadir."""
        cap = self.calibration.to_mps(self.kinematics_cfg.max_approach_forward_speed) * 0.40
        speed = braking_velocity(
            abs(forward_m), self.kinematics_cfg.max_accel_mps2, cruise_velocity=cap
        )
        return math.copysign(speed, forward_m)

    def _lateral_command(self, norm_err_x: float, dt: float) -> float:
        """Cross-track velocity from normalized horizontal pixel error.

        Sign convention: the body frame is FLU, so positive ``vy`` is to the
        left. A target right of centre has positive ``norm_err_x`` and must be
        answered with negative ``vy``. Feeding the error in as the measurement
        against a zero setpoint produces exactly that, since the controller's
        error term is then ``-norm_err_x``.

        The previous version defeated its own deadband: whenever the PID
        returned zero -- including when the output deadband had deliberately
        suppressed it -- a fallback reinjected a raw proportional term, so the
        configured deadband never took effect and the drone jittered around the
        centreline.
        """
        return self._lateral.update(norm_err_x, dt)

    def _reached_nadir(self, tilt_deg: float, projection: Optional[GroundProjection]) -> bool:
        """Has the approach arrived over the target?"""
        at_nadir_attitude = tilt_deg <= self.gimbal_cfg.nadir_tilt_deg + 2.5
        if projection is None:
            return at_nadir_attitude
        return at_nadir_attitude and (
            projection.ground_range_m <= self.vision_cfg.nadir_range_threshold_m
        )
