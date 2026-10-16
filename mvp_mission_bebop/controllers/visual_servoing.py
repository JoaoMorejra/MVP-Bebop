"""Image-based lateral servoing with a latched freeze.

The lateral flight pattern searches by translating to the left with the camera
at the search tilt, so the target is confirmed somewhere ahead of the track at a
horizontal range ``R`` fixed by where the operator laid the track out. Stage 3
then has one job: bring the target onto the vertical centreline of the image by
translating along the same lateral axis, and stop. Three phases do it.

**Centering** commands the body-frame lateral channel alone, from the
normalized horizontal pixel error, through a filtered PID and the actuator
shaper. The along-track command is a literal ``0.0`` -- not a clamped or
profiled value -- and the gimbal tilt is passed through unchanged.

Both restrictions follow from the geometry rather than from caution. Without
along-track motion ``R`` is constant for the whole stage, so a target that was
inside the frame at confirmation stays inside it vertically: the depression
``atan(h / R)`` does not change while the drone slides sideways, and nothing in
this stage needs the gimbal. The gimbal belongs to Stage 4, where it is the only
thing that moves. Lateral translation only changes the horizontal bearing
``atan(y / sqrt(R^2 + h^2))``, which is exactly the error the lateral loop nulls.

The phase earns credit, one frame at a time, only while the target is
*measured* inside the centring gate, and loses one credit for every frame
outside it, measured or predicted. A predicted (coasted) frame inside the gate
leaves the credit unchanged: the tracker's extrapolation is an assumption about
where the target went, and freezing the airframe on an assumption would
photograph wherever the assumption was wrong. A predicted frame outside the gate
still costs credit, because evidence against being centred is acted on
conservatively whatever its source.

**Locked** is entered once the credit reaches ``vision.alignment_dwell_cycles``
on a measured in-gate frame with the airframe at rest. It is latched: every
later cycle returns ``vx = vy = 0`` whatever the observation, and the lateral
loop is silenced with its state, so nothing banked before the freeze can be
discharged after it.

**Reacquiring** runs when the target cannot be resolved. It holds the tilt and
creeps laterally toward the side the target was last seen on, for a bounded
window, and never along-track. A loss with the target last seen inside the
gate authorises no motion at all: a detector dropout at the centreline says
nothing about which way to go.
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
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
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
    """Sub-phases of the lateral servo."""

    #: Lateral loop running, centring credit accumulating.
    CENTERING = "centering"
    #: Latched lateral freeze. Terminal for the stage.
    LOCKED = "locked"
    #: Target lost. Hold the tilt and creep toward the side it left from.
    REACQUIRING = "reacquiring"


@dataclass(frozen=True)
class TrackAnchor:
    """The last observation reacquisition recovers toward.

    Refreshed on every resolved observation so that a loss has a concrete side
    to creep toward. Carries the frame centre with the pixel so the anchor alone
    yields the last horizontal error, without the frame size of the cycle in
    which the target is already gone.
    """

    #: Gimbal tilt at which the target was last seen, degrees.
    tilt_deg: float
    #: Target centroid in pixels at that moment.
    target_px: Tuple[float, float]
    #: Horizontal centre of the frame the centroid was measured in, pixels.
    frame_center_x: float
    #: Ground range when geometry was available, metres. Diagnostic only.
    ground_range_m: Optional[float]

    @property
    def err_x_px(self) -> float:
        """Horizontal pixel error of the last sighting, positive to the right."""
        return self.target_px[0] - self.frame_center_x


@dataclass(frozen=True)
class ServoCommand:
    """One cycle of visual servoing output."""

    #: Normalized along-track velocity command. Always ``0.0``.
    vx: float
    #: Normalized cross-track velocity command, positive to the left (FLU).
    vy: float
    #: Absolute gimbal tilt, degrees. Always the tilt the caller passed in.
    tilt_deg: float
    #: Radial pixel error from the frame centre.
    pixel_error: float
    #: Estimated horizontal distance to the target, when geometry allows.
    ground_range_m: Optional[float]
    #: Depression of the target below horizontal, in degrees.
    depression_deg: Optional[float]
    phase: TrackingPhase
    #: True when ``|err_x|`` is inside the centring tolerance this cycle.
    laterally_centered: bool
    #: True once the lateral freeze has latched.
    locked: bool
    note: str
    #: Effective optical tolerance in force this cycle, in pixels. Reported so
    #: the annotated stream and the flight log show the gate the controller is
    #: actually applying rather than the nominal configured value.
    tolerance_px: float = 0.0
    #: Cross-track alignment in [0, 1]: 1.0 inside the tolerance, decaying with
    #: the lateral error. Diagnostic only; it scales no command.
    alignment: float = 1.0
    #: True once reacquisition has exhausted its window and the step should stop.
    recovery_exhausted: bool = False
    #: Normalized yaw-rate command, positive to the left. Always ``0.0`` from the
    #: lateral law; nonzero only from ``YawServoingController`` (Simple mission
    #: profile), whose consumer must hold a yaw window to transmit it.
    vyaw: float = 0.0


class VisualServoingController:
    """Nulls the horizontal pixel error with lateral velocity and latches a freeze."""

    def __init__(
        self,
        gimbal_config: GimbalConstraintsConfig,
        gimbal_pid_cfg: GimbalPIDConfig,
        lateral_pid_cfg: LateralPIDConfig,
        vision_cfg: VisionConfig,
        kinematics_cfg: FlightKinematicsConfig,
        calibration: Optional[SpeedCalibration] = None,
    ) -> None:
        """Build the lateral servo.

        Parameters
        ----------
        gimbal_config : GimbalConstraintsConfig
            Gimbal limits. Retained for the diagnostics and the constructor
            contract; this stage never commands the gimbal.
        gimbal_pid_cfg : GimbalPIDConfig
            Retained for the constructor contract. No pitch loop runs here.
        lateral_pid_cfg : LateralPIDConfig
            Gains, limits and actuator floor of the lateral channel.
        vision_cfg : VisionConfig
            Optical tolerances, dwell and reacquisition windows.
        kinematics_cfg : FlightKinematicsConfig
            Kinematic envelope. Retained for the constructor contract.
        calibration : SpeedCalibration, optional
            Normalized-to-m/s conversion; defaults to
            ``kinematics_cfg.normalized_to_mps``.
        """
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

        # The cross-track axis is the one this stage drives, and it was the one
        # the driver was throwing away. ``min_effective_velocity`` is rendered
        # as a duty cycle rather than a floor, so the commanded mean still
        # tracks the demand continuously down to zero while every emitted
        # command survives the driver's int8 truncation.
        self._lateral_shaper = QuantizedCommandShaper(
            min_effective=lateral_pid_cfg.min_effective_velocity,
            quantization_step=lateral_pid_cfg.quantization_step,
        )

        self._phase = TrackingPhase.CENTERING
        #: Centring credit: measured in-gate frames, decayed by out-of-gate ones.
        self._centered_frames = 0
        #: Latched lateral freeze.
        self._locked = False
        self._fallback_warned = False
        #: Last resolved observation, the anchor reacquisition recovers toward.
        self._anchor: Optional[TrackAnchor] = None
        #: Time spent in the current occupancy of the reacquisition phase.
        self._reacquire_elapsed_sec = 0.0
        #: Consecutive measured re-detections accumulated while reacquiring.
        self._reacquire_hits = 0
        #: Set once reacquisition has run out of window.
        self._recovery_exhausted = False
        #: Effective tolerance, refreshed from the frame size each cycle.
        self._tolerance_px = vision_cfg.optical_center_tolerance_px

    # ------------------------------------------------------------- properties

    @property
    def phase(self) -> TrackingPhase:
        """Current phase of the servo."""
        return self._phase

    @property
    def locked(self) -> bool:
        """True once the lateral freeze has latched."""
        return self._locked

    def reset(self) -> None:
        """Clear all controller state and return to centering."""
        self._lateral.reset()
        self._lateral_shaper.reset()
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._locked = False
        self._anchor = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False

    # ------------------------------------------------------------------ cycle

    def compute(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        relative_altitude_m: float,
        dt: float,
        *,
        speed_scale: float = 1.0,
        measured: bool = True,
        airframe_at_rest: bool = True,
    ) -> ServoCommand:
        """Compute one cycle of servoing from a single resolved observation.

        Parameters
        ----------
        target_center : Tuple[float, float]
            Target centroid in pixels.
        frame_dimensions : Tuple[int, int]
            ``(width, height)`` of the frame.
        current_tilt_deg : float
            Gimbal tilt currently commanded, in degrees, negative downward.
            Returned unchanged in the command.
        relative_altitude_m : float
            Height above the plane the target lies on, for the diagnostic
            ground projection.
        dt : float
            True elapsed interval, from ``LoopRate.tick``.
        speed_scale : float
            Accepted for call compatibility and ignored. It throttled the
            along-track demand of the forward approach; this law has no
            along-track demand to throttle.
        measured : bool
            True when ``target_center`` comes from a detection in this frame,
            False when it is the tracker's prediction. Only measured frames earn
            centring credit or confirm a reacquisition.
        airframe_at_rest : bool
            True when the horizontal ground speed is inside the stillness
            threshold. The freeze latches only on a cycle where it holds.

        Returns
        -------
        ServoCommand
            The command for this cycle; ``vx`` is always ``0.0``.

        Raises
        ------
        ValueError
            If the frame dimensions are not positive.
        """
        width, height = self._validated_frame(frame_dimensions)
        center_x, center_y = width / 2.0, height / 2.0
        err_x = float(target_center[0]) - center_x
        err_y = float(target_center[1]) - center_y
        pixel_error = math.hypot(err_x, err_y)

        # Resolved per cycle rather than read straight from configuration: the
        # gate has to scale with the frame it is applied to, and it has to be no
        # tighter than the centroid jitter of the detector feeding it.
        self._tolerance_px = max(
            self.vision_cfg.optical_center_tolerance_px,
            self.vision_cfg.optical_center_tolerance_ratio * width,
        )
        in_gate = abs(err_x) <= self._tolerance_px

        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            horizontal_fov_deg=self.vision_cfg.horizontal_fov_deg,
            vertical_fov_deg=self.vision_cfg.vertical_fov_deg,
        )
        projection = self._project(intrinsics, target_center, relative_altitude_m, current_tilt_deg)

        if self._phase is TrackingPhase.LOCKED:
            return self._locked_command(
                current_tilt_deg, pixel_error, projection, in_gate, "locked (lateral freeze)"
            )

        self._anchor = TrackAnchor(
            tilt_deg=current_tilt_deg,
            target_px=(float(target_center[0]), float(target_center[1])),
            frame_center_x=center_x,
            ground_range_m=projection.ground_range_m if projection else None,
        )

        # Driven on normalized pixel error so the gains tuned in flight carry
        # over unchanged. The demand is shaped onto the actuator grid inside
        # each phase rather than here, so that a phase which suppresses the
        # axis suppresses the *demand* and drains the shaper, instead of zeroing
        # an output whose displacement the shaper has already banked.
        vy_demand = self._lateral_command(err_x / center_x, dt)

        if self._phase is TrackingPhase.REACQUIRING:
            return self._reacquired(
                err_x, pixel_error, in_gate, current_tilt_deg, projection, dt, measured
            )
        return self._centering(
            err_x,
            pixel_error,
            in_gate,
            vy_demand,
            current_tilt_deg,
            projection,
            dt,
            measured=measured,
            airframe_at_rest=airframe_at_rest,
        )

    # ----------------------------------------------------------------- phases

    def _centering(
        self,
        err_x: float,
        pixel_error: float,
        in_gate: bool,
        vy_demand: float,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
        *,
        measured: bool,
        airframe_at_rest: bool,
    ) -> ServoCommand:
        """Null the horizontal error laterally and decide when to freeze.

        Credit accrues and decays rather than resetting on a single jittered
        frame, and is capped at the dwell so that a long in-gate wait for the
        airframe to come to rest cannot bank enough credit to survive a real
        excursion afterwards.
        """
        dwell = self._lock_dwell_cycles()
        if in_gate:
            if measured:
                self._centered_frames = min(dwell, self._centered_frames + 1)
        else:
            self._centered_frames = max(0, self._centered_frames - 1)

        if in_gate and measured and airframe_at_rest and self._centered_frames >= dwell:
            self._enter_lock(err_x, projection)
            return self._locked_command(
                current_tilt_deg, pixel_error, projection, in_gate, "locked (lateral freeze)"
            )

        vy = self._shape_lateral(vy_demand, dt)
        if in_gate and self._centered_frames >= dwell:
            note = "centred, waiting for the airframe to settle"
        else:
            note = f"centering ({self._centered_frames}/{dwell})"
        return ServoCommand(
            vx=0.0,
            vy=vy,
            tilt_deg=current_tilt_deg,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.CENTERING,
            laterally_centered=in_gate,
            locked=False,
            note=note,
            tolerance_px=self._tolerance_px,
            alignment=self._alignment_gain(err_x),
        )

    def _enter_lock(self, err_x: float, projection: Optional[GroundProjection]) -> None:
        """Latch the lateral freeze.

        The cross-track loop is silenced with its state, not just its output.
        Zeroing only the command would leave the sigma-delta shaper holding a
        banked displacement that it discharges as a pulse several cycles into a
        phase whose whole premise is that the drone has stopped.
        """
        self._phase = TrackingPhase.LOCKED
        self._locked = True
        self._lateral.reset()
        self._lateral_shaper.reset()
        logger.info(
            "Target centred laterally (err %+.0f px, gate %.0f px, range %s). "
            "Freezing: no further translation until the return leg.",
            err_x,
            self._tolerance_px,
            f"{projection.ground_range_m:.2f} m" if projection else "unavailable",
        )

    def _locked_command(
        self,
        current_tilt_deg: float,
        pixel_error: float,
        projection: Optional[GroundProjection],
        laterally_centered: bool,
        note: str,
    ) -> ServoCommand:
        """The frozen command: both horizontal axes at an exact zero."""
        return ServoCommand(
            vx=0.0,
            vy=0.0,
            tilt_deg=current_tilt_deg,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.LOCKED,
            laterally_centered=laterally_centered,
            locked=True,
            note=note,
            tolerance_px=self._tolerance_px,
            alignment=1.0 if laterally_centered else 0.0,
        )

    def _alignment_gain(self, err_x: float) -> float:
        """Diagnostic cross-track alignment for the annotated stream.

        The same linear taper the forward approach used to scale its demand
        with: 1.0 inside the tolerance, falling to ``min_alignment_gain`` at the
        corridor edge (``approach_corridor_ratio`` times the tolerance) and to
        zero beyond it. It scales nothing here; it only tells the operator how
        far the lateral loop still has to go.
        """
        tolerance = self._tolerance_px
        corridor = tolerance * self.vision_cfg.approach_corridor_ratio
        magnitude = abs(err_x)

        if magnitude <= tolerance:
            return 1.0
        if magnitude >= corridor:
            return 0.0
        span = max(corridor - tolerance, 1e-6)
        ramp = 1.0 - (magnitude - tolerance) / span
        floor = self.vision_cfg.min_alignment_gain
        return floor + (1.0 - floor) * ramp

    # ----------------------------------------------------------- reacquisition

    def _begin_reacquire(self, reason: str) -> None:
        """Enter reacquisition, remembering which side to creep toward."""
        if self._phase is TrackingPhase.REACQUIRING:
            return
        logger.warning(
            "Target recovery engaged (%s). Holding the tilt at %.1f deg; last error %+.0f px.",
            reason,
            self._anchor.tilt_deg if self._anchor else float("nan"),
            self._anchor.err_x_px if self._anchor else float("nan"),
        )
        self._phase = TrackingPhase.REACQUIRING
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._centered_frames = 0
        self._lateral.reset()
        self._lateral_shaper.reset()

    def note_target_lost(self, current_tilt_deg: float, dt: float) -> ServoCommand:
        """Produce one cycle of recovery when no target could be resolved.

        Without a measurement there is no bearing, so the lateral PID is not
        run. What remains is the anchor: the side the target was last seen on.
        A target that left the frame sideways did so because the drone
        translated past it, so the recovery is a bounded creep back the way it
        came -- ``vision.reacquire_reverse_speed`` for at most
        ``vision.reacquire_reverse_sec`` -- and then a hold until
        ``vision.reacquire_timeout_sec``. A target last seen inside the gate
        authorises no motion at all.

        Parameters
        ----------
        current_tilt_deg : float
            Gimbal tilt currently commanded, returned unchanged.
        dt : float
            True elapsed interval, seconds.

        Returns
        -------
        ServoCommand
            The recovery command; ``vx`` is always ``0.0``. Once the freeze has
            latched, the frozen command instead.
        """
        if self._phase is TrackingPhase.LOCKED:
            return self._locked_command(
                current_tilt_deg, float("nan"), None, False, "locked (target not in view)"
            )

        cfg = self.vision_cfg
        if self._phase is not TrackingPhase.REACQUIRING:
            self._begin_reacquire("target lost")

        self._reacquire_elapsed_sec += max(0.0, dt)
        self._reacquire_hits = 0

        exhausted = self._reacquire_elapsed_sec >= cfg.reacquire_timeout_sec
        if exhausted and not self._recovery_exhausted:
            self._recovery_exhausted = True
            logger.warning(
                "Target not recovered within %.1f s. Ending the lateral servo.",
                cfg.reacquire_timeout_sec,
            )

        last_err_x = self._anchor.err_x_px if self._anchor is not None else 0.0
        creeping = (
            not exhausted
            and self._reacquire_elapsed_sec <= cfg.reacquire_reverse_sec
            and abs(last_err_x) > self._tolerance_px
        )
        # A target that left through the right edge (err_x > 0) is to the
        # right of the airframe, which is -y in the FLU body frame.
        demand = -math.copysign(abs(cfg.reacquire_reverse_speed), last_err_x) if creeping else 0.0
        vy = self._shape_lateral(demand, dt)

        return ServoCommand(
            vx=0.0,
            vy=vy,
            tilt_deg=current_tilt_deg,
            pixel_error=float("nan"),
            ground_range_m=None,
            depression_deg=None,
            phase=TrackingPhase.REACQUIRING,
            laterally_centered=False,
            locked=False,
            note=(
                f"reacquiring ({self._reacquire_elapsed_sec:.1f}s"
                f"{', creeping ' + ('right' if demand < 0.0 else 'left') if creeping else ''})"
            ),
            tolerance_px=self._tolerance_px,
            alignment=0.0,
            recovery_exhausted=exhausted,
        )

    def _reacquired(
        self,
        err_x: float,
        pixel_error: float,
        in_gate: bool,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
        measured: bool,
    ) -> ServoCommand:
        """A detection arrived while reacquiring: confirm it, then resume centring.

        The lateral axis holds still until the sighting is confirmed: a single
        frame is what sent the tracker chasing phantoms before. The
        reacquisition window keeps running on unconfirmed frames, so a stream of
        predicted observations cannot postpone the exhaustion that
        :meth:`note_target_lost` would otherwise report.
        """
        cfg = self.vision_cfg
        self._reacquire_elapsed_sec += max(0.0, dt)
        if measured:
            self._reacquire_hits += 1
        vy = self._shape_lateral(0.0, dt)

        confirmed = self._reacquire_hits >= cfg.reacquire_confirm_frames
        exhausted = not confirmed and self._reacquire_elapsed_sec >= cfg.reacquire_timeout_sec
        if exhausted and not self._recovery_exhausted:
            self._recovery_exhausted = True
            logger.warning(
                "Target not confirmed within %.1f s of reacquisition. Ending the lateral servo.",
                cfg.reacquire_timeout_sec,
            )

        if confirmed:
            logger.info(
                "Target reacquired after %.1f s (err %+.0f px). Resuming centering.",
                self._reacquire_elapsed_sec,
                err_x,
            )
            self._phase = TrackingPhase.CENTERING
            self._reacquire_elapsed_sec = 0.0
            self._reacquire_hits = 0
            self._recovery_exhausted = False
            self._centered_frames = 0

        return ServoCommand(
            vx=0.0,
            vy=vy,
            tilt_deg=current_tilt_deg,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.REACQUIRING,
            laterally_centered=in_gate,
            locked=False,
            note=f"reacquired {self._reacquire_hits}/{cfg.reacquire_confirm_frames}",
            tolerance_px=self._tolerance_px,
            alignment=0.0,
            recovery_exhausted=exhausted,
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _validated_frame(frame_dimensions: Tuple[int, int]) -> Tuple[int, int]:
        """Return ``(width, height)`` after checking both are positive.

        Raises
        ------
        ValueError
            If either dimension is not positive.
        """
        width, height = frame_dimensions
        if width <= 0 or height <= 0:
            raise ValueError(f"frame_dimensions must be positive, got {frame_dimensions!r}")
        return width, height

    def _lock_dwell_cycles(self) -> int:
        """Measured in-gate frames required to latch, clamped to at least one.

        Read from configuration every cycle so the parameter sheet governs the
        freeze, and clamped so a zero in the configuration cannot latch the
        freeze on a frame that has not been measured inside the gate.
        """
        return max(1, int(self.vision_cfg.alignment_dwell_cycles))

    def _project(
        self,
        intrinsics: CameraIntrinsics,
        target_center: Tuple[float, float],
        altitude_m: float,
        tilt_deg: float,
    ) -> Optional[GroundProjection]:
        """Ground projection for diagnostics, or ``None`` when it cannot be trusted."""
        if not self.vision_cfg.ibvs_enabled:
            return None
        if altitude_m < self.vision_cfg.min_altitude_for_ibvs_m:
            if not self._fallback_warned:
                self._fallback_warned = True
                logger.warning(
                    "Relative altitude %.2f m is below the %.2f m needed to trust the ground "
                    "projection. Range and depression are not reported.",
                    altitude_m,
                    self.vision_cfg.min_altitude_for_ibvs_m,
                )
            return None
        return project_to_ground(intrinsics, target_center, altitude_m, tilt_deg)

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

    def _shape_lateral(self, demand: float, dt: float) -> float:
        """Render a cross-track demand onto the driver's quantized channel.

        ``LateralPIDConfig.min_effective_velocity`` was declared from the first
        revision and read by nothing, which is why the centering phase could not
        close its own loop: the driver quantizes to ``int8(v * 100)``, so the
        0.029 demanded by a 35 px offset on an 856 px frame arrived as two
        counts -- under the airframe's response threshold. The error therefore
        stopped decreasing while still outside the tolerance that gates the
        phase exit, and no amount of additional hovering would have changed it.

        Applied as a duty cycle rather than as a floor, for the reason set out
        in :mod:`mvp_mission_bebop.controllers.quantization`: a floor would
        forbid the axis from ever settling gently onto the centreline.
        """
        return self._lateral_shaper.shape(demand, dt)
