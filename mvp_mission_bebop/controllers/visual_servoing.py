"""Image-based visual servoing with geometrically coupled approach guidance.

Three phases run in sequence.

**Centering** aligns the target on the vertical optical axis using lateral
velocity alone. Forward motion is locked at zero so the drone cannot overfly a
target it has not yet pointed at.

That lock is the reason this phase has to be exited on terms it can actually
meet, and the first flight-test build did not. It gated the exit on the *radial*
pixel error -- both axes inside a tight box for three strictly consecutive
frames -- while commanding only the cross-track axis. The vertical half of that
gate is nulled by the gimbal, whose travel this phase deliberately restricts, so
whenever the pointing solution railed against the band the residual vertical
error became a constant with no actuator behind it. Forward motion is the only
thing that reduces it, by shortening the range and steepening the ray, and
forward motion is precisely what the gate withheld. The result was a fixed point
at ``vx = 0``: the drone hovered, the gimbal dithered against its stop, and the
stage ended sixty seconds later having never advanced.

Concretely, with the frame geometry this mission flies (856x480, 50 degree
vertical field of view, 52.5 px vertical tolerance) the gate admitted bearings
within 5.83 degrees of the optical axis. Railed at the band's shallow stop of
-15 degrees and hovering at 1.23 m, that is satisfiable only for ground ranges
between 2.06 m and 7.62 m -- and Stage 2 confirms a target as soon as three
frames clear the confidence threshold, which for a bicycle at this altitude is
routinely eight metres out or more.

So the exit is now gated on the axis this phase drives, with the vertical
condition reduced to what it always should have been: a guard that the target is
still comfortably inside the frame. Credit accrues and decays rather than
resetting on a single jittered frame, and two timeouts hand over to the approach
if the cross-track loop has not converged -- which is not a bypass, because the
approach phase re-tests the lateral corridor every cycle and holds the forward
axis at zero outside it.

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
    """Sub-phases of the visual approach."""

    #: Brief dwell confirming the detection is persistent before committing to
    #: forward flight. Cross-track centring runs here, but is not gated on.
    CENTERING = "centering"
    APPROACHING = "approaching"
    NADIR = "nadir"
    #: Target lost. Hold position, sweep the gimbal back up, and try to get it
    #: back into frame rather than abandoning an approach already under way.
    REACQUIRING = "reacquiring"


@dataclass(frozen=True)
class TrackAnchor:
    """The last observation the controller is willing to fly back toward.

    Recorded on every measured detection so that a loss has something concrete
    to recover *to*. Without it the only recovery available is a blind hold,
    which is how an approach that was seconds from the target used to end.
    """

    #: Gimbal tilt at which the target was last seen, degrees.
    tilt_deg: float
    #: Target centroid in pixels at that moment.
    target_px: Tuple[float, float]
    #: Phase to resume once the target is back.
    phase: "TrackingPhase"
    #: Ground range when geometry was available, metres.
    ground_range_m: Optional[float]
    #: Depression of the target below horizontal, degrees.
    depression_deg: Optional[float]


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
    #: Effective optical tolerance in force this cycle, in pixels. Reported so
    #: the annotated stream and the flight log show the gate the controller is
    #: actually applying rather than the nominal configured value.
    tolerance_px: float = 0.0
    #: Cross-track alignment in [0, 1] scaling the forward demand this cycle.
    alignment: float = 1.0
    #: True once reacquisition has exhausted its window and the step should stop.
    recovery_exhausted: bool = False
    #: True once the inspection attitude has been reached and the airframe has
    #: come to rest over the scene. Both halves matter: the step must not
    #: declare the approach finished while the drone is still translating, and
    #: the drone must not still be translating once the gimbal is down.
    nadir_frozen: bool = False
    #: True once the controller has committed to inspecting from here. Distinct
    #: from ``nadir_frozen``, which additionally requires the deceleration into
    #: the freeze to have finished; between the two the drone is stopping.
    nadir_committed: bool = False


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

        # The cross-track axis is the one the centering phase actually drives,
        # and it was the one the driver was throwing away. ``min_effective_velocity``
        # is rendered as a duty cycle rather than a floor, so the commanded mean
        # still tracks the demand continuously down to zero while every emitted
        # command survives the driver's int8 truncation.
        self._lateral_shaper = QuantizedCommandShaper(
            min_effective=lateral_pid_cfg.min_effective_velocity,
            quantization_step=lateral_pid_cfg.quantization_step,
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
        #: Time spent in the current occupancy of the acquisition phase.
        self._centering_elapsed_sec = 0.0
        #: Last measured observation, the anchor reacquisition recovers toward.
        self._anchor: Optional[TrackAnchor] = None
        #: Time spent in the current occupancy of the reacquisition phase.
        self._reacquire_elapsed_sec = 0.0
        #: Consecutive re-detections accumulated while reacquiring.
        self._reacquire_hits = 0
        #: Set once reacquisition has run out of window.
        self._recovery_exhausted = False
        #: One-shot latch for the off-corridor diagnostic.
        self._decentered_warned = False
        #: Effective tolerances, refreshed from the frame size each cycle.
        self._tolerance_px = vision_cfg.optical_center_tolerance_px
        self._vertical_guard_px = float("inf")
        #: Latched once the inspection attitude has been reached. The freeze is
        #: a commitment, not a state the controller drifts in and out of: the
        #: mission's next act is to photograph the scene from exactly here.
        self._nadir_committed = False
        #: Relative altitude reported this cycle, in metres. Carried as state so
        #: the approach can size its braking against the standoff the gimbal
        #: angle implies, which is a function of altitude.
        self._altitude_m = 0.0
        #: Horizontal speed scale requested by the altitude loop this cycle.
        self._speed_scale = 1.0

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
        self._lateral_shaper.reset()
        self._phase = TrackingPhase.CENTERING
        self._centered_frames = 0
        self._decentered_frames = 0
        self._nadir_excursion_frames = 0
        self._transitioned_to_approach = False
        self._centering_elapsed_sec = 0.0
        self._anchor = None
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        self._recovery_exhausted = False
        self._nadir_committed = False
        self._speed_scale = 1.0

    # ------------------------------------------------------------------ cycle

    @property
    def nadir_committed(self) -> bool:
        """True once the inspection attitude has been reached and latched."""
        return self._nadir_committed

    def compute(
        self,
        target_center: Tuple[float, float],
        frame_dimensions: Tuple[int, int],
        current_tilt_deg: float,
        relative_altitude_m: float,
        dt: float,
        *,
        speed_scale: float = 1.0,
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
        speed_scale : float
            Fraction of the along-track demand the altitude loop permits this
            cycle, from
            :meth:`~mvp_mission_bebop.controllers.altitude_hold.AltitudeHoldGovernor.horizontal_scale`.
            Applied to the *demand*, before profiling, so that translating less
            hard is a change of intent the jerk limiter can shape rather than a
            clamp bolted onto an already-shaped command.
        """
        self._altitude_m = relative_altitude_m
        self._speed_scale = max(0.0, min(1.0, speed_scale))
        width, height = frame_dimensions
        center_x, center_y = width / 2.0, height / 2.0
        err_x = float(target_center[0]) - center_x
        err_y = float(target_center[1]) - center_y
        pixel_error = math.hypot(err_x, err_y)
        norm_err_x = err_x / center_x

        # Resolved per cycle rather than read straight from configuration: the
        # gate has to scale with the frame it is applied to, and it has to be no
        # tighter than the centroid jitter of the detector feeding it.
        self._tolerance_px = max(
            self.vision_cfg.optical_center_tolerance_px,
            self.vision_cfg.optical_center_tolerance_ratio * width,
        )
        self._vertical_guard_px = self.vision_cfg.centering_vertical_guard_ratio * center_y

        intrinsics = CameraIntrinsics(
            width=width,
            height=height,
            horizontal_fov_deg=self.vision_cfg.horizontal_fov_deg,
            vertical_fov_deg=self.vision_cfg.vertical_fov_deg,
        )
        projection = self._project(intrinsics, target_center, relative_altitude_m, current_tilt_deg)

        # Cross-track control is the same in every phase: hold the target on the
        # vertical centreline. Driven on normalized pixel error so the gains
        # tuned in flight carry over unchanged. The demand is shaped onto the
        # actuator grid inside each phase rather than here, so that a phase
        # which suppresses the axis (nadir, inside its deadband) suppresses the
        # *demand* and drains the shaper, instead of zeroing an output whose
        # displacement the shaper has already banked and will later discharge.
        vy_demand = self._lateral_command(norm_err_x, dt)

        # A live observation is also the thing reacquisition needs in order to
        # have somewhere to return to, so the anchor is refreshed before the
        # phase machine runs and is therefore always the most recent sighting.
        self._anchor = TrackAnchor(
            tilt_deg=current_tilt_deg,
            target_px=(float(target_center[0]), float(target_center[1])),
            phase=(
                self._resume_phase()
                if self._phase is TrackingPhase.REACQUIRING
                else self._phase
            ),
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
        )

        if self._phase is TrackingPhase.REACQUIRING:
            return self._reacquired(
                err_x, err_y, pixel_error, vy_demand, current_tilt_deg, projection, dt
            )

        if self._phase is TrackingPhase.CENTERING:
            return self._centering(
                err_x, err_y, pixel_error, vy_demand, current_tilt_deg, projection, dt
            )
        if self._phase is TrackingPhase.APPROACHING:
            return self._approaching(
                err_x, err_y, pixel_error, vy_demand, current_tilt_deg, projection, dt
            )
        return self._nadir(err_x, err_y, pixel_error, vy_demand, current_tilt_deg, projection, dt)

    # ----------------------------------------------------------------- phases

    def _centering(
        self,
        err_x: float,
        err_y: float,
        pixel_error: float,
        vy_demand: float,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
    ) -> ServoCommand:
        """Brief acquisition dwell before the approach engages.

        This phase used to decide *whether* the drone had centred well enough to
        move. It no longer decides anything about pixel error, and that is the
        substance of the change: every version that gated on centring was a hover
        trap, because the phase commands no forward velocity and forward velocity
        is what closes the range the vertical error depends on. The drone was
        asked to null an error it had just forbidden itself the means to null.

        What remains is what the dwell is actually for: letting the Stage 2
        deceleration finish, and confirming across a few frames that the
        detection is persistent rather than a single-frame false positive.
        Cross-track centring runs here exactly as it runs everywhere else, and
        continues without interruption into the approach.
        """
        vision_cfg = self.vision_cfg
        vy = self._shape_lateral(vy_demand, dt)
        self._centering_elapsed_sec += max(0.0, dt)

        # The gimbal tracks the target within a bounded band. The band is
        # asymmetric because its two stops are not equivalent: looking further
        # down keeps the ground projection well conditioned, looking further up
        # walks it toward the horizon where range sensitivity is h/sin^2(d).
        tilt = self._slew(
            current_tilt_deg,
            self._pointing_tilt(current_tilt_deg, err_y, projection, dt),
            dt,
            lower=self.gimbal_cfg.search_tilt_deg - self.gimbal_cfg.centering_tilt_band_down_deg,
            upper=self.gimbal_cfg.search_tilt_deg + self.gimbal_cfg.centering_tilt_band_up_deg,
        )

        in_frame = abs(err_y) <= self._vertical_guard_px
        if in_frame:
            self._centered_frames += 1
        else:
            self._centered_frames = max(0, self._centered_frames - 1)

        dwell_done = (
            self._centering_elapsed_sec >= vision_cfg.acquisition_dwell_sec
            and self._centered_frames >= vision_cfg.confirmation_frames
        )
        if dwell_done:
            self._enter_approach(
                f"acquisition confirmed after {self._centering_elapsed_sec:.1f} s "
                f"(err {err_x:+.0f}, {err_y:+.0f} px)"
            )
        elif self._centering_elapsed_sec >= vision_cfg.acquisition_timeout_sec:
            # Not a fault. The approach is corridor-guarded and the cross-track
            # loop runs throughout, so committing forward on a persistent
            # detection is safe even when the dwell counter is short.
            self._enter_approach(
                f"acquisition window elapsed at {self._centering_elapsed_sec:.1f} s "
                f"(err {err_x:+.0f}, {err_y:+.0f} px)",
                level=logging.WARNING,
            )

        return ServoCommand(
            vx=0.0,
            vy=vy,
            tilt_deg=tilt,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.CENTERING,
            nadir_aligned=False,
            note=f"acquiring ({self._centering_elapsed_sec:.1f}s)",
            tolerance_px=self._tolerance_px,
            alignment=self._alignment_gain(err_x),
        )

    def _enter_approach(self, reason: str, *, level: int = logging.INFO) -> None:
        """Hand the airframe over to the approach phase."""
        logger.log(level, "Acquisition -> approaching: %s.", reason)
        self._phase = TrackingPhase.APPROACHING
        self._transitioned_to_approach = True
        self._centered_frames = 0
        self._decentered_frames = 0
        self._centering_elapsed_sec = 0.0

    def _alignment_gain(self, err_x: float) -> float:
        """How much of the forward demand the cross-track error permits.

        A continuous taper across the corridor rather than a switch at its edge.
        The approach used to be all-or-nothing: full speed inside the corridor,
        hard zero outside it. That is jerky at the boundary and, worse, it made
        forward progress a function of a threshold test rather than of how well
        aimed the drone actually was -- so a target drifting slowly off-axis
        produced full speed right up to the instant it produced none.

        Inside the tolerance the gain is 1. From there it falls linearly to the
        floor at the corridor edge, and to zero beyond it, where closing on a
        bearing the drone is not aimed at stops being an approach and starts
        being a guess.
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

    def _pitch_alignment_gain(self, err_y: float) -> float:
        """Throttle forward demand when the gimbal lags behind the target downward.

        A target above or near the center line (err_y <= tolerance) does not restrict
        advance. When the target slips toward the bottom of the frame (err_y > tolerance),
        the drone is closing faster than the camera can tilt down. Ramping the speed
        down prevents overrunning the target before reaching nadir.
        """
        tolerance = self._tolerance_px
        guard = self._vertical_guard_px
        if err_y <= tolerance:
            return 1.0
        if err_y >= guard:
            return 0.0
        span = max(guard - tolerance, 1e-6)
        ramp = 1.0 - (err_y - tolerance) / span
        floor = self.vision_cfg.min_alignment_gain
        return max(0.0, floor + (1.0 - floor) * ramp)

    def _approaching(
        self,
        err_x: float,
        err_y: float,
        pixel_error: float,
        vy_demand: float,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
    ) -> ServoCommand:
        """Advance along-track while centring, gimbal tracking the target to nadir.

        The three things this stage has to do happen *simultaneously*, which is
        the whole point of the manoeuvre and is what the previous phase machine
        prevented: the cross-track PID holds the target on the centreline, the
        forward channel closes the range under a braking profile, and the gimbal
        pitches from the search attitude down toward nadir by pointing at the
        target's measured bearing. None of the three waits on the others.

        Cross-track and pitch lag modulate forward speed continuously instead of
        gating it. There is no path back to a zero-velocity phase: the approach either
        makes progress, or -- if the cross-track loop is demonstrably not winning
        -- hands over to reacquisition, which widens the field of view. Falling
        back to a phase that also commands zero forward velocity was a livelock
        dressed as a safety feature.
        """
        vision_cfg = self.vision_cfg
        corridor = self._tolerance_px * vision_cfg.approach_corridor_ratio
        alignment = self._alignment_gain(err_x)
        pitch_alignment = self._pitch_alignment_gain(err_y)
        vy = self._shape_lateral(vy_demand, dt)

        tilt = self._slew(
            current_tilt_deg,
            self._pointing_tilt(current_tilt_deg, err_y, projection, dt),
            dt,
            lower=self.gimbal_cfg.nadir_tilt_deg,
            upper=self.gimbal_cfg.search_tilt_deg,
        )

        demand_mps, note = self._approach_speed(projection, tilt)
        target_mps = demand_mps * alignment * pitch_alignment
        if self._speed_scale < 1.0:
            note = f"{note}, altitude throttle {self._speed_scale:.2f}"

        if alignment <= 0.0:
            # Outside the corridor entirely. The cross-track loop keeps working;
            # only the forward axis is withheld. There is deliberately no phase
            # change here: the target is visible, so nothing about the
            # observation needs recovering, and every previous design that
            # escalated an off-axis target into a second zero-velocity phase
            # produced a livelock between the two. The lateral loop is the thing
            # that fixes a lateral error, and it is already running.
            self._decentered_frames += 1
            note = f"outside corridor ({err_x:+.0f} px > {corridor:.0f} px)"
            if (
                self._decentered_frames >= vision_cfg.decentered_frames_to_revert
                and not self._decentered_warned
            ):
                self._decentered_warned = True
                logger.warning(
                    "Target has held %+.0f px off-axis for %d frames, outside the %.0f px "
                    "corridor. Forward motion stays withheld while the cross-track loop "
                    "closes the bearing; the stage deadline bounds the wait.",
                    err_x,
                    self._decentered_frames,
                    corridor,
                )
        else:
            self._decentered_frames = 0
            self._decentered_warned = False
            if alignment < 1.0:
                note = f"{note}, alignment {alignment:.2f}"

        profiled = self._forward_profile.step(target_mps * self._speed_scale, dt)
        vx = max(0.0, self.calibration.to_normalized(profiled))

        if self._phase is TrackingPhase.APPROACHING and self._reached_nadir(tilt, alignment):
            self._enter_nadir(tilt, projection)
            # Hand straight to the nadir law rather than emitting a bespoke
            # command here. The freeze has to be *the same code* that runs for
            # the rest of the phase, or the first cycle of it is a special case
            # nothing tests -- and the first cycle is the one where the airframe
            # is still carrying the whole approach velocity.
            return self._nadir(
                err_x, err_y, pixel_error, vy_demand, current_tilt_deg, projection, dt
            )

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
            tolerance_px=self._tolerance_px,
            alignment=alignment,
        )

    def _enter_nadir(
        self, tilt_deg: float, projection: Optional[GroundProjection]
    ) -> None:
        """Commit to inspecting from here.

        Latched, and the latch is the behaviour the mission asks for: once the
        camera is at the inspection attitude the airframe freezes at the spot it
        reached, photographs the scene, and returns. There is nothing left for
        translation to achieve -- the gimbal is already pointing at the target,
        from a standoff chosen so that it is -- and flying further would only
        push the subject out from under an optical axis that can no longer
        follow it down.
        """
        self._phase = TrackingPhase.NADIR
        self._nadir_committed = True
        self._nadir_excursion_frames = 0
        # The cross-track loop is silenced with its state, not just its output.
        # Zeroing only the command would leave the sigma-delta shaper holding a
        # banked displacement that it discharges as a pulse several cycles into
        # a phase whose whole premise is that the drone has stopped.
        self._lateral.reset()
        self._lateral_shaper.reset()
        logger.info(
            "Inspection attitude reached at %.1f deg (range %s). Freezing over the scene: "
            "no further translation until the return leg.",
            tilt_deg,
            f"{projection.ground_range_m:.2f} m" if projection else "unavailable",
        )

    def _nadir(
        self,
        err_x: float,
        err_y: float,
        pixel_error: float,
        vy_demand: float,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
    ) -> ServoCommand:
        """Hold the inspection attitude with the airframe frozen beneath it.

        This phase used to fine-position over the target on both horizontal
        axes. It no longer translates at all once the attitude has been
        committed to: the mission's requirement is that reaching the inspection
        tilt ends the manoeuvre, and a last few centimetres of cross-track trim
        is worth less than a photograph taken from an airframe that is provably
        at rest -- Stage 4 verifies motionlessness statistically before it
        triggers, and every command sent here is something that verification has
        to wait out.

        Coming to rest is still a *manoeuvre*, though, and the distinction is not
        pedantic. The approach arrives carrying real velocity; a step to zero is
        a pitch transient that swings the camera at the exact moment the mission
        needs it steady, which is the same defect the Stage 2 halt was written to
        avoid. So the along-track axis is commanded to zero *through the profile*
        and reaches it in a few tenths of a second, and ``nadir_frozen`` is
        reported only once it is actually there.
        """
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

        # Decelerate to rest rather than stepping to it, then stay there.
        profiled = self._forward_profile.step(0.0, dt)
        vx = max(0.0, self.calibration.to_normalized(profiled))
        if vx < self.lateral_pid_cfg.quantization_step:
            # Below one actuator count the command is an exact zero on the wire
            # anyway; saying so here is what lets the freeze be reported.
            self._forward_profile.reset()
            vx = 0.0

        if self._nadir_committed:
            # Frozen: the cross-track demand is discarded, not merely clamped,
            # so the shaper has nothing to accumulate and nothing to discharge.
            vy = 0.0
        else:
            deadband_px = self._tolerance_px * vision_cfg.nadir_deadband_ratio
            if abs(err_x) < deadband_px:
                vy_demand = 0.0
            vy = self._shape_lateral(vy_demand, dt)

        aligned = pixel_error <= (self._tolerance_px * vision_cfg.nadir_alignment_ratio)
        frozen = self._nadir_committed and vx == 0.0 and vy == 0.0

        if aligned:
            self._nadir_excursion_frames = 0
        elif not self._nadir_committed:
            # The excursion exit exists for a nadir hold that is still *tracking*
            # -- one entered without the commitment above, which is the only way
            # a target can drift out of the window with the drone still able to
            # do anything about it. A committed freeze does not revert: reverting
            # would restart an approach the mission has already finished, and
            # send the airframe translating again at the moment Stage 4 is about
            # to ask it to hold perfectly still.
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
            note=(
                "frozen for inspection"
                if frozen
                else ("settling to rest" if self._nadir_committed else "nadir")
            ),
            tolerance_px=self._tolerance_px,
            nadir_frozen=frozen,
            nadir_committed=self._nadir_committed,
        )

    # ----------------------------------------------------------- reacquisition

    def _resume_phase(self) -> "TrackingPhase":
        """Phase to return to once the target is back in frame."""
        if self._anchor is not None and self._anchor.phase is not TrackingPhase.REACQUIRING:
            return self._anchor.phase
        return TrackingPhase.APPROACHING

    def _begin_reacquire(self, reason: str) -> None:
        """Enter reacquisition, remembering where to come back to."""
        if self._phase is TrackingPhase.REACQUIRING:
            return
        logger.warning(
            "Target recovery engaged (%s). Holding position, sweeping the gimbal up from "
            "%.1f deg, resuming as %s.",
            reason,
            self._anchor.tilt_deg if self._anchor else float("nan"),
            self._resume_phase().value,
        )
        self._phase = TrackingPhase.REACQUIRING
        self._reacquire_elapsed_sec = 0.0
        self._reacquire_hits = 0
        # The forward profile must not carry its momentum into a phase whose
        # whole purpose is to stop translating and look around.
        self._forward_profile.reset()
        self._lateral.reset()
        self._lateral_shaper.reset()

    def note_target_lost(self, current_tilt_deg: float, dt: float) -> ServoCommand:
        """Produce one cycle of recovery when no target could be resolved.

        Losing the target near nadir is the expected case rather than an
        anomaly. At -80 degrees the ground footprint is a few tens of
        centimetres across, the object is foreshortened into an aspect ratio the
        detector never trained on, and the airframe's own shadow falls on it.
        The previous behaviour -- end the approach -- threw away a target the
        drone was directly on top of, at the exact moment it had almost arrived.

        Recovery is deliberately conservative about what it assumes. Without a
        measurement there is no bearing, so the cross-track axis is silent
        rather than guessing. What it does have is the anchor: the tilt, pixel
        position and phase of the last real sighting. From that it sweeps the
        gimbal back *up*, because a shallower ray lands the footprint further
        ahead and covers far more ground per degree of pitch, and it creeps
        backward a little, because a target directly beneath the airframe is
        outside the frustum at any tilt the gimbal can reach.
        """
        cfg = self.vision_cfg
        if self._phase is not TrackingPhase.REACQUIRING:
            self._begin_reacquire("target lost")

        self._reacquire_elapsed_sec += max(0.0, dt)
        self._reacquire_hits = 0

        anchor_tilt = self._anchor.tilt_deg if self._anchor else current_tilt_deg
        # Sweep up toward the search attitude, never past it: above that the
        # camera is looking at the horizon, where nothing on the ground is.
        desired = min(self.gimbal_cfg.search_tilt_deg, anchor_tilt + cfg.reacquire_sweep_deg)
        tilt = self._slew(
            current_tilt_deg,
            desired,
            dt,
            lower=self.gimbal_cfg.nadir_tilt_deg,
            upper=self.gimbal_cfg.search_tilt_deg,
        )

        reversing = self._reacquire_elapsed_sec <= cfg.reacquire_reverse_sec
        vx = -abs(cfg.reacquire_reverse_speed) if reversing else 0.0

        exhausted = self._reacquire_elapsed_sec >= cfg.reacquire_timeout_sec
        if exhausted and not self._recovery_exhausted:
            self._recovery_exhausted = True
            logger.warning(
                "Target not recovered within %.1f s. Ending the approach.",
                cfg.reacquire_timeout_sec,
            )

        return ServoCommand(
            vx=0.0 if exhausted else vx,
            vy=0.0,
            tilt_deg=tilt,
            pixel_error=float("nan"),
            ground_range_m=None,
            depression_deg=None,
            phase=TrackingPhase.REACQUIRING,
            nadir_aligned=False,
            note=(
                f"reacquiring ({self._reacquire_elapsed_sec:.1f}s, tilt {tilt:.1f} deg"
                f"{', reversing' if reversing and not exhausted else ''})"
            ),
            tolerance_px=self._tolerance_px,
            alignment=0.0,
            recovery_exhausted=exhausted,
        )

    def _reacquired(
        self,
        err_x: float,
        err_y: float,
        pixel_error: float,
        vy_demand: float,
        current_tilt_deg: float,
        projection: Optional[GroundProjection],
        dt: float,
    ) -> ServoCommand:
        """A detection arrived while reacquiring: confirm it, then resume."""
        cfg = self.vision_cfg
        self._reacquire_elapsed_sec += max(0.0, dt)
        self._reacquire_hits += 1

        # Hold the gimbal where it found the target rather than continuing the
        # sweep past it, but stay still until the sighting is confirmed: a
        # single frame is what sent the tracker chasing phantoms before.
        tilt = self._slew(
            current_tilt_deg,
            self._pointing_tilt(current_tilt_deg, err_y, projection, dt),
            dt,
            lower=self.gimbal_cfg.nadir_tilt_deg,
            upper=self.gimbal_cfg.search_tilt_deg,
        )

        if self._reacquire_hits >= cfg.reacquire_confirm_frames:
            resumed = self._resume_phase()
            logger.info(
                "Target reacquired after %.1f s at tilt %.1f deg. Resuming %s.",
                self._reacquire_elapsed_sec,
                tilt,
                resumed.value,
            )
            self._phase = resumed
            self._reacquire_elapsed_sec = 0.0
            self._reacquire_hits = 0
            self._recovery_exhausted = False
            self._decentered_frames = 0
            self._nadir_excursion_frames = 0

        return ServoCommand(
            vx=0.0,
            vy=0.0,
            tilt_deg=tilt,
            pixel_error=pixel_error,
            ground_range_m=projection.ground_range_m if projection else None,
            depression_deg=projection.depression_deg if projection else None,
            phase=TrackingPhase.REACQUIRING,
            nadir_aligned=False,
            note=f"reacquired {self._reacquire_hits}/{cfg.reacquire_confirm_frames}",
            tolerance_px=self._tolerance_px,
            alignment=0.0,
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

    def inspection_standoff_m(self, altitude_m: Optional[float] = None) -> float:
        """Ground range at which the gimbal reaches the inspection attitude.

        Pure geometry: the optical axis at ``nadir_tilt_deg`` meets the ground
        ``h / tan(|tilt|)`` ahead of the airframe, so that is where the target
        must be for the gimbal -- which points at the target -- to have arrived
        at that angle. At -69 deg and 1.55 m it is 0.60 m.

        This is what the approach brakes against. The previous arrival tolerance
        was ``nadir_range_threshold_m``, which is 0.25 m and describes a drone
        parked almost on top of the subject; aiming at it meant the braking
        profile was still asking for most of the approach cap at the moment the
        gimbal hit its stop, so the freeze had to shed real velocity. Aiming at
        the standoff instead makes the two events -- arriving and stopping --
        the same event, which is what "freeze at the location" actually requires
        of the guidance rather than of the clamp downstream of it.
        """
        altitude = self._altitude_m if altitude_m is None else altitude_m
        depression = abs(self.gimbal_cfg.nadir_tilt_deg)
        if altitude <= 0.0 or not 0.0 < depression < 90.0:
            return self.vision_cfg.nadir_range_threshold_m
        return altitude / math.tan(math.radians(depression))

    def _approach_speed(
        self, projection: Optional[GroundProjection], tilt_deg: float
    ) -> Tuple[float, str]:
        """Along-track speed demand for the approach phase."""
        cap = self.calibration.to_mps(self.kinematics_cfg.max_approach_forward_speed)

        if projection is None:
            # Open-loop fallback: speed decays with how far the gimbal ramp has
            # progressed toward nadir, which is what the previous version did.
            # The span is read from the configured angles, so shortening the
            # sweep from 60 to 49 degrees rescales the ramp automatically
            # instead of leaving it calibrated against an attitude the gimbal no
            # longer reaches.
            span = self.gimbal_cfg.search_tilt_deg - self.gimbal_cfg.nadir_tilt_deg
            progress = 1.0 if span <= 0.0 else (tilt_deg - self.gimbal_cfg.nadir_tilt_deg) / span
            return cap * max(0.20, min(1.0, progress)), "open-loop ramp (no geometry)"

        # Closed-loop: brake against the measured ground range so the drone
        # arrives at the inspection standoff at rest rather than at a speed
        # dictated by how many frames the gimbal ramp has consumed.
        arrival = max(self.vision_cfg.nadir_range_threshold_m, self.inspection_standoff_m())
        speed = braking_velocity(
            projection.ground_range_m,
            self.kinematics_cfg.max_approach_decel_mps2,
            cruise_velocity=cap,
            arrival_tolerance=arrival,
        )
        return speed, f"range {projection.ground_range_m:.2f} m (stop at {arrival:.2f} m)"

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

    def _reached_nadir(self, tilt_deg: float, alignment: float) -> bool:
        """Has the camera reached the inspection attitude, aimed at the scene?

        The pitch axis decides the *when*. Previously the range from the ground
        projection had to agree with it, and with the attitude moved from -80 to
        -69 deg those two conditions no longer describe the same moment: the
        gimbal arrives at -69 with the subject 0.60 m ahead at the operating
        altitude, and the 0.25 m range gate would have kept the airframe flying
        for another third of a metre past the point where the camera could still
        follow the subject down. The gimbal angle *is* the measurement of
        arrival -- the camera points at the target, so the angle it has reached
        is the target's depression -- and the approach brakes against the
        standoff that angle implies, so arriving and stopping are one event.

        The cross-track corridor still has a veto, and it is not a formality.
        The gimbal has one axis. A target well off to the side drives the pitch
        solution down exactly as a centred one does, because the pointing law
        reads the vertical bearing and nothing else -- so without this the drone
        would freeze at the inspection attitude while aimed at something beside
        its track, and photograph the tarmac next to the accident. Being inside
        the corridor is the only evidence available that the thing the camera
        has pitched down onto is the thing in front of the airframe.
        """
        at_attitude = (
            tilt_deg
            <= self.gimbal_cfg.nadir_tilt_deg + self.gimbal_cfg.nadir_tilt_tolerance_deg
        )
        return at_attitude and alignment > 0.0
