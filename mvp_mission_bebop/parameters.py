"""Centralized mission configuration and tunable parameters.

All operational parameters that govern flight behavior, vision processing,
PID controllers, network topics, and safety limits are declared here.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("MissionParameters")


@dataclass
class NetworkConfig:
    """Network connection endpoints and ROS 2 topic names."""

    drone_ip: str = "192.168.42.1"
    namespace: str = "bebop"
    camera_raw_topic: str = "/bebop/camera/image_raw"
    detection_stream_topic: str = "/bebop/camera/detections"
    odometry_topic: str = "/bebop/odom"


@dataclass
class GimbalConstraintsConfig:
    """Mechanical and operational angular limits for the camera gimbal (degrees)."""

    search_tilt_deg: float = -20.0
    #: Inspection attitude, in degrees. The gimbal is never asked to go below
    #: this, and reaching it is what ends the approach.
    #:
    #: Previously -80. The airframe does not need to stare straight down to
    #: photograph a scene, and driving the last eleven degrees was expensive in
    #: a way the imagery never repaid: the ground footprint collapses, the
    #: object is foreshortened into an aspect ratio the detector never trained
    #: on, the drone's own shadow falls across it, and the manoeuvre required
    #: flying the airframe to within a couple of tens of centimetres of the
    #: subject. At -69 deg the optical axis meets the ground one altitude-over-
    #: tan(69) ahead -- 0.60 m at the 1.55 m operating height -- which frames the
    #: scene from a standoff the drone can hold without overflying it.
    nadir_tilt_deg: float = -69.0
    #: How close to :attr:`nadir_tilt_deg` the commanded tilt must come before
    #: the inspection attitude counts as reached, in degrees.
    #:
    #: Was a bare ``2.5`` written into three separate call sites. It is a gate on
    #: the stage transition, so it belongs with the angle it is a tolerance on.
    #: Tightened along with the angle itself: the whole point of stopping at -69
    #: rather than -80 is that the standoff is chosen deliberately, and a wide
    #: tolerance gives that choice away again -- 2.5 deg at 1.55 m is 8 cm of
    #: ground range.
    nadir_tilt_tolerance_deg: float = 1.0
    #: Maximum gimbal angular rate, in degrees per second. Previously declared
    #: and never read; it now rate-limits every tilt command so the pitch axis
    #: cannot step discontinuously between control cycles.
    max_slew_limit_deg: float = 45.0
    #: How aggressively the gimbal chases the target's measured bearing. 1.0
    #: points the optical axis straight at the target each cycle; lower values
    #: lag deliberately, trading tracking bandwidth for steadiness.
    tracking_gain: float = 1.0
    #: How far above the search attitude the gimbal may look while centering.
    #: Kept small: a shallower ray meets the ground at a range whose sensitivity
    #: to the angle is h / sin^2(depression), so range estimates degrade sharply
    #: toward the horizon and there is nothing to gain by staring at it.
    centering_tilt_band_up_deg: float = 5.0
    #: How far below the search attitude the gimbal may look while centering.
    #: Wider than the upward margin because the downward direction is where the
    #: projection is well conditioned. The previous symmetric +/-5 deg band
    #: railed the pointing solution against its lower stop for any target closer
    #: than h / tan(search_tilt + 5 deg) -- about 2.1 m at a 1.2 m altitude --
    #: and a railed gimbal cannot null the vertical pixel error.
    centering_tilt_band_down_deg: float = 15.0


@dataclass
class VisionConfig:
    """Computer vision model, inference parameters, and optical tolerances."""

    model_path: str = "yolov8n.pt"
    confidence_threshold: float = 0.50
    target_classes: List[str] = field(default_factory=lambda: ["motorcycle", "bicycle"])
    confirmation_frames: int = 3
    #: Frames of sustained loss required before confirmation is withdrawn.
    #: Separate from ``confirmation_frames`` so acquisition and loss can have
    #: independent hysteresis instead of one counter that zeroes on any miss.
    release_frames: int = 2
    optical_center_tolerance_px: float = 35.0
    #: Optical tolerance as a fraction of frame width. The effective tolerance
    #: is the larger of this and ``optical_center_tolerance_px``, so the gate
    #: scales with resolution instead of being a magic number tied to one
    #: capture size. 35 px on the Bebop's 856 px frame is 4.1% of the width,
    #: which is inside the frame-to-frame centroid jitter of a YOLOv8n
    #: detection on a small distant object; the gate was therefore tighter than
    #: the measurement it gates on.
    optical_center_tolerance_ratio: float = 0.05
    approach_centering_tolerance_px: float = 30.0
    #: Vertical centering tolerance as a multiple of the horizontal one.
    #:
    #: Retained for the approach and nadir phases. It is deliberately *not* a
    #: convergence gate during centering: see ``centering_vertical_guard_ratio``.
    centering_vertical_ratio: float = 1.5
    #: Vertical in-frame guard during centering, as a fraction of the frame
    #: half-height.
    #:
    #: Centering commands cross-track velocity only; the vertical pixel error is
    #: nulled by the gimbal, whose travel is deliberately restricted in this
    #: phase. Gating the phase exit on an axis the phase cannot authoritatively
    #: control is what deadlocked Stage 3 in flight: with the gimbal railed
    #: against its band the residual vertical error is a constant the controller
    #: has no means to reduce, and forward motion -- the only thing that *would*
    #: reduce it, by shortening the range -- is exactly what the gate withholds.
    #: The vertical precondition for advancing is therefore that the target is
    #: still comfortably inside the frame, not that it is centred in it.
    centering_vertical_guard_ratio: float = 0.72
    #: Acquisition dwell before the approach engages, in seconds.
    #:
    #: Deliberately short, and deliberately *not* gated on pixel error. Its only
    #: job is to let the airframe finish the Stage 2 deceleration and to confirm
    #: the detection is persistent before committing to forward flight. Every
    #: version of this phase that gated on centring produced a hover trap,
    #: because the phase commands no forward velocity and forward velocity is
    #: what closes the range that the vertical error depends on. Cross-track
    #: centring is now continuous across every phase instead of being a gate on
    #: one of them.
    acquisition_dwell_sec: float = 1.0
    #: Hard ceiling on the acquisition dwell. Reaching it is not a fault.
    acquisition_timeout_sec: float = 3.0
    #: Lateral corridor half-width during approach, as a multiple of the
    #: horizontal tolerance. Forward speed tapers to zero across this band
    #: rather than switching off at its edge -- see ``alignment_gain``.
    approach_corridor_ratio: float = 1.6
    #: Floor on the cross-track alignment gain while the target is inside the
    #: corridor but not yet centred.
    #:
    #: The approach used to gate forward motion on a boolean: full speed inside
    #: the corridor, hard zero outside it. That is both jerky at the boundary and
    #: unnecessary, because being 50 px off-axis at 6 m is a bearing error of
    #: three degrees, not a reason to stop. Forward speed is now scaled
    #: continuously by how well aligned the drone is, so it centres *while*
    #: advancing, which is what the manoeuvre actually calls for. This floor
    #: keeps a partially aligned approach moving instead of creeping.
    min_alignment_gain: float = 0.25
    #: Per-axis deadband at nadir, as a multiple of the horizontal tolerance.
    nadir_deadband_ratio: float = 0.50
    #: Radial alignment threshold at nadir, as a multiple of the tolerance.
    nadir_alignment_ratio: float = 1.5
    #: Consecutive out-of-corridor frames before the approach concludes that the
    #: cross-track loop is not winning and hands over to reacquisition, which
    #: widens the field of view rather than continuing to creep at a target the
    #: drone is not aimed at.
    decentered_frames_to_revert: int = 10
    #: Consecutive out-of-window frames before leaving the nadir phase.
    nadir_exit_frames: int = 8
    #: Consecutive lost frames tolerated before entering recovery.
    lost_frames_tolerance: int = 3
    #: Consecutive aligned cycles required before the approach is declared
    #: finished. Raising it buys confidence at the cost of hover time; lowering
    #: it commits to the nadir transition sooner.
    alignment_dwell_cycles: int = 4
    #: Multiplier applied to `optical_center_tolerance_px` when deciding the
    #: airframe is over the target and the gimbal is at nadir. The arrival gate
    #: is deliberately looser than the tracking gate: at nadir the bounding box
    #: centroid walks with the airframe's own attitude jitter, and holding it to
    #: the tracking tolerance is what kept the aircraft station-keeping over a
    #: target it had already reached.
    nadir_arrival_tolerance_factor: float = 2.0
    #: How long reacquisition may run before the approach is abandoned.
    #:
    #: Losing the target near nadir is the *expected* case, not an anomaly: at
    #: -80 deg the ground footprint is a few tens of centimetres across, the
    #: object is foreshortened to an aspect ratio the detector never trained on,
    #: and the drone's own shadow lands on it. Treating that as the end of the
    #: approach threw away a target the drone was directly on top of.
    reacquire_timeout_sec: float = 8.0
    #: How far above the last known good tilt reacquisition sweeps the gimbal.
    #:
    #: Pitching *up* is the recovery, not down: a shallower ray lands the
    #: footprint further ahead and covers far more ground per degree, so it
    #: re-frames a target that fell out of the near field. Sweeping down would
    #: only stare harder at the patch that already failed.
    reacquire_sweep_deg: float = 30.0
    #: Bounded reverse creep during reacquisition, in normalized units.
    #:
    #: A target lost directly beneath the airframe cannot be recovered by
    #: pitching up alone, because the gimbal's own travel limit stops short of
    #: straight down plus the field of view. Backing off a few centimetres puts
    #: it back inside the frustum.
    reacquire_reverse_speed: float = 0.05
    #: How long the reverse creep may run before reacquisition holds station.
    reacquire_reverse_sec: float = 2.0
    #: Consecutive re-detections required to leave reacquisition.
    reacquire_confirm_frames: int = 2
    #: Camera field of view, used by the IBVS pinhole geometry to convert
    #: pixel error into a bearing. Bebop 2 front camera, digitally stabilized.
    horizontal_fov_deg: float = 80.0
    vertical_fov_deg: float = 50.0
    #: Use the geometric IBVS law. Setting this false reverts the approach to
    #: the previous open-loop gimbal ramp without a code change -- the escape
    #: hatch for a field session where the altitude estimate proves unusable.
    ibvs_enabled: bool = True
    #: Relative altitude below which the ground projection is not trusted and
    #: the controller falls back to the open-loop ramp.
    min_altitude_for_ibvs_m: float = 0.35
    #: Ground range at which the approach is considered to be over the target.
    nadir_range_threshold_m: float = 0.25
    #: Per-cycle gimbal step used by the open-loop fallback, in degrees.
    legacy_gimbal_ramp_deg: float = 2.0


@dataclass
class GimbalPIDConfig:
    """High-bandwidth PID gains for vertical optical centering."""

    kp: float = 18.0
    ki: float = 0.0
    kd: float = 1.2
    output_limits: Tuple[float, float] = (-18.0, 18.0)


@dataclass
class LateralPIDConfig:
    """PID gains for horizontal centering via body-frame lateral velocity."""

    kp: float = 0.35
    ki: float = 0.0
    kd: float = 0.03
    output_limits: Tuple[float, float] = (-0.22, 0.22)
    deadband: float = 0.005
    #: Smallest cross-track command the airframe actually answers.
    #:
    #: Declared since the first revision and, until now, never read by anything.
    #: The consequence is structural rather than cosmetic: the driver quantizes
    #: to ``int8(v * 100)``, so with ``kp = 0.35`` on normalized pixel error a
    #: 35 px offset on an 856 px frame demands 0.029 -- two driver counts, below
    #: the airframe's response threshold. The lateral loop therefore asymptoted
    #: into the actuator dead zone instead of reaching the centering tolerance.
    #: It is applied through :class:`QuantizedCommandShaper` as a duty cycle, not
    #: as a hard floor, so the commanded mean still tracks the demand down to
    #: zero.
    min_effective_velocity: float = 0.06
    #: Driver command quantization step, in normalized units.
    quantization_step: float = 0.01


@dataclass
class AltitudeGovernorConfig:
    """Altitude-hold parameters governing the vertical axis in level flight.

    Historically this configured a one-way *anti-climb* governor: it could ask
    for descent and nothing else, because the mission's vertical invariant
    forbade commanding ascent anywhere outside the Stage 1 climb. That was the
    right shape for the failure it was written against -- the ultrasonic
    rangefinder mistaking an obstacle for lost height and the firmware climbing
    to "correct" it -- and the wrong shape for the one observed in flight
    afterwards.

    Translation costs the Bebop lift. Pitching into a forward command tilts the
    thrust vector, the firmware does not fully make up the vertical component,
    and the airframe sinks for as long as the translation lasts. Against a
    descent-only governor the sink is invisible: the altitude error has the sign
    the governor cannot act on, so it commands ``vz = 0.0`` and the drone goes on
    sinking. A mission that held 1.55 m rock-steady in hover arrived at the
    accident scene most of a metre low.

    The governor is therefore two-sided now, bounded far more tightly upward
    than downward, and the ascent authority is granted by the failsafe as a
    window rather than taken by the controller -- see
    :meth:`~mvp_mission_bebop.telemetry.failsafe.FailsafeSupervisor.altitude_hold_window`.
    """

    #: Altitude *above* target tolerated before descent is commanded, in metres.
    deadband_m: float = 0.03
    kp: float = 0.80
    kd: float = 0.03
    max_descent_speed: float = 0.08

    # ------------------------------------------------------------- hold mode

    #: Master switch. With this false the mission constructs the historical
    #: descent-only :class:`AltitudeAntiClimbGovernor` instead, which is the
    #: escape hatch for a field session where the two-sided law misbehaves.
    hold_enabled: bool = True
    #: Altitude *below* target tolerated before climb is commanded, in metres.
    #:
    #: Deliberately wider than ``deadband_m``. The two directions are not
    #: symmetric in consequence: drifting up walks toward the safety ceiling,
    #: drifting down a few centimetres does not, and a tight band on the climb
    #: side buys a limit cycle against sonar noise for nothing.
    climb_deadband_m: float = 0.05
    #: Ceiling on corrective ascent, in normalized command units.
    #:
    #: Bounded well under the descent cap's counterpart in Stage 1
    #: (``max_climb_speed_mps``) because this is a trim, not a manoeuvre: it
    #: cancels a sink rate of a few centimetres per second, and anything larger
    #: would be a climb the mission never asked for.
    max_climb_speed: float = 0.10
    #: Integral gain. The sink this law exists to reject is a *sustained*
    #: disturbance for as long as the translation lasts, and proportional action
    #: alone leaves a standing offset against it -- the drone would stabilize
    #: low rather than on target. The integral is what actually pins it.
    ki: float = 0.25
    #: Anti-windup bound on the integral term, in normalized units.
    integral_limit: float = 0.05
    #: Time constant with which the integral bleeds away while the altitude is
    #: inside the deadband, in seconds. Long enough that a translation-induced
    #: sink does not have to be re-learned on every brief excursion, short
    #: enough that a bank accumulated against one disturbance cannot drive an
    #: overshoot once that disturbance is gone.
    integral_leak_sec: float = 4.0
    #: Cutoff of the derivative filter, in hertz. The Bebop's altitude estimate
    #: carries 5-8 cm of noise; differentiating it unfiltered at 15 Hz produces
    #: more phantom rate than the signal being corrected.
    derivative_cutoff_hz: float = 2.0
    #: Acceleration and jerk ceilings applied to the vertical command itself.
    #: An altitude loop that steps its output excites exactly the oscillation
    #: the requirement rules out.
    max_accel_mps2: float = 0.40
    max_jerk_mps3: float = 2.00

    # -------------------------------------------- horizontal speed throttling

    #: Altitude error at which horizontal flight is throttled all the way down
    #: to ``min_horizontal_scale``, in metres.
    #:
    #: Translation is the disturbance. When the vertical loop is losing, the
    #: cheapest correction available is to translate less hard -- and unlike a
    #: blanket speed reduction it costs nothing on the phases where altitude is
    #: holding fine.
    horizontal_throttle_error_m: float = 0.12
    #: Floor on that throttle. Never zero: a stage that stops translating
    #: outright cannot finish, and the altitude error would then have to be
    #: resolved by the vertical axis alone anyway.
    min_horizontal_scale: float = 0.35

    @property
    def climb_authority(self) -> float:
        """Ascent ceiling this configuration authorizes, in normalized units.

        Zero whenever hold mode is off, which is what makes the failsafe's
        altitude-hold window a no-op without a second flag to check.
        """
        return max(0.0, self.max_climb_speed) if self.hold_enabled else 0.0


@dataclass
class FlightKinematicsConfig:
    """Translational velocity caps and geometric safety envelopes."""

    target_altitude_m: float = 1.00
    altitude_ceiling_margin_m: float = 0.25
    forward_cruise_velocity: float = 0.20
    max_approach_forward_speed: float = 0.15
    takeoff_stabilize_duration_sec: float = 4.0
    hover_duration_sec: float = 7.0
    countdown_sec: float = 0.0
    #: Metres per second produced by a unit normalized velocity command.
    #:
    #: ``BebopDrone.move_velocity`` publishes a Twist normalized to [-1, 1]
    #: (nectar/control/bebop/drone.py:216-231), while /bebop/odom reports
    #: metres. Guidance laws work in m/s and convert at the actuator boundary
    #: using this constant. The default of 1.0 is *not* calibrated: it makes
    #: the two domains numerically identical, reproducing the historical
    #: behaviour. Calibrate in the field and set it here.
    normalized_to_mps: float = 1.0
    #: Per-axis refinements of that gain. ``None`` means "same as the
    #: longitudinal one", which is the honest default: nothing in the flight log
    #: separates them yet. They exist because the Bebop's roll and pitch
    #: authority are not the same number, and a dead-reckoning estimate that
    #: assumes they are accumulates a cross-track bias over a mission.
    lateral_normalized_to_mps: Optional[float] = None
    vertical_normalized_to_mps: Optional[float] = None
    #: Normalized command magnitude below which the airframe does not move.
    #:
    #: Not a tuning choice: the C++ driver quantizes each component to
    #: ``int8(v * 100)``, so anything under 0.01 is transmitted as an exact zero.
    #: Dead reckoning has to model that or it credits the drone with travel it
    #: never made -- and the sigma-delta shaper deliberately emits long runs of
    #: sub-threshold demand, so the error is systematic rather than incidental.
    command_deadzone_normalized: float = 0.01
    #: Fraction of a commanded interval during which the airframe is actually
    #: travelling at the commanded speed.
    #:
    #: A ``move_velocity`` held for ten seconds does not displace the drone by
    #: ten times its steady-state speed: it spends the first fraction of a second
    #: accelerating into it and, when the command changes, some of the next one
    #: shedding it. 1.0 assumes it does, which is the uncalibrated placeholder
    #: and reproduces the naive conversion exactly. Measure it in flight -- fly a
    #: known command for a known duration, divide measured displacement by
    #: ``speed * duration`` -- and set it here.
    translation_efficiency: float = 1.0
    #: Translational acceleration ceiling for jerk-limited velocity profiles.
    max_accel_mps2: float = 0.30
    #: Jerk ceiling. Bounds the rate of change of acceleration, which is what
    #: actually stops the airframe from pitching sharply on stop.
    max_jerk_mps3: float = 1.20
    #: Deceleration the visual approach plans its arrival against, in m/s^2.
    #:
    #: Deliberately far gentler than ``max_accel_mps2``, and the two are not in
    #: conflict: that one is an *envelope*, the fastest the airframe may change
    #: speed, while this is an *intention*, how briskly the approach chooses to
    #: shed its cruise. Planning the arrival against the envelope means the
    #: feedforward holds full approach speed until ``v^2 / 2a`` -- under six
    #: centimetres -- from the stopping point and then demands the whole
    #: deceleration at once. That is survivable, and it is the wrong thing to ask
    #: for at the end of this particular manoeuvre: the stage finishes by
    #: freezing over an accident scene and handing a camera to a stage that
    #: verifies motionlessness statistically before it triggers, so arriving
    #: already slow is worth more than arriving quickly and stopping hard. At
    #: 0.08 the approach begins easing roughly 0.14 m out and settles onto the
    #: standoff instead of braking onto it.
    max_approach_decel_mps2: float = 0.08
    #: Peak commanded ascent speed for the Stage 1 climb, in m/s.
    #:
    #: Deliberately gentler than it could be. A climb is a discretionary
    #: manoeuvre bounded by a ceiling only 0.25 m above the target, so the
    #: profile is sized to stop inside that margin rather than to arrive fast.
    max_climb_speed_mps: float = 0.25
    #: Altitude error inside which the Stage 1 climb counts as arrived, in
    #: metres. Wider than the governor's 0.03 m deadband so the climb hands over
    #: to the governor already inside the band the governor tolerates, instead
    #: of finishing on the edge of it and provoking an immediate correction.
    climb_deadband_m: float = 0.05
    #: Hard bound on the Stage 1 climb, in seconds. Reaching it is not a fault:
    #: the mission continues at the altitude actually achieved. Kept short
    #: because the stall detector (in _ascend) provides an earlier exit when
    #: the firmware rejects the commanded vz.
    climb_timeout_sec: float = 15.0
    #: Multivariate settling gate for the climb. The drone must hold inside the
    #: deadband, with low vertical speed and low dispersion, continuously --
    #: a single sample crossing the target proves nothing about convergence.
    #: Tuned for the Bebop sonar/barometer noise floor (~0.05-0.08 m).
    climb_settle_window_sec: float = 1.00
    climb_settle_min_samples: int = 5
    climb_settle_max_speed_mps: float = 0.10
    climb_settle_max_position_sigma_m: float = 0.08
    #: Hard envelope on any horizontal velocity component reaching the driver,
    #: in normalized units.
    #:
    #: Deliberately above every legitimate demand in the mission -- cruise 0.20,
    #: approach 0.15, cross-track 0.22, RTL 0.10 -- so it never shapes normal
    #: guidance, and far below the driver's full scale of 1.0 so a defect in any
    #: guidance law cannot command a full-throttle translation. Until now no
    #: bound of any kind stood between a guidance law and the wire: the failsafe
    #: saturated ``vz`` and ``vyaw`` and passed ``vx``/``vy`` through untouched.
    max_horizontal_speed: float = 0.30
    #: Target cadence for closed-loop mission control loops.
    control_loop_hz: float = 15.0


@dataclass
class ReturnToLaunchConfig:
    """Return-to-Launch navigation, ArUco terminal guidance, and landing.

    Two return laws are configured here and the split is deliberate.

    The **ArUco** fields at the bottom drive the flown path: a reverse cruise
    that searches for the marker on the launch pad, a closed-loop centering over
    it, and a precision touchdown. Every quantity that law consumes is an
    absolute observation of the ground.

    The fields above them configure the **legacy closed-loop odometry return**,
    which is retained as the degraded path for the case where no marker detector
    can be constructed at all -- missing intrinsic calibration, an unsupported
    dictionary, a workspace without the vision SDK. It is open-loop with respect
    to the ground and cannot land to marker precision, but it is airworthy and
    it is better than having no return leg.
    """

    max_speed: float = 0.10
    #: Longitudinal PD trim applied on top of the feedforward braking profile.
    #: Both gains were declared and never read by the previous implementation,
    #: which had no control law at all -- only a piecewise ramp in displacement.
    kp: float = 0.15
    kd: float = 0.01
    lateral_kp: float = 0.18
    lateral_kd: float = 0.02
    max_lateral_speed: float = 0.05
    braking_distance_m: float = 0.45
    deadband_m: float = 0.03
    #: Smallest command the driver does not truncate to zero. The Bebop C++
    #: driver quantizes to int8(v * 100), so anything under 0.01 normalized
    #: becomes a no-op. Applied by duty-cycling, never as a hard floor.
    min_effective_speed: float = 0.035
    #: Driver command quantization step, in normalized units.
    quantization_step: float = 0.01
    settle_cycles: int = 3
    arrival_radius_m: float = 0.20
    #: Bounded forward authority for nulling a terminal overshoot, normalized.
    #:
    #: RTL flies strictly backward, and that rule used to be absolute: when the
    #: origin ended up ahead of the nose the along-track axis was simply held at
    #: zero. A braking profile plus odometry lag makes overshoot close to
    #: certain, so the drone would arrive a little past the origin, freeze that
    #: axis, and hover there -- distance never changing, arrival never
    #: confirming -- until the whole return window expired. Observed in flight as
    #: "returns to the takeoff point and never lands".
    #:
    #: The invariant exists to keep the return a straight reverse line with no
    #: yaw, which protects optical-flow fidelity. A centimetre-scale forward trim
    #: in the terminal regime does not threaten that; hovering off-target until a
    #: timeout does. Bounded well below the cruise cap, and only applied inside
    #: ``overshoot_recovery_radius_m``.
    overshoot_recovery_speed: float = 0.04
    #: Range within which forward overshoot recovery is permitted, in metres.
    #: Beyond it the origin being ahead means something worse than overshoot has
    #: happened, and creeping forward is not the answer.
    overshoot_recovery_radius_m: float = 1.50
    #: Dwell inside the arrival radius at low speed that commits to landing.
    #:
    #: Separate from, and far easier to satisfy than, the full settlement
    #: verdict. Settlement additionally requires positional sigma under 0.06 m
    #: sustained across a window, which real Bebop optical-flow odometry at
    #: hover does not reliably deliver -- so arrival could fail to confirm even
    #: with the drone sitting over the origin. Landing is not a manoeuvre that
    #: needs millimetric confirmation: inside the radius, moving slowly, for
    #: this long, is enough to put it on the ground.
    landing_commit_dwell_sec: float = 0.80
    timeout_sec: float = 60.0
    final_hover_delay_sec: float = 2.0
    #: Navigate the return leg against the aggregated motion sequence rather
    #: than against ``/bebop/odom``.
    #:
    #: The two answer the same question from opposite directions. Odometry says
    #: where the optical-flow estimator believes the drone is; dead reckoning
    #: says where the drone must be if it obeyed the commands it was given. Over
    #: a mission spent staring at featureless tarmac from 1.55 m, at the tilt
    #: angles the approach flies, the optical-flow estimate is the one that
    #: drifts -- and it drifts silently, because nothing on this airframe
    #: cross-checks it. The command history does not drift; it is only ever as
    #: wrong as the speed calibration behind it, which is a measurable quantity.
    use_dead_reckoning: bool = True
    #: Disagreement between the two estimates, in metres, above which the flight
    #: log says so. Not a fault: the point is to give the operator the number
    #: that tells them which one to trust next time.
    dead_reckoning_disagreement_warn_m: float = 1.00
    #: Acceleration and jerk ceilings for the RTL velocity profile, in m/s.
    max_accel_mps2: float = 0.25
    max_jerk_mps3: float = 1.00
    #: Multivariate settlement window. Arrival requires every sample inside the
    #: arrival radius, low positional variance, and low mean speed, sustained
    #: across the window -- not N consecutive loop iterations.
    settle_window_sec: float = 1.20
    settle_min_samples: int = 6
    settle_max_speed_mps: float = 0.05
    settle_max_position_sigma_m: float = 0.06
    #: Terminal touchdown verification.
    touchdown_timeout_sec: float = 12.0
    touchdown_altitude_m: float = 0.15
    land_burst_count: int = 3
    #: How long the descent may stall before assisted descent is commanded.
    #:
    #: ``land()`` publishes an ``Empty`` and returns; nothing acknowledges it and
    #: nothing verifies the firmware acted. If the altitude has not moved after
    #: this long, the landing request is not being honoured and re-sending it
    #: more times will not change that -- so an explicit bounded descent is
    #: commanded instead, and the land request re-asserted afterwards.
    descent_stall_sec: float = 3.0
    #: Altitude change below which the descent counts as stalled, in metres.
    descent_stall_epsilon_m: float = 0.04
    #: Commanded descent rate for the assisted fallback, in normalized units.
    #: Bounded by the governor's own descent cap at the actuator boundary.
    assisted_descent_speed: float = 0.06
    #: How long the altitude must stop making progress before touchdown is
    #: inferred, in seconds.
    #:
    #: ``relative_altitude`` is measured against a ground reference calibrated
    #: before launch; if that datum drifted during the flight the absolute
    #: threshold may never be crossed even with the airframe on the ground.
    #: Descent stagnation under a commanded landing is the platform-independent
    #: touchdown signature, and it is the fallback used here.
    #:
    #: Expressed in seconds rather than in loop cycles, and that distinction is
    #: not cosmetic. A cycle count makes the test depend on how fast the loop
    #: happens to run: the progress marker only moves once the drone has
    #: descended a whole epsilon, so a descent slow enough to take more cycles
    #: than the threshold to cover that epsilon reads as stagnant while it is
    #: still descending -- and reports touchdown in mid-air. Against a time
    #: window the comparison is between two physical quantities and the loop
    #: rate cannot enter into it.
    touchdown_stagnation_sec: float = 1.50

    # ------------------------------------------------------------------ ArUco
    #
    # Stage 5 no longer returns to a *coordinate*; it returns to a *landmark*.
    #
    # The dead-reckoned and odometric estimates of the launch origin are both
    # open-loop with respect to the ground: one integrates commands, the other
    # integrates optical flow, and neither of them can tell the mission that it
    # is actually above the pad. Over a mission spent translating at low
    # altitude over featureless tarmac the accumulated error is metres, and a
    # landing is a manoeuvre whose acceptable error is centimetres. The ArUco
    # marker that the drone took off from closes that loop: it is an absolute,
    # metric, drift-free observation of the base, and the fields below are what
    # it takes to find it and settle over it.
    #
    # The legacy fields above are retained and still flown -- they are the
    # degraded path taken when the marker detector cannot be constructed at all
    # (no intrinsic calibration on disk, an unsupported dictionary, no SDK).

    #: Identity of the marker that physically marks the launch/landing pad.
    #:
    #: Checked strictly. A marker from the same dictionary carrying any other ID
    #: is rejected outright rather than treated as a weak observation: a
    #: mis-identified pad is a landing at the wrong place, and there is no
    #: subsequent stage to catch it.
    target_aruco_id: int = 8
    #: ArUco/AprilTag dictionary identifier, passed to
    #: ``nectar.vision.Aruco``.  Accepts legacy integer orders (4, 5, 6, 7 for
    #: ``DICT_NxN_1000``), OpenCV enum integers (e.g. ``20`` for
    #: ``DICT_APRILTAG_36h11``), or string names (e.g. ``"DICT_APRILTAG_36h11"``,
    #: ``"tag36h11"``).
    marker_dict: Union[int, str] = "DICT_APRILTAG_36h11"
    #: Physical edge length of the printed marker, in metres.
    #:
    #: This is the scale factor of the entire pose estimate: ``solvePnP`` recovers
    #: translation in units of the object model it is given, so a tag declared
    #: 0.20 m and printed 0.15 m reports every distance 33% too large and the
    #: centering law converges onto a point 33% off. Measure the printed
    #: marker's black border, edge to edge.
    tag_size: float = 0.20
    #: Gimbal depression for the return leg, in degrees (negative is down).
    #:
    #: Near-nadir, and deliberately not fully nadir. At exactly -90 the marker's
    #: image position is a pure function of horizontal offset, which is ideal for
    #: centering and useless for *finding*: the footprint ahead of the airframe
    #: is zero, so a reverse cruise would only ever see the pad at the instant it
    #: was already over it, with no range in which to brake. Ten degrees of
    #: forward lean puts the optical axis on the ground roughly
    #: ``altitude / tan(80 deg)`` ahead -- 0.18 m at 1.0 m AGL -- and, more to the
    #: point, keeps the whole forward half of the frame looking at ground the
    #: drone has not yet flown over.
    camera_tilt_deg: float = -80.0
    #: Reverse cruise command for the marker search, normalized.
    #:
    #: Signed, and the sign is load-bearing: the search leg is the mirror of
    #: Stage 2, flown backward along the track the mission came out on, and the
    #: along-track command is saturated non-positive at the actuator boundary so
    #: a sign error in configuration cannot turn the return into an outbound
    #: cruise. Slower than the Stage 2 cruise because the payoff is detection
    #: probability per metre travelled, not metres travelled.
    reverse_cruise_velocity: float = -0.10
    #: Longitudinal (body-x) centering gains, m/s per metre of error and per
    #: metre-per-second of error rate.
    centering_kp_x: float = 0.25
    centering_kd_x: float = 0.02
    #: Lateral (body-y) centering gains. Higher than the longitudinal pair: the
    #: lateral error is recovered from the camera's x axis, which is unaffected
    #: by the tilt projection and therefore the better-conditioned of the two
    #: channels, so it tolerates more gain before it chases noise.
    centering_kp_y: float = 0.30
    centering_kd_y: float = 0.03
    #: Speed ceiling while centering, normalized, on the *resultant* horizontal
    #: velocity rather than on either axis alone. Well below the cruise cap:
    #: this phase is a convergence, not a transit, and every metre per second of
    #: overshoot has to be paid back over the marker.
    max_centering_speed: float = 0.08
    #: Radial error inside which the airframe counts as centred, in metres.
    centering_tolerance_m: float = 0.04
    #: Consecutive cycles inside :attr:`centering_tolerance_m` *at residual
    #: speed* required before the landing is authorized. A single sample inside
    #: the tolerance is satisfied by a drone flying through the centre at speed.
    centering_settle_cycles: int = 4
    #: Consecutive frames carrying the target ID required before the reverse
    #: cruise brakes. Guards against a single-frame false positive committing the
    #: mission to a landing site.
    confirmation_frames: int = 2
    #: Frames the marker may be absent before centering treats it as lost.
    #:
    #: Detection over a moving airframe drops frames -- motion blur, the drone's
    #: own shadow, a glint -- and zeroing the loop on the first miss would make
    #: the phase a sequence of restarts. Within the tolerance the law holds
    #: station and keeps its PD state; beyond it the settle counter is cleared,
    #: because a settlement claim built on stale observations is exactly the
    #: claim that puts the aircraft down somewhere it was not looking.
    lost_frames_tolerance: int = 5
    #: Window for the centering phase alone, in seconds.
    #:
    #: Separate from :attr:`timeout_sec`, which bounds the search. Sharing one
    #: window would let a long search consume the budget for the convergence it
    #: exists to enable, and the failure mode of that is an aircraft landing
    #: half-centred because the clock ran out on a phase that was converging.
    centering_timeout_sec: float = 25.0


@dataclass
class CalibrationConfig:
    """Robust statistics governing ground reference (z0) calibration."""

    #: Minimum odometry samples required before a calibration is accepted.
    min_ground_samples: int = 12
    #: Outlier rejection threshold, in robust sigmas (MAD * 1.4826) from the
    #: median. Replaces the previous plain arithmetic mean, which let a single
    #: spurious altitude sample bias the whole ground reference.
    mad_outlier_sigma: float = 3.0
    #: Maximum tolerated dispersion of the altitude buffer. Above this the
    #: surface is not flat enough (or the sensor is not settled) to trust.
    max_ground_dispersion_m: float = 0.15
    #: Ring buffer depth for pre-takeoff samples.
    sample_buffer_size: int = 50


@dataclass
class DeadReckoningConfig:
    """Aggregation of executed ``move_velocity`` commands into displacement.

    The Bebop offers no position feedback the mission can trust end to end, but
    it does offer something the mission owns outright: the exact sequence of
    velocity commands it transmitted, and how long each one was held. Integrated
    through a calibrated command-to-speed map that is a dead-reckoned track from
    the launch origin, independent of the optical flow estimator and of whether
    ``/bebop/odom`` is publishing at all.
    """

    enabled: bool = True
    #: Ceiling on a single integration step, in seconds. A mission thread
    #: descheduled behind a blocking frame grab must not credit the drone with
    #: several seconds of travel at the last commanded speed.
    max_integration_step_sec: float = 0.50
    #: Yaw rate, in rad/s, produced by a unit normalized yaw command.
    #:
    #: Nominal, and deliberately so: the mission pins ``vyaw`` at exactly zero as
    #: a hard invariant, precisely because rotation is what would make this
    #: integration depend on a number nobody has measured. It is here so that a
    #: violation of that invariant shows up in the track rather than being
    #: silently ignored.
    yaw_rate_per_unit_rad_s: float = 1.75
    #: Retain the per-command segment log for the post-flight summary.
    record_segments: bool = True
    #: Ring-buffer depth for that log. At 15 Hz a segment per cycle fills this
    #: in about four and a half minutes, which is longer than the mission.
    max_segments: int = 4000


@dataclass
class InspectionConfig:
    """Stochastic motionlessness verification before evidence capture."""

    settle_window_sec: float = 1.00
    settle_min_samples: int = 5
    settle_max_speed_mps: float = 0.03
    settle_max_position_sigma_m: float = 0.04
    #: Give up waiting for stillness and capture anyway after this long.
    settle_timeout_sec: float = 6.0
    #: Write a forensic metadata sidecar alongside the image pair.
    write_metadata_sidecar: bool = True
    #: Detection confidence used for the nadir pass, independent of the cruise
    #: threshold.
    #:
    #: Evidence capture wants recall, not precision: from directly overhead the
    #: target is foreshortened and partially self-occluded, so the detector
    #: scores it well below what it scored on the oblique approach. This used to
    #: be `min(0.30, vision.confidence_threshold)` written into the step, which
    #: silently discarded any operator setting above 0.30 and made that slider
    #: a no-op for the only inference the forensic record depends on.
    nadir_confidence_threshold: float = 0.30


@dataclass
class TimeoutsConfig:
    """Execution timeouts and sensor watchdog windows."""

    search_timeout_sec: float = 30.0
    #: Stage 3 window, sized from the standoff the approach has to close.
    #:
    #: Not a round number chosen for comfort: at the 0.15 approach cap, 60 s
    #: bought 9.0 m of travel, and closing the 8 m the flight test actually
    #: handed over consumed 53 s of it. Any target confirmed past roughly 8.5 m
    #: was therefore unreachable inside the window regardless of how well the
    #: control law performed -- and Stage 2 confirms as soon as three frames
    #: clear the confidence threshold, which for a bicycle at 1.2 m AGL is
    #: routinely eight metres out and can be half as far again. The stage would
    #: have ended in a timeout indistinguishable from the centering deadlock it
    #: was just fixed for. 110 s covers 16.5 m of approach plus the centering
    #: and nadir settling either side of it.
    tracking_timeout_sec: float = 110.0
    target_recovery_timeout_sec: float = 4.0
    odometry_heartbeat_timeout_sec: float = 3.0
    video_stream_timeout_sec: float = 8.0


@dataclass
class MissionParameters:
    """Root container consolidating all configurable mission subsystems."""

    network: NetworkConfig = field(default_factory=NetworkConfig)
    gimbal: GimbalConstraintsConfig = field(default_factory=GimbalConstraintsConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    gimbal_pid: GimbalPIDConfig = field(default_factory=GimbalPIDConfig)
    lateral_pid: LateralPIDConfig = field(default_factory=LateralPIDConfig)
    governor: AltitudeGovernorConfig = field(default_factory=AltitudeGovernorConfig)
    kinematics: FlightKinematicsConfig = field(default_factory=FlightKinematicsConfig)
    rtl: ReturnToLaunchConfig = field(default_factory=ReturnToLaunchConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    dead_reckoning: DeadReckoningConfig = field(default_factory=DeadReckoningConfig)
    inspection: InspectionConfig = field(default_factory=InspectionConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    no_fly: bool = False
    output_dir: str = "."

    def to_dict(self) -> Dict[str, Any]:
        """Convert parameter dataclasses to a plain nested dictionary.

        The Electron GCS invokes this through ``python3 -c`` to recover the
        defaults when ``mission_config.json`` is unreadable
        (``electron/main.cjs:602-604``), so the method name and the nested key
        layout are part of the external contract.
        """
        return dataclasses.asdict(self)

    def update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update this instance hierarchically from a nested dictionary.

        Unknown keys are ignored, which is what lets new configuration fields be
        introduced without invalidating an on-disk config written by an older
        build, or a ``--params-json`` payload sent by an older GCS.
        """

        def _apply(target: Any, values: Dict[str, Any]) -> None:
            for key, value in values.items():
                if not hasattr(target, key):
                    continue
                current = getattr(target, key)
                if isinstance(value, dict) and dataclasses.is_dataclass(current):
                    _apply(current, value)
                else:
                    setattr(target, key, value)

        _apply(self, data)

    def save_to_file(self, file_path: str) -> None:
        """Save parameters as JSON to disk, atomically.

        Written to a sibling temporary and renamed into place. The GCS reads
        this file to populate the parameter sheet and may do so at any moment,
        including while a mission is writing it back; a torn read there sends
        the operator to the Python defaults without saying so.
        """
        directory = os.path.dirname(os.path.abspath(file_path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=directory, prefix=".mission_config.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self.to_dict(), stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, file_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    @classmethod
    def load_from_file(cls, file_path: str) -> MissionParameters:
        """Load parameters from JSON on disk, falling back to defaults if missing or invalid."""
        params = cls()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                params.update_from_dict(data)
            except Exception as exc:  # noqa: BLE001 - a bad config must not block flight
                logger.warning("Failed to load %s: %s. Using defaults.", file_path, exc)
        return params
