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
from typing import Any, Dict, Final, List, Optional, Tuple, Union

logger = logging.getLogger("MissionParameters")

#: Fields a document may only set with a JSON boolean, as ``(section, name)``.
#: A truthy string (``"false"``) or ``0``/``1`` would otherwise be stored and
#: read with the opposite or an unintended meaning.
STRICT_BOOL_FIELDS: Final[Tuple[Tuple[str, str], ...]] = (
    ("rtl", "enabled"),
    ("platform", "takeoff_from_ground"),
    ("vision", "victim_detection_enabled"),
)

#: Mission mode removed from the station and the mission; stored documents that
#: still name it are migrated by :func:`migrate_legacy_mission_mode`.
LEGACY_INTERMEDIATE_MODE: Final[str] = "intermediate"

_legacy_mode_warned = False


class ParameterTypeError(TypeError):
    """A document field of the wrong type, refused before anything flies.

    Parameters
    ----------
    field : str
        Dotted path of the field (``"rtl.enabled"``).
    reason : str
        Short explanation in Portuguese, fit to be shown to the operator.

    Attributes
    ----------
    field : str
    reason : str
    """

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def migrate_legacy_mission_mode(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Carry a document of the removed Intermediate profile over to its equivalent.

    The Intermediate profile was exactly the Advanced one with no return leg.
    A document whose ``mission_mode`` reads ``"intermediate"`` (after trimming
    and lower-casing) becomes ``"advanced"`` with ``rtl.enabled = False``; an
    ``rtl.enabled`` the document states explicitly is kept. The station applies
    the same rule (``launchDocument.ts:migrateLegacyMode``).

    Pure and idempotent: ``raw`` is never mutated, and a migrated document
    migrates to itself. Logs one warning per process the first time it
    migrates.

    Parameters
    ----------
    raw : dict
        Parameter document, as read from JSON.

    Returns
    -------
    dict
        ``raw`` itself when nothing changes, else a migrated shallow copy (the
        ``rtl`` section is copied too).
    """
    global _legacy_mode_warned
    if not isinstance(raw, dict):
        return raw
    mode = raw.get("mission_mode")
    if not isinstance(mode, str) or mode.strip().lower() != LEGACY_INTERMEDIATE_MODE:
        return raw
    migrated = dict(raw)
    migrated["mission_mode"] = "advanced"
    rtl = dict(raw["rtl"]) if isinstance(raw.get("rtl"), dict) else {}
    rtl.setdefault("enabled", False)
    migrated["rtl"] = rtl
    if not _legacy_mode_warned:
        _legacy_mode_warned = True
        logger.warning(
            "mission_mode 'intermediate' was removed; flying 'advanced' with rtl.enabled=%s.",
            rtl["enabled"],
        )
    return migrated


@dataclass
class NetworkConfig:
    """Network connection endpoints and ROS 2 topic names."""

    drone_ip: str = "192.168.42.1"
    namespace: str = "bebop"
    camera_raw_topic: str = "/bebop/camera/image_raw"
    detection_stream_topic: str = "/bebop/camera/detections"
    #: Lightweight overlay feed: detections and stage caption as
    #: ``bmg.detections.v1`` JSON in ``std_msgs/String``. The GCS bridge draws
    #: it over the 33 Hz raw stream, so the cockpit video no longer runs at the
    #: inference rate. See :mod:`mvp_mission_bebop.perception.summary`.
    detection_boxes_topic: str = "/bebop/camera/detection_boxes"
    odometry_topic: str = "/bebop/odom"


@dataclass
class GimbalConstraintsConfig:
    """Mechanical and operational angular limits for the camera gimbal (degrees)."""

    search_tilt_deg: float = -20.0
    #: Steepest gimbal tilt allowed in the Stage 4 framing, in degrees. The
    #: gimbal is never asked to go below this. The drone holds position in
    #: Stage 4, so the closed-loop framing law points the optical axis at the
    #: target within ``[nadir_tilt_deg, search_tilt_deg]``; reaching this floor
    #: without centring the target ends the framing at the best view available.
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
    #:
    #: Safe envelope: ``[-90, search_tilt_deg)``. Below -90 the axis looks
    #: backward; at or above the search attitude the approach can never end.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    centering_tilt_band_up_deg: float = 5.0
    #: How far below the search attitude the gimbal may look while centering.
    #: Wider than the upward margin because the downward direction is where the
    #: projection is well conditioned. The previous symmetric +/-5 deg band
    #: railed the pointing solution against its lower stop for any target closer
    #: than h / tan(search_tilt + 5 deg) -- about 2.1 m at a 1.2 m altitude --
    #: and a railed gimbal cannot null the vertical pixel error.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    centering_tilt_band_down_deg: float = 15.0


@dataclass
class VisionConfig:
    """Computer vision model, inference parameters, and optical tolerances."""

    model_path: str = "yolov8n.pt"
    confidence_threshold: float = 0.50
    #: Detector input size in pixels, or ``None`` for the model's native 640.
    #:
    #: Inference cost falls roughly with the square of this number (bench, one
    #: 856x480 frame, YOLOv8n .pt on CPU: 640 ~81 ms, 480 ~47 ms, 320 ~30 ms).
    #: 480 was validated against 640 on the 35 recorded evidence frames
    #: (2026-09-30): same classes on 33, the other two corrected to the true
    #: target (640 read a bicycle as a motorcycle and as a clock), and 24
    #: target hits against 22 with the .pt weights, 33/35 identical with the
    #: OpenVINO IR. Must be a multiple of 32, the YOLOv8 feature stride.
    inference_imgsz: Optional[int] = 480
    #: Where the detector runs: ``AUTO`` (benchmark on the first real frame),
    #: ``CUDA`` (GeForce MX110), ``IGPU`` (Intel HD 620 through OpenVINO) or
    #: ``CPU`` (OpenVINO). Always with a runtime fallback down CUDA, IGPU, CPU.
    #: Validated by ``perception.inference_device.normalize_inference_device``.
    inference_device: str = "AUTO"
    #: Least time between two inferences of the perception worker, seconds.
    #: Unbounded, YOLO on the CPU took 187 % and dragged the driver's own
    #: camera delivery from 27 to 11 Hz; 0.1 s caps it at 10 Hz.
    inference_min_period_sec: float = 0.10
    #: Oldest detection a control loop may still act on, in seconds.
    #:
    #: Inference runs on its own thread and a loop reads the newest result, so
    #: this bounds the latency between the scene and the command derived from
    #: it. The lower bound is set by the pipeline itself: a result is published
    #: one acquisition (<= 33 ms) plus one CPU inference (150-250 ms) after its
    #: frame, and a bound near that figure turns every slightly slow inference
    #: into a dropout. 0.5 s leaves two worst-case inference periods of margin.
    #: The upper bound is set by motion: at the 0.20 search cruise and the 0.15
    #: approach cap (m/s at the uncalibrated unit gain) a half-second-old
    #: observation places the target 0.10 m and 0.075 m from where it is now --
    #: under the 0.25 m nadir range threshold, and a bearing error of a few
    #: pixels at the ranges where the approach is still translating.
    perception_max_age_sec: float = 0.5
    target_classes: List[str] = field(default_factory=lambda: ["motorcycle", "bicycle"])
    #: Whether a detected ``person`` is treated as a victim.
    #:
    #: ``True`` (normal flight): the boxes read ``Vitima`` and the Stage 4
    #: frame is assessed for a victim (``perception/inspection_worker.py``).
    #: ``False``: the boxes read ``Person``, the victim inference is not run
    #: and every inspection settles as ``not_detected``, so the report always
    #: takes the no-visible-victim branch. Target selection is unaffected: a
    #: person is never a flight target in either mode. Safe envelope: a
    #: ``bool``, refused otherwise by ``update_from_dict``.
    victim_detection_enabled: bool = True
    #: Class priority of the persistent target selector
    #: (``estimation.target_selector``), highest first. The group
    #: ``acidente`` matches every accident label
    #: (``perception.classes.ACCIDENT_CLASS_NAMES``), so the COCO
    #: ``motorcycle``/``bicycle`` and a future model's own ``acidente`` class
    #: rank the same. Classes absent from the list are never selected.
    selector_class_priority: List[str] = field(default_factory=lambda: ["acidente", "motorcycle", "bicycle"])
    #: Least IoU with the tracked box for a detection to continue the track.
    selector_iou_min: float = 0.25
    #: Centroid gate, pixels, when no candidate reaches ``selector_iou_min``.
    selector_gate_px: float = 90.0
    #: Consecutive frames without a match before the track is released.
    selector_lost_frames: int = 6
    #: Consecutive frames a higher-priority candidate must persist to take over.
    selector_switch_frames: int = 4
    #: Weight of the newest box in the exponential smoothing of the track.
    selector_box_alpha: float = 0.5
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
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    approach_centering_tolerance_px: float = 30.0
    #: Vertical centering tolerance as a multiple of the horizontal one.
    #:
    #: Retained for the approach and nadir phases. It is deliberately *not* a
    #: convergence gate during centering: see ``centering_vertical_guard_ratio``.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    acquisition_dwell_sec: float = 1.0
    #: Hard ceiling on the acquisition dwell. Reaching it is not a fault.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    acquisition_timeout_sec: float = 3.0
    #: Lateral corridor half-width during approach, as a multiple of the
    #: horizontal tolerance. Forward speed tapers to zero across this band
    #: rather than switching off at its edge -- see ``alignment_gain``.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    min_alignment_gain: float = 0.25
    #: Per-axis deadband at nadir, as a multiple of the horizontal tolerance.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    nadir_deadband_ratio: float = 0.50
    #: Radial alignment threshold at nadir, as a multiple of the tolerance.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    nadir_alignment_ratio: float = 1.5
    #: Consecutive out-of-corridor frames before the approach concludes that the
    #: cross-track loop is not winning and hands over to reacquisition, which
    #: widens the field of view rather than continuing to creep at a target the
    #: drone is not aimed at.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    decentered_frames_to_revert: int = 10
    #: Consecutive out-of-window frames before leaving the nadir phase.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    reacquire_sweep_deg: float = 30.0
    #: Bounded lateral creep of Stage 3 reacquisition, in normalized units.
    #:
    #: A target that left the frame sideways did so because the airframe
    #: translated past it. The creep moves cross-track toward the side the
    #: target was last seen on, which puts it back inside the frustum. A target
    #: last seen inside the centering tolerance authorises no motion. The name
    #: is kept from the forward pattern for the GCS contract.
    reacquire_reverse_speed: float = 0.05
    #: How long the lateral creep may run before reacquisition holds station.
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    nadir_range_threshold_m: float = 0.25
    #: Per-cycle gimbal step used by the open-loop fallback, in degrees.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    legacy_gimbal_ramp_deg: float = 2.0


@dataclass
class GimbalPIDConfig:
    """High-bandwidth PID gains for vertical optical centering.

    Inert in the lateral flight pattern; retained for the GCS contract.
    Stage 4 frames the target with the closed-form gimbal law
    (``controllers/gimbal_framing``), which reads none of these gains.
    """

    kp: float = 18.0
    ki: float = 0.0
    kd: float = 1.2
    output_limits: Tuple[float, float] = (-18.0, 18.0)


@dataclass
class LateralPIDConfig:
    """PID gains for horizontal centering via body-frame lateral velocity."""

    kp: float = 0.18
    ki: float = 0.0
    kd: float = 0.03
    output_limits: Tuple[float, float] = (-0.04, 0.04)
    deadband: float = 0.002
    #: Smallest cross-track command the airframe actually answers.
    #:
    #: Declared since the first revision and, until now, never read by anything.
    #: The consequence is structural rather than cosmetic: the driver quantizes
    #: to ``int8(v * 100)``, so with ``kp = 0.18`` on normalized pixel error a
    #: 35 px offset on an 856 px frame demands 0.015 -- two driver counts, below
    #: the airframe's response threshold. The lateral loop therefore asymptoted
    #: into the actuator dead zone instead of reaching the centering tolerance.
    #: It is applied through :class:`QuantizedCommandShaper` as a duty cycle, not
    #: as a hard floor, so the commanded mean still tracks the demand down to
    #: zero.
    min_effective_velocity: float = 0.02
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

    # ------------------------------------------------------ lift feedforward

    #: Anticipatory climb command per unit of commanded horizontal speed
    #: (normalized-to-normalized gain; both axes share the driver's [-1, 1]
    #: command scale). The integral term already rejects the *sustained*
    #: translation-induced sink, but only after the altitude has already
    #: sagged enough to accumulate against it. This term requests climb
    #: authority the instant translation begins, ahead of that lag.
    #:
    #: Defaults to zero -- inert -- until a field measurement of the actual
    #: sink-per-commanded-speed relationship justifies a nonzero value. See
    #: the design spec's "field validation required" section.
    feedforward_gain: float = 0.0
    #: Climb feedforward per unit of commanded ``|vyaw|``, normalized. A turn
    #: tilts the thrust vector like a translation does, and the Simple profile
    #: turns through Stages 2 to 4. Zero (inert) until identified from flight
    #: data (``scripts/analyze_flight_logs.py --identify-sink``).
    yaw_feedforward_gain: float = 0.0
    #: Climb feedforward per unit of ``|d|v_h|/dt|`` (normalized per second),
    #: low-passed at ``accel_feedforward_cutoff_hz``: the sink is largest while
    #: the airframe pitches into or out of a translation. Zero (inert) until
    #: identified.
    accel_feedforward_gain: float = 0.0
    #: Cutoff of the first-order filter on the acceleration term, hertz.
    accel_feedforward_cutoff_hz: float = 2.0

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


#: Floor of ``kinematics.countdown_sec``, seconds.
#:
#: Zero is a valid countdown, not a sentinel: the takeoff stage then warms the
#: detector and samples the ground reference before liftoff without the
#: countdown overlapping them (``steps/takeoff.py:_countdown``), and the
#: three-second clearance call is raised at once. Negative and non-finite
#: values are refused by ``mission.py`` before anything is persisted.
COUNTDOWN_MIN_SEC: Final[float] = 0.0

@dataclass
class FlightKinematicsConfig:
    """Translational velocity caps and geometric safety envelopes."""

    #: Operating altitude above the calibrated ground reference, in metres.
    #:
    #: Safe envelope: above ``VisionConfig.min_altitude_for_ibvs_m``, below
    #: which the approach abandons the IBVS projection for the open-loop ramp,
    #: and above :attr:`takeoff_settle_min_altitude_m`, below which no hover
    #: sample counts as settled and the stabilization gate can only time out.
    target_altitude_m: float = 1.00
    #: Safety headroom allowed above target_altitude_m before failsafe triggers, in metres.
    #: 0.60 m absorbs climb momentum, sonar dispersion, and pitch-induced tilt reading spikes.
    #:
    #: Safe envelope: at or above :attr:`climb_deadband_m` plus twice
    #: :attr:`climb_settle_max_position_sigma_m` (0.21 m). The hover the climb
    #: accepts reaches ``target + climb_deadband_m``, and the sonar/baro scatter
    #: a settled hover still shows is bounded by the settle sigma; a ceiling
    #: inside that band trips ``OdometrySupervisor.is_ceiling_breached`` in nominal flight.
    #: Below :attr:`climb_deadband_m` itself it trips on arrival (station rule
    #: E04; W10 below the band). The end of the firmware's launch profile is
    #: covered by the ceiling ratchet (``estimation.ceiling_schedule``), not by
    #: this margin.
    #:
    #: Read only for a launch from the floor: on the pad top the ceiling is
    #: fixed at ``max(target, native height) + TOTEM_CEILING_HEADROOM_M`` (D2)
    #: and the stored value is ignored.
    altitude_ceiling_margin_m: float = 0.60
    #: Consecutive odometry samples above the ceiling required to declare a breach.
    #: ``/bebop/odom`` arrives at about 5 Hz (the driver integrates
    #: ``SpeedChanged`` at the firmware's rate), so 3 samples are about 0.6 s of
    #: debounce, rejecting instantaneous ultrasonic/baro spikes.
    ceiling_breach_streak: int = 3
    #: Search cruise setpoint, normalized. Stage 2 flies it as a leftward
    #: lateral velocity (``vy > 0``, FLU) along the outbound track; the name is
    #: kept for the GCS contract and rule E05.
    forward_cruise_velocity: float = 0.20
    #: Forward approach speed ceiling, normalized. No forward motion is
    #: commanded after the target is confirmed.
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
    max_approach_forward_speed: float = 0.15
    #: Safety ceiling on the post-takeoff stabilization gate, in seconds.
    #:
    #: Was an unconditional hover of this length. The stage now leaves as soon
    #: as the ``takeoff_settle_*`` criteria hold and waits this long only when
    #: they never do -- which is also the previous behaviour, so a gate that
    #: cannot be satisfied costs nothing relative to the old wait.
    takeoff_stabilize_duration_sec: float = 4.0
    hover_duration_sec: float = 7.0
    #: Pre-flight countdown, seconds; 0 means none. Taken literally by the
    #: station and by the mission (see :data:`COUNTDOWN_MIN_SEC`).
    #:
    #: Was 0.0, which the station read as "unset" and replaced with its own 10 s,
    #: so an operator who asked for no countdown got one. The default is now the
    #: station's figure and every value, 0 included, means what it says.
    countdown_sec: float = 10.0
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
    #:
    #: Inert in the lateral flight pattern; retained for the GCS contract.
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
    #: because the stall detector (in TakeoffStep._acquire_altitude) provides an earlier exit when
    #: the firmware rejects the commanded vz.
    climb_timeout_sec: float = 15.0
    #: Multivariate settling gate for the climb. The drone must hold inside the
    #: deadband, with low vertical speed and low dispersion, continuously --
    #: a single sample crossing the target proves nothing about convergence.
    #: Tuned for the Bebop sonar/barometer noise floor (~0.05-0.08 m).
    climb_settle_window_sec: float = 1.00
    climb_settle_min_samples: int = 5
    climb_settle_max_speed_mps: float = 0.05
    climb_settle_max_position_sigma_m: float = 0.08
    #: Lead of the closed-loop climb (``controllers.altitude_acquisition``), s.
    #:
    #: The Bebop's vertical axis is a first-order lag of about 0.35 s behind
    #: 0.10-0.20 s of transport delay, read by 5 Hz odometry. The position
    #: error is taken against ``z + climb_lag_sec * vz``, where the airframe
    #: will be once the velocity it already has plays out, which is what keeps
    #: the climb from carrying 9-13 cm past the target at 0.25 m/s. Audited in
    #: ``[0, 1]``.
    climb_lag_sec: float = 0.35
    #: Position gain of the closed-loop climb, 1/s. Audited in ``(0, 4]``.
    climb_position_kp: float = 1.5
    #: Deceleration of the climb's braking envelope, m/s^2: easing starts
    #: about 0.26 m out at 0.25 m/s.
    climb_decel_mps2: float = 0.12
    #: Convergence gate that ends the post-takeoff stabilization early.
    #:
    #: The Bebop runs its own launch profile and the SDK's ``takeoff`` returns
    #: after a fixed delay without confirming anything, so the gate watches the
    #: airframe instead: mean ``|vz|`` and the standard deviation of altitude
    #: over a sliding window, on the same sonar/barometer noise floor the climb
    #: gate is tuned for (~0.05-0.08 m). Only *new* odometry samples count, so a
    #: stalled telemetry stream reads as "not yet settled" rather than as a
    #: perfectly still aircraft. Four samples over the window is what a 5 Hz
    #: odometry stream delivers with margin.
    takeoff_settle_window_sec: float = 1.00
    takeoff_settle_min_samples: int = 4
    takeoff_settle_max_vz_mps: float = 0.10
    takeoff_settle_max_altitude_sigma_m: float = 0.08
    #: Altitude below which a sample cannot be evidence of a settled hover, in
    #: metres. Before liftoff -- motors still spooling when ``takeoff`` returns
    #: -- the drone sits on the ground with zero ``vz`` and zero dispersion,
    #: which is exactly what settlement looks like. Samples under this height
    #: restart the window. Well under the ~1.0 m firmware hover height and above
    #: the sonar's ground return.
    takeoff_settle_min_altitude_m: float = 0.30
    #: Hard envelope on any horizontal velocity component reaching the driver,
    #: in normalized units.
    #:
    #: Deliberately above every legitimate demand in the mission -- cruise 0.20,
    #: approach 0.15, cross-track 0.22, RTL 0.10 -- so it never shapes normal
    #: guidance, and far below the driver's full scale of 1.0 so a defect in any
    #: guidance law cannot command a full-throttle translation. Until now no
    #: bound of any kind stood between a guidance law and the wire: the failsafe
    #: saturated ``vz`` and ``vyaw`` and passed ``vx``/``vy`` through untouched.
    #:
    #: Safe envelope: at or above every legitimate demand (the cruise and
    #: approach speeds, the lateral PID ceiling, the RTL cruise) and below 1.0.
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

    #: End of flight after the evidence. ``True``: the full return, with the
    #: marker search, the centering and the precision landing on the pad
    #: (in the Simple profile, the centering over the pad it never left).
    #: ``False``: the airframe lands where it is right after the evidence, in
    #: every profile; the marker fields below are then not read.
    #:
    #: Safe envelope: a ``bool``. ``update_from_dict`` refuses anything else
    #: (:class:`ParameterTypeError`): the string ``"false"`` is truthy.
    enabled: bool = True
    #: Speed ceiling of the odometric return along the return track (body Y,
    #: to the right in the lateral flight pattern), in normalized units.
    max_speed: float = 0.10
    #: PD gains of the odometric return. In the lateral flight pattern ``kp``
    #: and ``kd`` act on the off-track trim (body X) and ``lateral_kp`` and
    #: ``lateral_kd`` on the return channel (body Y), under the feedforward
    #: braking profile. The names predate the pattern and are kept for the GCS
    #: contract. The gains were declared and never read by the previous
    #: implementation, which had no control law at all -- only a piecewise ramp
    #: in displacement.
    kp: float = 0.15
    kd: float = 0.01
    lateral_kp: float = 0.18
    lateral_kd: float = 0.02
    #: Ceiling of the off-track trim in the odometric return, applied on the
    #: body X axis in the lateral flight pattern.
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
    #: Bounded leftward authority (``vy > 0``) for nulling a terminal overshoot,
    #: normalized.
    #:
    #: RTL flies strictly to the right, and that rule used to be absolute: when
    #: the origin ended up on the outbound side the return axis was simply held
    #: at zero. A braking profile plus odometry lag makes overshoot close to
    #: certain, so the drone would arrive a little past the origin, freeze that
    #: axis, and hover there -- distance never changing, arrival never
    #: confirming -- until the whole return window expired. Observed in flight as
    #: "returns to the takeoff point and never lands".
    #:
    #: The invariant exists to keep the return a straight reverse line with no
    #: yaw, which protects optical-flow fidelity. A centimetre-scale leftward trim
    #: in the terminal regime does not threaten that; hovering off-target until a
    #: timeout does. Bounded well below the cruise cap, and only applied inside
    #: ``overshoot_recovery_radius_m``.
    overshoot_recovery_speed: float = 0.04
    #: Range within which leftward overshoot recovery is permitted, in metres.
    #: Beyond it the origin being that far to the left means something worse
    #: than overshoot has happened, and creeping back out is not the answer.
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
    max_accel_mps2: float = 0.12
    max_jerk_mps3: float = 0.35
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
    #:
    #: Safe envelope: a non-negative integer inside :attr:`marker_dict`.
    target_aruco_id: int = 8
    #: ArUco/AprilTag dictionary identifier, passed to
    #: ``nectar.vision.Aruco``.  Accepts legacy integer orders (4, 5, 6, 7 for
    #: ``DICT_NxN_1000``), OpenCV enum integers (e.g. ``20`` for
    #: ``DICT_APRILTAG_36h11``), or string names (e.g. ``"DICT_APRILTAG_36h11"``,
    #: ``"tag36h11"``).
    #:
    #: Safe envelope: a value the Stage 5 resolver accepts. Anything else
    #: leaves the return on the degraded odometric path described above.
    marker_dict: Union[int, str] = "DICT_APRILTAG_36h11"
    #: Physical edge length of the printed marker, in metres.
    #:
    #: This is the scale factor of the entire pose estimate: ``solvePnP`` recovers
    #: translation in units of the object model it is given, so a tag declared
    #: 0.20 m and printed 0.15 m reports every distance 33% too large and the
    #: centering law converges onto a point 33% off. Measure the printed
    #: marker's black border, edge to edge.
    #:
    #: Safe envelope: a positive, finite length; the station also caps it at
    #: 1.0 m (rule E07).
    tag_size: float = 0.20
    #: Gimbal depression for the return leg, in degrees (negative is down).
    #:
    #: Near-nadir, and deliberately not fully nadir. The return leg is flown to
    #: the right, so the pad enters the frame through its right side and the
    #: search range is set by the horizontal field of view, not by this angle.
    #: Ten degrees of forward lean puts the optical axis on the ground roughly
    #: ``altitude / tan(80 deg)`` ahead -- 0.18 m at 1.0 m AGL -- so the frame
    #: keeps ground below and slightly ahead of the airframe in view, which
    #: tolerates an off-track offset of the pad along the nose while centering.
    #:
    #: Safe envelope: ``(-90, 0)``, down but never exactly nadir.
    camera_tilt_deg: float = -80.0
    #: Reverse cruise command for the marker search, normalized.
    #:
    #: Signed, and the sign is load-bearing (rule E05): the search leg is the
    #: mirror of Stage 2, flown to the right along the track the mission came
    #: out on as ``vy = -abs(reverse_cruise_velocity)``, and the return command
    #: is saturated at the actuator boundary so a sign error in configuration
    #: cannot turn the return into an outbound cruise. Slower than the Stage 2 cruise because the payoff is detection
    #: probability per metre travelled, not metres travelled.
    #:
    #: Safe envelope: strictly negative. The station writes it as minus
    #: ``kinematics.forward_cruise_velocity`` and refuses any other value
    #: (rule E05), so a station launch flies the search speed back; this
    #: default is what a run without the station flies.
    reverse_cruise_velocity: float = -0.10
    #: Longitudinal (body-x) centering gains, m/s per metre of error and per
    #: metre-per-second of error rate.
    centering_kp_x: float = 0.16
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
    #: Speed ceiling of the same centring in the Simple profile, normalized.
    #: The Simple profile centres over the pad it never left, from a short
    #: offset (the camera-to-body lever arm plus the hover drift), and at the
    #: advanced ceiling its 15 s window always expired before the landing gate.
    #: The gates, tolerances and the frame-edge guard are the same in both.
    simple_max_centering_speed: float = 0.04
    #: Forward speed cap of the centring, m/s: forward motion pushes the marker
    #: toward the bottom edge at a nadir-ish tilt, where its quiet zone is lost.
    centering_forward_cap_mps: float = 0.015
    #: The same cap in the Simple profile, m/s.
    simple_centering_forward_cap_mps: float = 0.035
    #: Measured horizontal speed, m/s, above which the marker centring (and the
    #: tracked descent) stops commanding translation and sends an exactly zero
    #: roll/pitch, so the driver selects the firmware hover and its optical-flow
    #: brake. The Bebop flies roll/pitch as a tilt, not a velocity: a held
    #: 0.02 keeps accelerating the airframe, which is how a 0.02 centring command
    #: drifted 0.5 m forward and lost the marker. Safe envelope: ``(0, 0.3]``.
    centering_overspeed_mps: float = 0.06
    #: Measured speed under which the brake releases and the centring resumes
    #: from rest, m/s. Below :attr:`centering_overspeed_mps` (hysteresis).
    centering_overspeed_release_mps: float = 0.03
    #: Radial error inside which the airframe counts as centred, in metres.
    centering_tolerance_m: float = 0.04
    #: Radial error inside which the landing is authorized, once at residual
    #: speed for :attr:`centering_settle_cycles` consecutive cycles, in metres.
    #:
    #: The operational statement of "centred over the pad", and strict: the
    #: mission must land on the centre of the marker, not merely on the pad.
    #: The previous 0.13 m let the gate open on the first cycle with a residual
    #: of 0.06-0.13 m (median 0.11 m over 18 logged landings), so the PD's own
    #: ``centering_tolerance_m`` never acted. With the staged descent
    #: (``visual_descent_enabled``) the error is also tracked down to
    #: ``commit_height_m`` instead of being frozen at the gate.
    #:
    #: Safe envelope: at or above :attr:`centering_tolerance_m`.
    landing_radius_m: float = 0.06
    #: Horizontal distance from the CoG to the camera lens, positive with the
    #: lens ahead of the CoG, metres.
    #:
    #: The marker projection resolves the error at the lens; without this
    #: vector the law centres the lens over the marker and leaves the CoG
    #: behind it. 0.04 m keeps the camera lens looking directly over the
    #: marker center with the white border centered in the FOV, maximizing
    #: marker detection margin and preventing quiet zone clipping at low altitude.
    #:
    #: Safe envelope: ``|offset| <= 0.3``.
    camera_offset_forward_m: float = 0.04
    #: Horizontal distance from the CoG to the lens, positive with the lens to
    #: the left, metres. Zero: the camera sits on the hull's centreline.
    #:
    #: Safe envelope: ``|offset| <= 0.3``.
    camera_offset_left_m: float = 0.0
    #: Fly the marker-tracked staged descent between the centring gate and the
    #: touchdown (``controllers.precision_descent``). ``False`` restores the
    #: blind landing from the centring height.
    visual_descent_enabled: bool = True
    #: Peak descent speed of the tracked descent, m/s. Safe envelope: ``(0, 0.25]``.
    descent_speed_mps: float = 0.12
    #: Radial error that must hold, at residual speed, before the descent
    #: starts, metres. Safe envelope: at or above :attr:`centering_tolerance_m`.
    descent_gate_radius_m: float = 0.05
    #: The descent pauses and realigns when the error exceeds
    #: ``descent_gate_radius_m`` times this factor.
    descent_hysteresis: float = 1.5
    #: Height over the marker from which the touchdown is flown blind, metres.
    #: Safe envelope: ``[0.20, 0.80]``.
    commit_height_m: float = 0.40
    #: Radial error required at the commit height, metres.
    commit_radius_m: float = 0.15
    #: Height under which the horizontal correction is scaled down: the rotor
    #: wash reflected by the ground amplifies every correction, metres.
    ground_effect_height_m: float = 0.60
    #: Scale of the horizontal correction under ``ground_effect_height_m``.
    ground_effect_gain_scale: float = 0.6
    #: Marker loss tolerated once the marker has been sighted, before the
    #: touchdown is committed, seconds. A descent already under way keeps
    #: descending through it; an alignment holds height. Never a realignment and
    #: never a climb: the debounce only rejects a single dropped frame.
    descent_marker_lost_hold_sec: float = 0.25
    #: Height over the marker under which losing the marker commits the touchdown
    #: on the first missed frame, metres: the marker leaves the field of view by
    #: proximity there, and waiting for it is hovering for nothing.
    descent_lost_commit_height_m: float = 0.60
    #: Total time the tracked descent may spend not descending (alignment, pauses
    #: for a grown error, waiting at the commit height for the radius), seconds.
    #: Once spent, the descent proceeds and commits regardless of the radial
    #: error: optical flow and wind never hold an ideal alignment for long.
    descent_pause_max_sec: float = 1.5
    #: Bound on the tracked descent, seconds; reaching it commits the touchdown.
    descent_timeout_sec: float = 20.0
    #: Consecutive cycles inside :attr:`landing_radius_m` *at residual speed*
    #: required before the landing is authorized. A single sample inside the
    #: radius is satisfied by a drone flying through it at speed; two
    #: consecutive samples at residual speed is the minimum that distinguishes
    #: "passing through" from "arrived", without paying for cycles beyond what
    #: that distinction needs.
    centering_settle_cycles: int = 2
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
    #: Bounded leftward creep (``vy > 0``, never ``vx``) commanded while the
    #: marker is lost, normalized. The return is flown to the right, so an
    #: overshot marker leaves the frame on its left side.
    #:
    #: A marker lost while the airframe was still actively closing on it -- not
    #: already at rest over it -- is very often a marker that has just slipped
    #: past the left edge of the frame. Holding station and waiting out
    #: lost_frames_tolerance treats that the same as any other dropout;
    #: creeping left treats it as what it almost always is.
    #: Bounded well below reverse_cruise_velocity: this is a re-framing nudge,
    #: not a second search leg.
    reacquire_creep_speed: float = 0.03
    #: Duration the leftward creep runs before yielding to a plain hold, in
    #: seconds.
    reacquire_creep_sec: float = 1.0
    #: Radial error, at the last valid sighting, below which a subsequent loss
    #: is treated as a close-in overshoot rather than a general dropout. Losses
    #: from well outside this range are not the narrow-FOV failure the creep
    #: exists for, and creeping there would be aimless.
    reacquire_creep_range_m: float = 0.30
    #: Window for the centering phase alone, in seconds.
    #:
    #: Separate from :attr:`timeout_sec`, which bounds the search. Sharing one
    #: window would let a long search consume the budget for the convergence it
    #: exists to enable, and the failure mode of that is an aircraft landing
    #: half-centred because the clock ran out on a phase that was converging.
    #: Edited in the station's advanced settings, inside ``[1, 120]`` s.
    #:
    #: Safe envelope: a positive, finite duration. Zero ends the centering
    #: before its first cycle, and a negative value makes the phase's
    #: ``Deadline`` raise in flight.
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

    # -------------------------------------------------- altitude plausibility

    #: Master switch for the sonar false-climb plausibility gate applied to
    #: live odometry. With this false the raw altitude is trusted exactly as
    #: before this fix, which is the escape hatch for a field session where
    #: the gate itself misbehaves.
    plausibility_enabled: bool = True
    #: Ceiling on the airframe's real vertical speed the gate will accept in
    #: one cycle, in metres per second.
    plausibility_max_speed_mps: float = 1.00
    #: Ceiling on the airframe's real vertical acceleration, in metres per
    #: second squared. Matches the altitude governor's own command profile
    #: limit (``AltitudeGovernorConfig.max_accel_mps2``): a controller that
    #: cannot command more than this cannot have produced more than this.
    plausibility_max_accel_mps2: float = 0.40
    #: Consecutive out-of-envelope samples required before a new regime is
    #: accepted rather than held.
    plausibility_reject_streak: int = 3

    # ------------------------------------------------------- altitude probe

    #: Log ``[ALT_PROBE ...]`` once a second: along-track position, platform
    #: state, odometric altitude, the firmware's own ``states/altitude`` and
    #: the vertical speed. Log only; nothing reads it back.
    altitude_probe_enabled: bool = True

    # ------------------------------------------------ vertical state estimator

    #: Altitude source for live odometry: ``"legacy"`` keeps ``pose.z`` behind
    #: the position plausibility gate above; ``"vz_gated"`` integrates the
    #: gated vertical speed instead (``estimation.vertical_estimator``);
    #: ``"terrain_kf"`` runs the terrain-aware Kalman filter
    #: (``estimation.terrain_vertical_filter``). Stays ``"legacy"`` until the
    #: emulated M1/M2/M3 criteria and field identification F-ID pass (D7).
    #:
    #: Safe envelope: one of ``"legacy"``, ``"vz_gated"``, ``"terrain_kf"``;
    #: anything else is refused with ``ValueError`` when the odometry
    #: supervisor is built.
    altitude_estimator: str = "legacy"
    #: Ceiling on a trusted vertical speed sample, in metres per second.
    #:
    #: Describes the firmware's reaction, not the governor's commanded profile:
    #: the 0.40 m/s^2 of ``plausibility_max_accel_mps2`` would reject the real
    #: response to a sonar step.
    vz_max_speed_mps: float = 1.5
    #: Ceiling on a trusted change of vertical speed, in metres per second squared.
    vz_max_accel_mps2: float = 5.0
    #: Consecutive out-of-envelope samples after which a new regime is accepted.
    vz_reject_streak: int = 3
    #: Pull the estimate toward ``/bebop/states/altitude`` (``"vz_gated"`` only).
    altitude_anchor_enabled: bool = False
    #: Time constant of that pull, in seconds.
    altitude_anchor_tau_sec: float = 6.0
    #: Innovation beyond which the anchor does not correct, in metres; 3 s of
    #: persistent disagreement is logged.
    altitude_anchor_gate_m: float = 0.25

    # ------------------------------------------- terrain-aware Kalman filter

    #: What ``/bebop/states/altitude`` (``AltitudeChanged``) measures, for the
    #: ``"terrain_kf"`` estimator: ``"unknown"`` (logged only, never used),
    #: ``"baro"`` (the airframe's height, plus a bias the filter estimates) or
    #: ``"sonar_fused"`` (the height over the terrain below, which steps by the
    #: pad height at its edge). Stays ``"unknown"`` until field test F-ID-1. [A CONFIRMAR]
    #:
    #: Safe envelope: one of ``"unknown"``, ``"baro"``, ``"sonar_fused"``.
    firmware_altitude_source: str = "unknown"
    #: First-order lag of the airframe's vertical speed to the commanded
    #: ``gaz``, in seconds: the process model of ``"terrain_kf"``. [A CONFIRMAR]
    #:
    #: Safe envelope: ``(0.05, 2.0]``.
    vertical_lag_tau_sec: float = 0.35
    #: Process noise of ``"terrain_kf"``: the unmodelled vertical
    #: acceleration, 1-sigma, in metres per second squared. [A CONFIRMAR]
    #:
    #: Safe envelope: ``(0, 10]``.
    vertical_accel_noise_mps2: float = 1.5
    #: Noise of the reported ``speedZ``, 1-sigma, in metres per second. [A CONFIRMAR]
    #:
    #: Safe envelope: ``(0, 1]``.
    vz_measurement_noise_mps: float = 0.08
    #: Noise of ``AltitudeChanged`` as a measurement, 1-sigma, in metres. [A CONFIRMAR]
    #:
    #: Safe envelope: ``(0, 1]``.
    altitude_measurement_noise_m: float = 0.10
    #: Factor on the ``speedZ`` noise variance while the platform edge guard
    #: window is open (``LEAVING``/``ENTERING``): the sonar step is expected
    #: there, so ``speedZ`` weighs less and a rejected sample is not taken as
    #: a new regime until the window closes. [A CONFIRMAR]
    #:
    #: Safe envelope: ``[1, 1000]``.
    terrain_step_noise_scale: float = 16.0


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
    settle_max_speed_mps: float = 0.05
    settle_max_position_sigma_m: float = 0.04
    #: Ceiling on the mean absolute commanded vertical speed tolerated during
    #: the stillness window, in the same normalized units
    #: ``AltitudeHoldGovernor.compute_vz`` returns.
    #:
    #: The horizontal criteria above say nothing about the vertical axis: a
    #: hover can be statistically still in x/y while the altitude governor is
    #: still actively correcting a sink, and the firmware's autonomous
    #: `do_hover` mode will not engage while any nonzero `gaz_speed` is being
    #: commanded (`bebop.cpp::Bebop::move`). This closes that gap.
    settle_max_vertical_speed: float = 0.02
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
    #: Window for the aircraft to report the native 14 MP photo
    #: (``PictureEventChanged``), counted from the request.
    #:
    #: Measured on the aircraft: TAKEN 2.5 s after the request. The capture
    #: does not block for it; the hover resolves it and rewrites the sidecar,
    #: and the end of Stage 4 waits out the rest of the window while holding
    #: station. A missing acknowledgement never fails the capture.
    native_photo_ack_timeout_sec: float = 4.0


@dataclass
class TimeoutsConfig:
    """Execution timeouts and sensor watchdog windows."""

    #: Longest wait for ``FlatTrimChanged`` after the flat-trim request, in
    #: seconds. The Bebop answers within a few hundred milliseconds on a level
    #: surface; a real flight without the answer does not take off.
    flat_trim_ack_timeout_sec: float = 3.0

    #: Longest wait for confirmation that the airframe actually left the ground
    #: (flying_state in {1, 2} or altitude > takeoff_settle_min_altitude_m) after
    #: the takeoff command, in seconds (R1, D2).
    takeoff_confirm_timeout_sec: float = 6.0

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
    tracking_timeout_sec: float = 15.0
    target_recovery_timeout_sec: float = 4.0
    odometry_heartbeat_timeout_sec: float = 3.0
    #: Frame-heartbeat age at which the camera stream counts as lost and
    #: ``FailsafeSupervisor.evaluate_system_health`` lands the aircraft, in
    #: seconds. Safe envelope: 1.5-2.0 s.
    #:
    #: Was 8.0, sixteen times the 0.5 s ``perception_max_age_sec`` the control
    #: laws already treat as the oldest usable observation: for the last 7.5 s
    #: of that window Stages 2 and 3 were translating with no fresh evidence at
    #: all -- 1.6 m at the 0.20 search cruise -- before anything reacted. The
    #: heartbeat is refreshed once per new inference result, 150-250 ms apart on
    #: the ground station's CPU, so 2.0 s is eight worst-case inference periods:
    #: a single slow inference cannot trip it, a dead link or a hung detector
    #: does. The lower edge of the envelope, 1.5 s, still leaves six. No
    #: separate, longer diagnostic threshold is kept, because nothing in the
    #: mission or the station would consume one.
    video_stream_timeout_sec: float = 2.0


@dataclass
class BatteryConfig:
    """Critical-charge return, owned by the mission process.

    At ``land_pct`` the airborne mission jumps to Stage 5 and flies the return
    of the active profile, even with ``rtl.enabled = false``; it never lands in
    place because of the charge. The ground station's trigger
    (``bebop_mission_control/src/lib/batteryFailsafe.ts``) is redundancy. The
    same figure gates the Stage 1 launch. See
    :mod:`mvp_mission_bebop.telemetry.battery`.
    """

    #: Charge, in percent, at or below which the airborne mission returns to base.
    #:
    #: Sent by the station at launch as ``battery.land_pct`` in the
    #: ``--params-json`` payload, carrying the operator's battery-flyout
    #: threshold (``failsafe.thresholdPct`` in ``App.tsx``). Absent -- a CLI run
    #: without the station -- it falls back to this default, which is the same
    #: number as ``FAILSAFE_DEFAULT.thresholdPct`` in ``App.tsx`` by convention
    #: only: the two languages cannot share the constant, so a change on either
    #: side must be replicated on the other.
    land_pct: float = 20.0
    #: Whether the critical-charge return and the pre-flight charge gate act.
    #:
    #: Sent by the station as ``battery.enabled`` with every launch and, in
    #: flight, on ``/bebop/mission/battery_failsafe``. ``False`` disables the
    #: return, the Stage 1 refusal and the unknown-charge standby rule; the
    #: informative 10 % warning (``mission.battery_warning``) is still emitted,
    #: since it neither blocks nor triggers anything. A return already started
    #: is never cancelled by switching it off. Validated by
    #: ``telemetry.battery.normalize_enabled``, which also accepts the strings
    #: ``"true"``/``"false"`` a hand-edited document may carry.
    enabled: bool = True


@dataclass
class PlatformConfig:
    """Takeoff pad the mission launches from, lands on and flies above.

    The ground reference ``z0`` is calibrated on whatever the airframe rests on
    before takeoff, so on a pad ``relative_altitude`` is the height above the
    *top of the pad*. The governor, the ceiling and the touchdown live in that
    frame and need nothing from this section. The operating height is stated
    over the floor instead (``kinematics.target_altitude_m``), and two things
    therefore have to know the pad exists: the control target, which is
    ``target_altitude_m - height_m``, and the ground projection of the visual
    approach, which needs the height over the plane the target lies on.

    The rest of the section is the edge guard. The Bebop holds height with a
    downward sonar, and crossing the pad edge moves its echo from the pad top
    to the floor: a ``height_m`` step in the reading that the firmware answers
    by moving the airframe, not by correcting the reading. Every field below
    ``edge_ahead_m`` is inert while it is ``None``.

    With every field at its default the mission is identical to a flight from
    the floor.
    """

    #: Height of the pad top above the floor, in metres: the physical pad,
    #: whichever surface the flight takes off from.
    #:
    #: Safe envelope: ``>= 0``. ``0.0`` is a launch from the floor.
    height_m: float = 0.0
    #: Launch surface. ``False``: the airframe takes off from the top of the
    #: pad of :attr:`height_m`. ``True``: it takes off from the floor and
    #: :attr:`height_m` takes no part in the flight
    #: (``mission_mode.apply_launch_surface`` zeroes it in memory, after the
    #: document was persisted).
    #:
    #: Safe envelope: a ``bool``. ``update_from_dict`` refuses anything else.
    takeoff_from_ground: bool = False
    #: Venue ceiling above the floor, in metres; ``None`` when unknown.
    #:
    #: Clamps the effective altitude ceiling to ``venue_ceiling_agl_m -
    #: height_m`` in the control frame, whatever the margin and the allowance
    #: would otherwise permit.
    #:
    #: Safe envelope: ``> kinematics.target_altitude_m``.
    venue_ceiling_agl_m: Optional[float] = None
    #: Distance from the takeoff point to the pad edge along the outbound track
    #: (lateral, to the left), in metres, measured with a tape. ``None`` disables the platform monitor
    #: and the edge guard.
    #:
    #: Safe envelope: ``> 0``.
    edge_ahead_m: Optional[float] = None
    #: Uncertainty of the odometric along-track position over the 1-3 m the
    #: mission flies near the pad, in metres.
    #:
    #: Safe envelope: ``>= 0``.
    edge_uncertainty_m: float = 0.15
    #: Effective half-angle of the sonar beam, in degrees. Sets ``s*``, the
    #: along-track position at which the pad leaves the beam:
    #: ``edge_ahead_m + h * tan(sonar_half_angle_deg)``. Unconfirmed; measured
    #: in field test F-2.
    #:
    #: Safe envelope: ``(5, 45)``.
    sonar_half_angle_deg: float = 20.0
    #: How far before ``s* - edge_uncertainty_m`` the guard opens, in metres.
    #:
    #: Safe envelope: ``>= 0``.
    guard_lead_m: float = 0.40
    #: How far beyond ``s* + edge_uncertainty_m`` the guard stays open, in
    #: metres. Covers the recovery of a sink that starts at the edge.
    #:
    #: Safe envelope: ``> 0``.
    guard_extent_m: float = 1.0
    #: Climb ceiling of the altitude hold inside the guard, normalized. The
    #: global one is ``governor.max_climb_speed``.
    #:
    #: Safe envelope: ``<= 0.30``.
    guard_climb_authority: float = 0.20
    #: Descent ceiling of the altitude hold inside the guard, normalized. The
    #: global one is ``governor.max_descent_speed``.
    #:
    #: Safe envelope: ``<= 0.30``.
    guard_descent_cap: float = 0.16
    #: Multiplier on ``governor.kp`` inside the guard.
    #:
    #: Safe envelope: ``[1, 3]``.
    guard_kp_scale: float = 1.5
    #: Deadband of the altitude hold inside the guard, both sides, in metres.
    #:
    #: Safe envelope: ``>= 0``.
    guard_deadband_m: float = 0.01
    #: Vertical error above which translation stops inside the guard, in metres.
    #:
    #: Safe envelope: ``> guard_resume_error_m``.
    guard_stop_error_m: float = 0.15
    #: Vertical error below which translation resumes, in metres.
    #:
    #: Safe envelope: ``< guard_stop_error_m``.
    guard_resume_error_m: float = 0.08
    #: Render a sub-quantum vertical demand as +/-0.01 pulses inside the guard.
    #:
    #: The firmware engages its own sonar hover only on an exactly zero
    #: ``gaz``; a demand under the driver's ``int8(v * 100)`` quantum is
    #: transmitted as that zero.
    guard_vertical_shaper: bool = True
    #: Extra ceiling headroom granted only while the guard is open, in metres.
    #: Suggested 0.4; unconfirmed until field test F-3.
    #:
    #: Safe envelope: ``>= 0``. Always bounded by the venue ceiling.
    ceiling_allowance_m: float = 0.0
    #: How long a ceiling breach is tolerated while re-entering the pad
    #: (``PlatformState.ENTERING``), in seconds.
    #:
    #: Coming back over the edge, the sonar reading drops by the pad height and
    #: the firmware may climb on its own. An emergency landing there puts the
    #: airframe down on the pad edge. Inside this window the breach commands the
    #: full guard descent instead; past it, or once the airframe leaves
    #: ``ENTERING`` still above the ceiling, the emergency landing applies as
    #: everywhere else. Inert while ``edge_ahead_m`` is ``None``.
    #:
    #: Safe envelope: ``> 0``.
    reentry_breach_grace_sec: float = 3.0
    #: Anticipatory climb on leaving the pad, in metres per second. Inert until
    #: field test F-2 measures the sink.
    #:
    #: Safe envelope: ``>= 0``.
    sag_feedforward_mps: float = 0.0
    #: Anticipatory descent on entering the pad, in metres per second. Inert
    #: until field test F-3 measures the false climb.
    #:
    #: Safe envelope: ``>= 0``.
    rise_feedforward_mps: float = 0.0
    #: Duration of either feedforward after the transition, in seconds.
    #:
    #: Safe envelope: ``> 0``.
    feedforward_sec: float = 1.5
    #: Continuous time inside the governor deadband that releases Stage 2 to
    #: translate, in seconds.
    #:
    #: Safe envelope: ``> 0``.
    departure_settle_window_sec: float = 1.0
    #: Ceiling on that wait, in seconds. Reaching it is not a fault.
    #:
    #: Safe envelope: ``> 0``.
    departure_settle_timeout_sec: float = 8.0


@dataclass
class TranslationHoldConfig:
    """Retention of the axis orthogonal to a translational leg (``controllers.cross_axis_hold``).

    With ``pitch = 0`` commanded on a lateral leg the firmware does not hold
    position on the forward axis: wind, thrust asymmetry and trim error become
    a diagonal drift. While the main axis is commanded, the orthogonal axis
    receives ``-(kp_velocity * v + ki * integral(v))`` of its own measured body
    velocity, saturated at ``max_correction``. Starting points calibrated on a
    first-order plant (D14); the field values come from check F-E.
    """

    enabled: bool = True
    #: Normalized correction per m/s of orthogonal velocity.
    kp_velocity: float = 0.4
    #: Normalized correction per metre of integrated orthogonal displacement.
    #: 1.0 rather than the 0.1 first proposed: at 0.1 a constant 0.02 m/s
    #: crosswind still leaves 63 % of the uncorrected 5 s drift, against the
    #: 25 % acceptance; at 1.0 it leaves 21 % (``test_cross_axis_hold``).
    ki: float = 1.0
    #: Correction ceiling, normalized: three quanta of the driver's 0.01.
    max_correction: float = 0.03
    #: Main-axis command below which the law is inactive (hover holds itself).
    min_active_speed: float = 0.03
    #: Heading hold on the lateral cruise. Off: a yaw rate corrupts the
    #: optical-flow odometry; enable only after check F-E measures heading drift.
    heading_hold_enabled: bool = False
    #: Normalized yaw rate per radian of heading error.
    heading_kp: float = 0.6
    #: Yaw ceiling of the heading hold, normalized.
    heading_max_vyaw: float = 0.04
    #: Heading error inside which no yaw is commanded, degrees.
    heading_deadband_deg: float = 1.5


@dataclass
class NudgeConfig:
    """Operator keyboard nudge (``actuators.nudge_overlay``), normalized to the driver's ``[-1, 1]``.

    An additive overlay on the autonomous command with its own ceilings, ramps
    and deadman; it never interrupts the mission. Active in Stages 2 to 5 after
    the takeoff completes, suspended during the precision landing and in any
    emergency; Stage 4 per ``allow_in_inspection``.
    """

    enabled: bool = True
    #: Ceiling of the horizontal nudge, as a vector.
    max_horizontal: float = 0.08
    #: Ceiling of the yaw nudge.
    max_vyaw: float = 0.15
    #: Ramp from zero to the ceiling, seconds.
    attack_sec: float = 0.25
    #: Ramp from the ceiling to zero, seconds.
    release_sec: float = 0.15
    #: Silence from the station after which the intent is taken as released.
    deadman_sec: float = 0.35
    #: Nudge allowed during the Stage 4 inspection; the capture still waits
    #: for the keys to be released.
    allow_in_inspection: bool = True
    #: Nudge allowed during the precision landing (centering, descent, touchdown).
    allow_in_precision_landing: bool = False
    #: Heading the nudge may accumulate in one direction, degrees.
    yaw_budget_deg: float = 45.0


@dataclass
class YawPatternConfig:
    """Yaw laws of the Simple mission profile (``mission_mode == "simple"``).

    Yaw rate is a normalized command in [-1, 1]; a positive value turns left
    (counter-clockwise seen from above). One unit is about
    ``DeadReckoningConfig.yaw_rate_per_unit_rad_s`` rad/s on this airframe.
    The values below are conservative starting points to be tuned on the bench;
    none of them is an operator parameter.
    """

    #: Yaw command of the Stage 2 scan, normalized. 0.08 is roughly 8 deg/s:
    #: slow and subtle so the detector sees every bearing calmly without
    #: jerking or overshooting the target.
    scan_vyaw: float = 0.08
    #: Slew limit of the scan command, normalized units per second. Bounds the
    #: angular acceleration so the camera does not smear on the start and stop.
    scan_ramp_per_sec: float = 0.30
    #: Ceiling of the yaw authority window, normalized. Must be at least every
    #: other yaw magnitude in this section.
    max_vyaw: float = 0.30
    #: Stage 3 yaw PID on the normalized horizontal pixel error.
    kp: float = 0.14
    ki: float = 0.0
    kd: float = 0.02
    #: Output saturation of the Stage 3 PID, normalized. Caps fine centering adjustments.
    pid_output_limit: float = 0.055
    #: Output deadband of the Stage 3 PID, normalized.
    deadband: float = 0.01
    #: Smallest yaw command the airframe reproduces; sub-floor demands are
    #: duty-cycled by ``QuantizedCommandShaper`` rather than raised to the floor.
    min_effective_vyaw: float = 0.02
    #: Driver quantization step (``int8(v * 100)``).
    quantization_step: float = 0.01
    #: Rate at which Stage 3 searches toward the side the target left from.
    reacquire_vyaw: float = 0.050
    #: Heading restore (end of Stage 4): proportional and derivative gains on
    #: the wrapped heading error in radians.
    return_kp: float = 0.50
    return_kd: float = 0.02
    #: Output saturation of the heading restore, normalized.
    return_max_vyaw: float = 0.25
    #: Heading error inside which the restore counts as settled, degrees.
    return_tolerance_deg: float = 4.0
    #: Consecutive in-tolerance cycles required to end the restore.
    return_settle_cycles: int = 4
    #: Window of the heading restore, seconds. On expiry the mission continues
    #: with the heading reached.
    return_timeout_sec: float = 12.0


@dataclass
class MissionParameters:
    """Root container consolidating all configurable mission subsystems.

    ``mission_mode`` selects the mission profile: ``simple`` or ``advanced``.
    Resolved once by ``mission_mode.apply_mission_mode``; the removed
    ``intermediate`` is migrated on load (:func:`migrate_legacy_mission_mode`).
    """

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
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    platform: PlatformConfig = field(default_factory=PlatformConfig)
    mission_mode: str = "advanced"
    yaw_pattern: YawPatternConfig = field(default_factory=YawPatternConfig)
    translation_hold: TranslationHoldConfig = field(default_factory=TranslationHoldConfig)
    nudge: NudgeConfig = field(default_factory=NudgeConfig)
    no_fly: bool = False
    output_dir: str = "."

    @property
    def relative_target_altitude_m(self) -> float:
        """Operating height in the control frame, above the launch point, in metres.

        ``kinematics.target_altitude_m`` is stated over the floor;
        ``relative_altitude`` is measured from the ground reference calibrated
        where the airframe rested before takeoff, which on a pad is its top.
        Not a dataclass field, so the on-disk layout is unchanged.

        Returns
        -------
        float
            ``target_altitude_m - platform.height_m``; exactly
            ``target_altitude_m`` when no pad is configured or the flight
            takes off from the floor (``platform.takeoff_from_ground``).

        Raises
        ------
        ValueError
            With a pad configured, if the height left above it is under
            ``kinematics.takeoff_settle_min_altitude_m``: no hover sample there
            counts as airborne. Without a pad the configured target is returned
            unchecked, low targets included, exactly as before this section
            existed.
        """
        target = float(self.kinematics.target_altitude_m)
        height = 0.0 if self.platform.takeoff_from_ground is True else float(self.platform.height_m)
        if height <= 0.0:
            return target
        relative = target - height
        floor = float(self.kinematics.takeoff_settle_min_altitude_m)
        # 1e-9 m of slack: ``1.4 - 1.0`` is 0.39999999999999991 in binary, and
        # the station offers exactly ``pad + clearance`` as its lowest altitude.
        if relative < floor - 1e-9:
            raise ValueError(
                f"target_altitude_m={target:g} leaves {relative:g} m above a {height:g} m pad, "
                f"under takeoff_settle_min_altitude_m={floor:g}"
            )
        return relative

    @property
    def battery_land_pct(self) -> float:
        """Charge, in percent, at which the airborne mission returns to base.

        Read-only view of ``battery.land_pct``, the nested path the station
        writes. Not a dataclass field, so it never appears in :meth:`to_dict`
        and the on-disk layout carries the value exactly once.

        Raises
        ------
        TypeError
            If the configured value is not numeric.
        ValueError
            If it is non-finite or outside ``[0, 100]``.
        """
        from mvp_mission_bebop.telemetry.battery import normalize_percentage

        return normalize_percentage(self.battery.land_pct, "battery.land_pct")

    @classmethod
    def factory(cls) -> "MissionParameters":
        """The factory defaults: every field at its dataclass default.

        The single source of defaults for the ground station, served by
        ``mission.py --dump-defaults`` (``electron/main.cjs:readParameterDefaults``).
        """
        return cls()

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
        build, or a ``--params-json`` payload sent by an older GCS. A document of
        the removed Intermediate profile is migrated first
        (:func:`migrate_legacy_mission_mode`).

        Raises
        ------
        ParameterTypeError
            If a field of :data:`STRICT_BOOL_FIELDS` holds anything but a
            ``bool``. Checked over the whole document before any field is
            written, so a refused document leaves this instance untouched.
        """
        data = migrate_legacy_mission_mode(data)
        for section, name in STRICT_BOOL_FIELDS:
            values = data.get(section) if isinstance(data, dict) else None
            if isinstance(values, dict) and name in values and not isinstance(values[name], bool):
                raise ParameterTypeError(
                    f"{section}.{name}", f"precisa ser verdadeiro ou falso, recebeu {values[name]!r}"
                )

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
        """Load parameters from JSON on disk, falling back to defaults if missing or invalid.

        A document of the removed Intermediate profile is migrated
        (:func:`migrate_legacy_mission_mode`).

        Raises
        ------
        ParameterTypeError
            If a field of :data:`STRICT_BOOL_FIELDS` is not a ``bool``. Unlike
            an unreadable file, this is not replaced by the defaults: the store
            states a return plan or a launch surface, and flying the opposite of
            what it states is worse than refusing to start.
        """
        params = cls()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                params.update_from_dict(migrate_legacy_mission_mode(data))
            except ParameterTypeError:
                raise
            except Exception as exc:  # noqa: BLE001 - a bad config must not block flight
                logger.warning("Failed to load %s: %s. Using defaults.", file_path, exc)
        return params
