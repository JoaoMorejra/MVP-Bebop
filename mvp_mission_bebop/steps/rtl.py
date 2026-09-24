"""Stage 5: ArUco-guided return to launch and precision terminal landing.

The step owns sequencing only. Every control law lives in
:mod:`mvp_mission_bebop.controllers.rtl_guidance`, which is exercisable without
ROS, hardware, or a wall clock.

**What changed, and why.** The previous return leg flew the reverse of an
estimated displacement vector -- dead reckoning by preference, ``/bebop/odom``
otherwise -- until that estimate said the drone was home, and then landed there.
Both estimators integrate: one integrates the commands the mission sent, the
other integrates apparent motion across featureless tarmac. Integrators
accumulate, nothing on this airframe bounds the accumulation, and nothing
observes it either, so the aircraft landed wherever several minutes of
accumulated error happened to put it and reported success. A landing pad is a
metre across.

The return now closes on the pad itself. The launch base carries an ArUco marker
-- the same one Stage 1 took off over -- and that marker is an absolute, metric,
drift-free observation: its error is a property of the camera calibration, not
of how long the drone has been flying. Four phases run in order.

1. **Camera transition.** The gimbal drops to ``rtl.camera_tilt_deg`` (-80 deg by
   default), near-nadir but leaning far enough forward that the reverse cruise
   has ground ahead of it to search rather than only ground it has already
   overflown. The altitude-hold governor is engaged for everything that follows
   that is not the touchdown itself.
2. **Reverse cruise search.** The mirror of Stage 2: a straight, yaw-locked line
   flown *backward* at ``rtl.reverse_cruise_velocity`` while every frame is put
   through ``nectar.vision.Aruco``. The along-track command is saturated
   non-positive at the actuator boundary, so the search leg cannot become an
   outbound one; ``vyaw`` is pinned at zero, as everywhere else in this mission,
   because rotation is what corrupts the optical-flow estimate the altitude and
   failsafe logic still ride on. Confirmation is hysteretic: one frame carrying
   the target ID is not enough to commit the aircraft to a landing site.
3. **Closed-loop centering.** On confirmation the cruise brakes and a per-axis
   filtered PD closes on the marker's body-frame position, jerk-limited,
   saturated onto a centering speed ceiling, and rendered through the sigma-delta
   shaper so sub-quantum corrections survive the driver's ``int8(v * 100)``
   truncation. The landing is authorized only after the radial error has been
   inside tolerance *at residual speed* for several consecutive cycles.
4. **Touchdown.** Unchanged, and deliberately so. The burst ``land()``, the
   odometric confirmation, the descent-stall escalation and the assisted descent
   in :meth:`ClosedLoopRTLStep._touchdown` are the most thoroughly exercised code
   in this package and they are correct; the ArUco work happens upstream of them,
   which is the whole point -- the landing sequence does not need to know that
   the aircraft now arrives over the pad rather than near it.

**The degraded path.** ``nectar.vision.Aruco`` loads intrinsic calibration from
disk at construction and raises when it is not there, and a dictionary order
outside {4, 5, 6, 7} has no predefined OpenCV family. Either is a configuration
or deployment fault rather than a flight fault, and neither is a reason to
abandon a drone in the air. When the detector cannot be constructed the step
falls back to the legacy closed-loop odometry return -- :meth:`_navigate`,
:meth:`_station_keep` and the guidance law behind them, all retained intact --
and says so in the log. It lands less precisely. It lands.

**Failsafes.** The search window is ``rtl.timeout_sec`` and the centering window
is ``rtl.centering_timeout_sec``; both are separate on purpose, so a long search
cannot consume the budget for the convergence it exists to enable. Expiry of
either is not an abort: the aircraft descends where it stands, under the same
verified touchdown sequence, because a controlled landing at an imprecise
position is strictly better than the alternatives available to a battery.
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from typing import Any, Final, FrozenSet, Optional

import cv2
import numpy as np

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.rtl_guidance import (
    ArucoCenteringController,
    CenteringCommand,
    GuidanceCommand,
    MarkerObservation,
    ReturnReference,
    RTLGuidanceController,
    RTLPhase,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.detection_filter import HysteresisConfirmer
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, TelemetryHealth

logger = logging.getLogger("Step5RTL")

#: Publish an annotated telemetry frame every Nth cycle. Grabbing a frame blocks,
#: and the return leg needs its cadence more than it needs every frame.
_STREAM_DECIMATION: int = 3

#: Descent, as a multiple of the stall epsilon, that must have happened before a
#: halt in that descent may be read as touchdown. Guards against confirming a
#: landing on noise, or on the first centimetre of a descent that has only just
#: begun.
_MIN_CONFIRMED_DESCENT_RATIO: float = 4.0

#: Legacy ArUco dictionary orders that map to ``DICT_NxN_1000`` families.
#: The detector now also accepts AprilTag dictionaries and direct OpenCV enum
#: codes through ``nectar.vision.algorithms.markers.aruco.resolve_aruco_dict``,
#: so this set is kept only for the degraded-path guard that needs to reject a
#: truly invalid value (like ``3``) without importing the SDK.
_SUPPORTED_MARKER_DICTS: Final[FrozenSet[int]] = frozenset({4, 5, 6, 7})


def _resolve_marker_dict(marker_dict) -> Optional[int]:
    """Resolve a marker dictionary identifier to an OpenCV enum, or ``None``.

    Returns ``None`` on any input that cannot be resolved — an unsupported
    legacy order, a misspelled family name, or an enum code OpenCV does not
    recognise.  The caller treats ``None`` as a configuration fault and falls
    back to the legacy odometric return.
    """
    try:
        from nectar.vision.algorithms.markers.aruco import resolve_aruco_dict
        return resolve_aruco_dict(marker_dict)
    except Exception:  # noqa: BLE001
        return None

#: Frame-grab budget for the reverse cruise, in seconds. Matches Stage 2: the
#: search leg is tolerant of a slow frame because the aircraft is flying a
#: straight line and a missed cycle costs a few centimetres of track.
_SEARCH_FRAME_TIMEOUT_SEC: Final[float] = 1.0

#: How far the calibrated principal point may sit from the frame centre, as a
#: fraction of the frame half-width, before the return leg says so. A real
#: principal point is never exactly centred; a principal point that is *far* from
#: centre means the intrinsics were captured on a different sensor or a different
#: capture resolution than the one now feeding them, and every pose estimated
#: through them carries a fixed angular bias.
_PRINCIPAL_POINT_TOLERANCE_RATIO: Final[float] = 0.10

#: How often the return leg re-asserts the gimbal angle, in seconds.
#:
#: The tilt is not a cosmetic setting here: it is a term in the camera-to-body
#: projection, and the law has no way to measure it. If the single ``camera_control``
#: publish at engagement is dropped, the gimbal stays where Stage 4 left it --
#: ``gimbal.nadir_tilt_deg``, 11 degrees away -- and the centering law converges
#: on a point ``altitude * tan(11 deg)`` off the pad. At the 1.55 m cruise
#: altitude that is 0.30 m, seven times the centering tolerance, and the step
#: would report a confirmed centring and land there.
#:
#: Nothing on this airframe acknowledges a gimbal command, exactly as nothing
#: acknowledges ``land()`` -- and the landing is burst-asserted for that reason.
#: The same argument applies here, at the cost of one publish per second.
_TILT_REASSERT_SEC: Final[float] = 1.0

#: Frame-grab budget while centering, in seconds. Deliberately tighter. This is
#: a closed loop whose phase margin is set by its cadence, and a one-second
#: blocking grab is not a late frame -- it is a ``dt`` spike that the derivative
#: term and the sigma-delta accumulator both have to absorb, over a marker, at
#: low altitude. Better to treat the frame as missing and hold station.
_CENTERING_FRAME_TIMEOUT_SEC: Final[float] = 0.4


class _EmptyDetectionResult:
    """A detection result carrying no detections, for the marker HUD.

    ``MissionContext.publish_annotated_stream`` routes its frame through
    ``Detector.draw_detections``, whose first act is ``if not result.detections``
    (``nectar/ai/detection/core/base.py:539``). Passing ``None`` -- which reads
    like the natural way to say "no boxes to draw" and is what this step did --
    raises ``AttributeError`` there, on every single call.

    The consequence was not a crash, which is what made it worth finding: the
    exception was caught and logged at DEBUG, so Stage 5 published *no* annotated
    frame at all for the whole return leg. The operator watching the detection
    topic saw the last Stage 4 image, frozen, with no marker outline, no pose
    axes and no HUD, while the aircraft flew home -- and the log said nothing at
    a level anyone runs at.

    One object with an empty ``detections`` satisfies the guard, ``draw_detections``
    returns the frame unchanged, and the crosshair, the status band and the ROS
    publish downstream of it all run as intended.
    """

    __slots__ = ()

    #: Empty, and a tuple rather than a list so it cannot be appended to by
    #: anything that mistakes this for a real result.
    detections: Final[tuple] = ()


#: Shared instance. It is immutable and stateless, so one is enough.
_NO_DETECTIONS: Final[_EmptyDetectionResult] = _EmptyDetectionResult()


class _PhaseOutcome(Enum):
    """How one closed-loop phase of the return ended.

    The return leg has two phases that can each end four ways, and three of
    those ways are not "it worked". Collapsing them into a boolean is what makes
    a step swallow an emergency stop, so they are named.
    """

    #: The phase met its objective: the marker was confirmed, or the airframe
    #: settled over it.
    ACHIEVED = "achieved"
    #: The window expired first. Not a fault -- the aircraft still lands.
    EXHAUSTED = "exhausted"
    #: The mission-wide emergency event was raised.
    ABORTED = "aborted"
    #: A telemetry or envelope fault was detected; an emergency landing has
    #: already been commanded by the failsafe supervisor.
    FAILED = "failed"


class ArucoMarkerSensor:
    """Failure-tolerant adapter over ``nectar.vision.Aruco``.

    Exists to keep four concerns out of the flight loops: the SDK's call shape,
    the identity check, the fact that a perception library must never be able to
    end a mission by raising -- and one defect in the SDK that would otherwise
    make this entire stage a no-op.

    **The pose backend.** ``Aruco.pose_estimate`` calls
    ``cv2.aruco.estimatePoseSingleMarkers``, which OpenCV deprecated in 4.7 and
    *removed* in 4.10. Against the OpenCV this workspace ships (4.11) that call
    raises ``AttributeError`` on every frame, so the SDK's pose path returns a
    pose for no image at all. Absorbed as "no observation", which is what a
    perception adapter must do with an exception, the symptom in flight would be
    a return leg that searches for the full window, never sees a marker that is
    plainly in frame, and lands wherever it happened to be -- with nothing in the
    log but a timeout.

    So the backend is chosen once, at construction, from what OpenCV actually
    provides:

    ``"sdk"``
        ``estimatePoseSingleMarkers`` exists. ``Aruco.pose_estimate`` is called
        exactly as documented, and this class is a filter over its result.
    ``"solvepnp"``
        It does not. Detection, the dictionary, the intrinsics, the tag size and
        the yaw computation all still come from the SDK object -- only the one
        deleted call is replaced, by the substitution OpenCV's own migration
        notes prescribe: ``cv2.solvePnP`` with ``SOLVEPNP_IPPE_SQUARE`` over the
        canonical planar square, which is what ``estimatePoseSingleMarkers``
        computed internally. The returned translation is the same quantity in
        the same frame.

    **On the SDK's contract.** ``Aruco.pose_estimate`` returns ``(id, tvec,
    yaw)``, and ``Aruco.detect`` beneath it reduces the detector's ``ids`` array
    to ``ids[0][0]`` while ``pose_estimate`` reads ``tvecs[0]``. Only the *first*
    marker in the detector's output is therefore ever reported, whichever marker
    that happens to be. The consequence is operational rather than theoretical:
    a frame containing the landing pad **and** another marker from the same
    dictionary may report the other one, and this class will then reject the
    frame as carrying no target. That is the correct conservative behaviour --
    the cruise keeps searching, the hysteresis filter is unaffected by an
    isolated rejection, and the alternative would be to land on whichever marker
    the detector happened to order first. Do not place a second marker from the
    configured dictionary near the pad.

    **On exceptions.** Detection runs on frames from a lossy H.264 link.
    ``cvtColor`` on a truncated buffer, a pose solve on a degenerate corner set,
    a NumPy shape OpenCV did not expect -- each raises, and none of them is a
    reason to abandon an aircraft. Every failure is reported as "no observation
    this cycle", which the control law already knows how to fly.
    """

    __slots__ = ("_detector", "_target_id", "_annotate", "_failures", "_backend", "_object_points")

    #: Pose paths this class knows how to drive. See the class docstring.
    BACKENDS: Final[FrozenSet[str]] = frozenset({"sdk", "solvepnp"})

    def __init__(
        self,
        detector: Any,
        target_id: int,
        *,
        annotate: bool = True,
        backend: Optional[str] = None,
    ) -> None:
        self._detector = detector
        self._target_id = int(target_id)
        #: Draw the marker outline and pose axes onto the frame in place. The
        #: annotated frame is what the operator sees on the detection topic, and
        #: it is the only evidence available in flight that the pad was
        #: recognised rather than merely reported.
        self._annotate = bool(annotate)
        self._failures = 0

        # ``None`` -- the flight default -- selects from what the runtime
        # actually provides. An explicit value is for diagnostics and for tests
        # that need to exercise the path this machine's OpenCV would not choose;
        # it is validated rather than trusted, because a typo here would
        # silently disable marker detection for the whole return leg.
        if backend is None:
            resolved = "sdk" if hasattr(cv2.aruco, "estimatePoseSingleMarkers") else "solvepnp"
        elif backend in self.BACKENDS:
            resolved = backend
        else:
            raise ValueError(
                f"backend must be one of {sorted(self.BACKENDS)} or None, got {backend!r}"
            )
        self._backend = resolved
        self._object_points = self._square_model(getattr(detector, "tag_size", 0.0))

        if self._backend == "solvepnp" and backend is None:
            logger.warning(
                "OpenCV %s does not provide cv2.aruco.estimatePoseSingleMarkers (removed in "
                "4.10), so nectar.vision.Aruco.pose_estimate cannot run. Estimating the marker "
                "pose with cv2.solvePnP(SOLVEPNP_IPPE_SQUARE) over the SDK's own intrinsics and "
                "tag size, which is the documented equivalent.",
                cv2.__version__,
            )

    # ------------------------------------------------------------- properties

    @property
    def target_id(self) -> int:
        """Marker identity this sensor accepts, and only this one."""
        return self._target_id

    @property
    def backend(self) -> str:
        """Which pose path is in use: ``"sdk"`` or ``"solvepnp"``."""
        return self._backend

    @property
    def failures(self) -> int:
        """Detector exceptions absorbed so far, for the post-phase log."""
        return self._failures

    # ------------------------------------------------------------------ cycle

    def observe(self, frame: Optional[np.ndarray]) -> Optional[MarkerObservation]:
        """Run one detection, returning a validated sighting or ``None``.

        Parameters
        ----------
        frame : Optional[np.ndarray]
            BGR image from :meth:`MissionContext.grab_frame`. ``None`` -- no
            frame this cycle -- is answered with ``None``, so the caller has one
            code path for "no image" and "no marker".
        """
        if frame is None:
            return None

        try:
            if self._backend == "sdk":
                marker_id, translation, yaw = self._detector.pose_estimate(
                    frame, draw=self._annotate
                )
            else:
                marker_id, translation, yaw = self._pose_via_solvepnp(frame)
        except Exception as exc:  # noqa: BLE001 - perception never ends a flight
            self._failures += 1
            logger.debug("ArUco detection failed on this frame: %s", exc)
            return None

        return MarkerObservation.from_pose_estimate(
            marker_id, translation, yaw, expected_id=self._target_id
        )

    # ---------------------------------------------------------------- helpers

    def _pose_via_solvepnp(self, frame: np.ndarray):
        """Reproduce ``Aruco.pose_estimate`` on an OpenCV that removed its call.

        Everything but the solve itself is still the SDK's: ``detect`` applies
        the configured dictionary and draws the outline, ``camera_matrix`` and
        ``camera_distortion`` are the intrinsics it loaded from disk,
        ``tag_size`` is the object scale, and ``calculateYawFromCorners`` is its
        own planar-yaw computation. The first detected marker is used, matching
        the SDK's ``tvecs[0]`` exactly.
        """
        bbox, marker_id = self._detector.detect(frame, self._annotate)
        if marker_id is None or bbox is None or len(bbox) == 0:
            return None, None, None

        image_points = np.asarray(bbox[0], dtype=np.float32).reshape(4, 2)
        solved, rotation, translation = cv2.solvePnP(
            self._object_points,
            image_points,
            np.asarray(self._detector.camera_matrix, dtype=np.float64),
            np.asarray(self._detector.camera_distortion, dtype=np.float64),
            flags=cv2.SOLVEPNP_IPPE_SQUARE,
        )
        if not solved:
            return None, None, None

        if self._annotate:
            cv2.drawFrameAxes(
                frame,
                np.asarray(self._detector.camera_matrix, dtype=np.float64),
                np.asarray(self._detector.camera_distortion, dtype=np.float64),
                rotation,
                translation,
                float(self._detector.tag_size),
            )

        return marker_id, np.asarray(translation, dtype=np.float64).reshape(3), (
            self._detector.calculateYawFromCorners(bbox)
        )

    @staticmethod
    def _square_model(tag_size: float) -> np.ndarray:
        """Object points of a marker lying in its own z=0 plane, centre at origin.

        The vertex order is the one ``detectMarkers`` returns -- top-left,
        top-right, bottom-right, bottom-left -- and it is the order
        ``estimatePoseSingleMarkers`` used. Permuting it yields a pose rotated by
        a multiple of 90 degrees about the marker normal, which leaves the
        translation correct and the yaw wrong; since this mission acts on the
        translation and never on the yaw, that would be an error nothing
        downstream could detect.
        """
        half = abs(float(tag_size)) / 2.0
        return np.array(
            [[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]],
            dtype=np.float32,
        )


class ClosedLoopRTLStep(BaseStep):
    """ArUco-guided return to launch with closed-loop precision landing.

    Parameters
    ----------
    guidance : Optional[RTLGuidanceController]
        Legacy odometric return law, used only on the degraded path. Injected
        in tests; constructed from the mission parameters otherwise.
    centering : Optional[ArucoCenteringController]
        Marker centering law for phase 3, likewise injectable.
    sensor : Optional[ArucoMarkerSensor]
        Marker sensor. Injecting one bypasses SDK construction entirely, which
        is what lets the whole return leg be flown against a synthetic marker in
        a unit test with no camera, no calibration files and no ROS.
    """

    def __init__(
        self,
        guidance: Optional[RTLGuidanceController] = None,
        centering: Optional[ArucoCenteringController] = None,
        sensor: Optional[ArucoMarkerSensor] = None,
    ) -> None:
        super().__init__("STEP 5: ArUco RTL & Precision Landing")
        self._guidance = guidance
        self._centering = centering
        self._sensor = sensor
        self._stream_failed = False

    # ------------------------------------------------------------------ entry

    def execute(self, ctx: MissionContext) -> StepStatus:
        """Fly the four phases of the marker-guided return, in order.

        Every *nominal* exit lands the aircraft through :meth:`_touchdown`: a
        search that finds nothing, a centering that runs out of window, a
        detector that could not be built. None of those is a reason to stay in
        the air, so none of them returns without a verified landing.

        The two abnormal exits deliberately do not run that sequence.
        ``ABORTED`` follows the mission-wide emergency event, whose signal
        handler has already transmitted its own landing; ``FAILURE`` follows a
        telemetry or envelope fault, on which the failsafe supervisor has already
        commanded an emergency landing. Running the verified touchdown on top of
        either would mean this step competing for the airframe with the mechanism
        that just took it away. Both halt translation on the way out.
        """
        logger.info("--- [%s] ---", self.name)

        rtl_cfg = ctx.params.rtl
        guidance = self._guidance or RTLGuidanceController(
            rtl_cfg, ctx.params.kinematics, ctx.speed_calibration, ctx.params.governor
        )
        guidance.reset()
        ctx.speed_calibration.warn_if_uncalibrated("RTL guidance")

        # The centering law is constructed here, before anything flies, and not
        # at the point it is first needed. Its constructor validates the
        # configuration -- a zero speed ceiling, a quantization floor below one
        # actuator step, a non-positive acceleration or jerk limit -- and raises
        # on each. Built lazily after phase 2, that raise would arrive with the
        # aircraft airborne, over the pad, having already flown the whole search
        # leg: the step would leave through the runner's generic handler and get
        # a single unacknowledged land() publish instead of the burst-asserted,
        # stall-escalating, odometrically confirmed touchdown sequence that this
        # module exists to end every flight with. A configuration fault must be
        # discovered before the manoeuvre, not during it.
        centering = self._build_centering(ctx)

        # ---- phase 1: attitude transition -------------------------------
        self._announce(
            "Iniciando Retorno à Base via ArUco",
            "iniciando retorno à base de lançamento por marcador ArUco",
        )
        self._engage_return_attitude(ctx)

        sensor = None if centering is None else (self._sensor or self._build_sensor(ctx))
        if sensor is None:
            # Not a flight fault. See the module docstring: the legacy odometric
            # return is retained precisely so that a missing calibration file
            # degrades the landing precision rather than the landing.
            logger.warning(
                "Marker-guided return unavailable; falling back to the closed-loop odometry "
                "return. The landing will be as precise as the position estimate allows, "
                "which is metres rather than centimetres."
            )
            return self._legacy_return(ctx, guidance)

        logger.info(
            "RTL/ArUco engaged. Marker ID %d, dictionary %s, tag %.3f m, "
            "gimbal %.1f deg, reverse cruise %.3f normalized (%.3f m/s), search window "
            "%.1f s, centering window %.1f s.",
            rtl_cfg.target_aruco_id,
            rtl_cfg.marker_dict,
            rtl_cfg.tag_size,
            rtl_cfg.camera_tilt_deg,
            rtl_cfg.reverse_cruise_velocity,
            ctx.speed_calibration.to_mps(rtl_cfg.reverse_cruise_velocity),
            rtl_cfg.timeout_sec,
            rtl_cfg.centering_timeout_sec,
        )
        logger.info(
            "Invariants: vyaw == 0.0 (locked everywhere), vx <= 0.0 (search leg is "
            "backward-only), centering bounded at %.3f normalized.",
            rtl_cfg.max_centering_speed,
        )

        # ---- phase 2: reverse cruise search -----------------------------
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            outcome = self._reverse_search(ctx, sensor, Deadline(rtl_cfg.timeout_sec))

        if outcome is _PhaseOutcome.ABORTED:
            return StepStatus.ABORTED
        if outcome is _PhaseOutcome.FAILED:
            return StepStatus.FAILURE
        if outcome is _PhaseOutcome.EXHAUSTED:
            logger.warning(
                "Marker %d was not sighted within the %.1f s search window. Executing a "
                "safe landing at the present position.",
                rtl_cfg.target_aruco_id,
                rtl_cfg.timeout_sec,
            )
            self._announce(
                "Marcador não localizado",
                "marcador da base não localizado, executando pouso seguro no local",
            )
            return self._touchdown(ctx, guidance)

        # ---- phase 3: closed-loop centering -----------------------------
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            outcome = self._center_over_marker(ctx, centering, sensor)

        if outcome is _PhaseOutcome.ABORTED:
            return StepStatus.ABORTED
        if outcome is _PhaseOutcome.FAILED:
            return StepStatus.FAILURE
        if outcome is _PhaseOutcome.EXHAUSTED:
            # Land anyway, and say so. The aircraft is over or near the pad with
            # a known residual error; holding it up waiting for a convergence
            # that has already had its window spends battery to buy nothing.
            logger.warning(
                "Centering window of %.1f s expired with a residual radial error. "
                "Landing from the position reached.",
                rtl_cfg.centering_timeout_sec,
            )
        else:
            self._announce(
                "Centralizado sobre a base",
                "aeronave centralizada sobre o marcador, iniciando pouso de precisão",
            )

        # ---- phase 4: precision touchdown -------------------------------
        return self._touchdown(ctx, guidance)

    # -------------------------------------------------------- phase 1: setup

    def _engage_return_attitude(self, ctx: MissionContext) -> None:
        """Drop the gimbal to the marker-search attitude.

        ``ctx.current_tilt_deg`` is updated alongside the command because it is
        the mission's record of where the camera is pointing -- the forensic
        metadata sidecar reads it, and so does every HUD overlay. Commanding the
        gimbal without updating it leaves the rest of the mission describing an
        attitude the camera left several seconds ago.
        """
        tilt = float(ctx.params.rtl.camera_tilt_deg)
        logger.info(
            "Pitching the gimbal to %.1f deg for the marker search (re-asserted every %.1f s "
            "while the return leg runs).",
            tilt,
            _TILT_REASSERT_SEC,
        )
        self._assert_tilt(ctx)

    @staticmethod
    def _assert_tilt(ctx: MissionContext) -> None:
        """Command the return-leg gimbal angle, absorbing any fault.

        Called repeatedly, on purpose. See :data:`_TILT_REASSERT_SEC`: the tilt
        is a term in the camera-to-body projection that the control law cannot
        measure, nothing on this airframe acknowledges a gimbal command, and
        Stage 4 leaves the mount eleven degrees away from where this stage needs
        it. A single dropped publish therefore produces a centering solution that
        converges confidently onto a point 0.30 m from the pad, and reports it as
        centred.

        A gimbal fault is still not a reason to abandon a return leg, so the
        exception is absorbed -- but it is logged at warning level, because a
        camera that will not move is a landing that will not be precise.
        """
        tilt = float(ctx.params.rtl.camera_tilt_deg)
        try:
            ctx.drone.camera_control(tilt=tilt, pan=0.0)
        except Exception as exc:  # noqa: BLE001 - a gimbal fault must not abort the return
            logger.warning("Gimbal command to %.1f deg failed: %s", tilt, exc)
        ctx.current_tilt_deg = tilt

    def _build_centering(self, ctx: MissionContext) -> Optional[ArucoCenteringController]:
        """Construct the centering law now, before anything flies.

        Built at step entry rather than at the point it is first needed, and the
        difference is when a configuration fault is discovered. The constructor
        validates the whole centering envelope -- a zero speed ceiling, a
        quantization floor below one actuator step, a non-positive acceleration
        or jerk limit, degenerate controller limits -- and raises on each.

        Constructed lazily after the search, that raise would arrive with the
        aircraft airborne and over the pad, having already flown the entire
        return leg. The step would leave through the runner's generic exception
        handler and receive one unacknowledged ``land()`` publish on a
        best-effort, depth-one topic, instead of the burst-asserted,
        stall-escalating, odometrically confirmed touchdown this module exists to
        end every flight with.

        Returning ``None`` rather than propagating is the same judgement
        :meth:`_build_sensor` makes: an operator who mistyped a gain in the
        parameter sheet should get a less precise return leg, not an aircraft
        that will not come home.
        """
        if self._centering is not None:
            self._centering.reset()
            return self._centering
        try:
            centering = ArucoCenteringController(
                ctx.params.rtl, ctx.params.kinematics, ctx.speed_calibration
            )
        except Exception as exc:  # noqa: BLE001 - degrade, never abort
            logger.error(
                "The ArUco centering law could not be configured (%s: %s). The marker-guided "
                "return is unavailable; check the rtl centering parameters.",
                type(exc).__name__,
                exc,
            )
            return None
        centering.reset()
        return centering

    def _build_sensor(self, ctx: MissionContext) -> Optional[ArucoMarkerSensor]:
        """Construct the Nectar SDK marker detector, or return ``None``.

        Three things can go wrong and all three are configuration or deployment
        faults rather than flight faults, so each is reported precisely and none
        of them raises:

        * an unsupported dictionary identifier, which would surface inside the
          SDK as a :class:`ValueError` or :class:`AttributeError`;
        * a non-positive tag size, which is the scale factor of the whole pose
          estimate and would make every reported distance meaningless;
        * missing intrinsic calibration. ``Aruco.__init__`` calls
          ``CameraCalibration.load_calibration()``, which raises
          :class:`FileNotFoundError` when ``camera_matrix.txt`` and
          ``camera_distortion.txt`` are not on disk. Without intrinsics there is
          no pose to estimate, only pixels.
        """
        rtl_cfg = ctx.params.rtl

        resolved = _resolve_marker_dict(rtl_cfg.marker_dict)
        if resolved is None:
            logger.error(
                "rtl.marker_dict=%r cannot be resolved to a known ArUco/AprilTag "
                "dictionary; supported values include legacy orders {4, 5, 6, 7}, "
                "OpenCV enum codes, or string names like 'DICT_APRILTAG_36h11'.",
                rtl_cfg.marker_dict,
            )
            return None

        tag_size = float(rtl_cfg.tag_size)
        if not math.isfinite(tag_size) or tag_size <= 0.0:
            logger.error(
                "rtl.tag_size=%r is not a positive length; the pose estimate has no scale.",
                rtl_cfg.tag_size,
            )
            return None

        try:
            from nectar.vision import Aruco

            detector = Aruco(marker_dict=rtl_cfg.marker_dict, tag_size=tag_size)
        except Exception as exc:  # noqa: BLE001 - degrade, never abort
            logger.error(
                "ArUco detector construction failed (%s: %s). Camera intrinsics are loaded "
                "from camera_matrix.txt and camera_distortion.txt in the Nectar calibration "
                "package; run the calibration node if they are absent.",
                type(exc).__name__,
                exc,
            )
            return None

        logger.info(
            "ArUco/AprilTag detector ready: dict %s (resolved enum %d), "
            "tag %.3f m, accepting only ID %d.",
            rtl_cfg.marker_dict,
            resolved,
            tag_size,
            int(rtl_cfg.target_aruco_id),
        )
        self._check_intrinsics(ctx, detector)
        return ArucoMarkerSensor(detector, int(rtl_cfg.target_aruco_id))

    @staticmethod
    def _check_intrinsics(ctx: MissionContext, detector: Any) -> None:
        """Warn when the intrinsics do not describe the camera now feeding them.

        A pose estimate is only as good as the calibration behind it, and the
        failure here is silent by construction: intrinsics from a different
        sensor -- or from the same sensor at a different capture resolution --
        still produce a smooth, confident, well-conditioned pose. It is simply
        offset, by a fixed angle, forever. The centering law then converges
        beautifully onto a point that is not the pad, and the only evidence is a
        landing that is consistently off in the same direction.

        The cheapest observable that catches it is the principal point. It sits
        near the centre of whatever image the calibration was captured from, so
        comparing it against the centre of the frames actually arriving tells us
        whether the two are the same camera. The check costs two subtractions and
        it is the difference between a biased landing nobody can explain and a
        warning in the flight log naming the bias in metres.

        A warning, not a refusal: an imprecise return is still a return, and the
        operator is better served by flying it with the number in front of them
        than by being handed an aircraft that will not come home.
        """
        try:
            matrix = detector.camera_matrix
            focal_x, focal_y = float(matrix[0][0]), float(matrix[1][1])
            principal_x, principal_y = float(matrix[0][2]), float(matrix[1][2])
        except Exception as exc:  # noqa: BLE001 - a diagnostic never blocks a flight
            logger.debug("Intrinsics could not be inspected: %s", exc)
            return

        width = float(getattr(ctx, "frame_width", 0.0) or 0.0)
        height = float(getattr(ctx, "frame_height", 0.0) or 0.0)
        if width <= 0.0 or height <= 0.0 or focal_x <= 0.0 or focal_y <= 0.0:
            return

        offset_x = principal_x - width / 2.0
        offset_y = principal_y - height / 2.0
        tolerance_x = _PRINCIPAL_POINT_TOLERANCE_RATIO * width / 2.0
        tolerance_y = _PRINCIPAL_POINT_TOLERANCE_RATIO * height / 2.0
        if abs(offset_x) <= tolerance_x and abs(offset_y) <= tolerance_y:
            return

        logger.warning(
            "Camera intrinsics do not match the live frame: principal point is "
            "(%.1f, %.1f) but the %dx%d stream is centred on (%.1f, %.1f). The pose "
            "estimate therefore carries a fixed bias of about (%+.3f, %+.3f) m per metre "
            "of range -- %.3f m lateral at 1 m -- which is larger than the %.3f m centering "
            "tolerance. The calibration in camera_matrix.txt was captured from a camera or "
            "a capture resolution other than this one; re-run the calibration node against "
            "the live stream before relying on the landing precision.",
            principal_x,
            principal_y,
            int(width),
            int(height),
            width / 2.0,
            height / 2.0,
            -offset_x / focal_x,
            -offset_y / focal_y,
            abs(offset_x) / focal_x,
            ctx.params.rtl.centering_tolerance_m,
        )

    # ------------------------------------------------- phase 2: reverse search

    def _reverse_search(
        self, ctx: MissionContext, sensor: ArucoMarkerSensor, deadline: Deadline
    ) -> _PhaseOutcome:
        """Cruise backward along the outbound track until the marker confirms.

        The kinematic invariants of this phase are the mirror of Stage 2's and
        are enforced structurally rather than asserted:

        * ``vx <= 0`` -- the shaped command is passed through ``min(0.0, vx)``
          before it reaches the envelope clamp, so neither a sign error in
          ``reverse_cruise_velocity`` nor a sigma-delta pulse of the wrong
          polarity can turn the return into an outbound cruise.
        * ``vy == 0`` -- the search leg does not correct laterally. It has
          nothing to correct *towards* until the marker is in frame, and a
          cross-track command with no reference is just a curved search track.
        * ``vyaw == 0`` -- as everywhere in this mission.
        * ``vz`` belongs to the altitude governor, inside the hold window the
          caller opened.

        Confirmation is hysteretic over ``rtl.confirmation_frames``, released
        after ``rtl.lost_frames_tolerance`` consecutive misses. One frame
        carrying the right ID is not a landing site.
        """
        rtl_cfg = ctx.params.rtl
        kinematics_cfg = ctx.params.kinematics

        rate = LoopRate(kinematics_cfg.control_loop_hz)
        cruise_mps = abs(ctx.speed_calibration.to_mps(rtl_cfg.reverse_cruise_velocity))
        profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(cruise_mps, 1e-3),
                max_accel=rtl_cfg.max_accel_mps2,
                max_jerk=rtl_cfg.max_jerk_mps3,
            )
        )
        shaper = QuantizedCommandShaper(rtl_cfg.min_effective_speed, rtl_cfg.quantization_step)
        confirmer = HysteresisConfirmer(
            max(1, int(rtl_cfg.confirmation_frames)),
            max(1, int(rtl_cfg.lost_frames_tolerance)),
        )
        ctx.failsafe.notify_frame_received()

        logger.info(
            "Reverse cruise search engaged: %.3f m/s backward, confirmation over %d frames, "
            "window %.1f s.",
            cruise_mps,
            max(1, int(rtl_cfg.confirmation_frames)),
            deadline.duration_sec,
        )

        cycle = 0
        since_tilt = 0.0
        while deadline.active:
            if ctx.interrupted():
                self._brake(ctx, profile, shaper, rate)
                return _PhaseOutcome.ABORTED

            health = self._check_health(ctx)
            if health is not None:
                ctx.failsafe.trigger_emergency_land(health)
                return _PhaseOutcome.FAILED

            frame = self._acquire_frame(ctx, _SEARCH_FRAME_TIMEOUT_SEC)
            dt = rate.tick()
            if frame is not None:
                ctx.failsafe.notify_frame_received()

            since_tilt += dt
            if since_tilt >= _TILT_REASSERT_SEC:
                since_tilt = 0.0
                self._assert_tilt(ctx)

            observation = sensor.observe(frame)
            report = confirmer.update(observation is not None)

            if report.just_confirmed and observation is not None:
                logger.info(
                    "Marker %d confirmed after %d frames at %.2f m slant range "
                    "(t=%+.2f, %+.2f, %+.2f m in camera frame, planar yaw %s). "
                    "Braking the reverse cruise.",
                    observation.marker_id,
                    report.hits,
                    observation.slant_range_m,
                    observation.x_cam_m,
                    observation.y_cam_m,
                    observation.z_cam_m,
                    (
                        f"{observation.yaw_deg:.1f} deg"
                        if observation.yaw_deg is not None
                        else "unavailable"
                    ),
                )
                ctx.blackboard.rtl_marker_sighted = True
                self._announce(
                    "Base localizada",
                    "marcador da base localizado, iniciando centralização",
                )
                self._publish_marker_stream(
                    ctx,
                    frame,
                    self._hud(
                        ctx,
                        marker_id=observation.marker_id,
                        distance_m=observation.slant_range_m,
                        vx=0.0,
                        vy=0.0,
                        suffix=" | ACQUIRED",
                    ),
                )
                self._brake(ctx, profile, shaper, rate)
                return _PhaseOutcome.ACHIEVED

            # No confirmation yet: keep flying the reverse line. The airframe
            # latches its last Twist, so a cycle that transmits nothing is a
            # cycle that keeps the previous command -- there is no "do nothing"
            # branch available here.
            vx = self._cruise_backward(ctx, profile, shaper, dt, cruise_mps)

            if cycle % _STREAM_DECIMATION == 0:
                snapshot = ctx.odom_supervisor.snapshot()
                required = max(1, int(rtl_cfg.confirmation_frames))
                progress = (
                    f" | {report.hits}/{required}"
                    if observation is not None
                    else " | SEARCHING"
                )
                self._publish_marker_stream(
                    ctx,
                    frame,
                    self._hud(
                        ctx,
                        marker_id=observation.marker_id if observation else None,
                        distance_m=observation.slant_range_m if observation else None,
                        vx=vx,
                        vy=0.0,
                        suffix=(
                            f"{progress} | T: {deadline.elapsed_sec:.1f}/"
                            f"{deadline.duration_sec:.1f}s"
                        ),
                    ),
                )
                logger.info(
                    "Reverse search: %s | cmd vx=%.3f | alt=%.2f m | %.1f/%.1f s",
                    progress.lstrip(" |"),
                    vx,
                    snapshot.relative_altitude,
                    deadline.elapsed_sec,
                    deadline.duration_sec,
                )

            cycle += 1

        self._brake(ctx, profile, shaper, rate)
        if sensor.failures:
            logger.warning(
                "The detector raised on %d frames during the search; verify the video link.",
                sensor.failures,
            )
        return _PhaseOutcome.EXHAUSTED

    def _cruise_backward(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        cruise_mps: float,
    ) -> float:
        """Transmit one cycle of the reverse cruise, and return what was sent.

        The demand is scaled by the altitude loop's horizontal allowance *before*
        it is profiled, exactly as Stage 2 does and for the same reason: scaling
        the shaped command afterwards would leave the profile tracking a velocity
        the airframe never received, so the jerk limit would be measured against
        a fiction, and a demand scaled below the driver's quantization floor
        would truncate to a standstill instead of duty-cycling down to it.
        """
        scale = getattr(ctx.governor, "horizontal_scale", None)
        permitted = cruise_mps * (float(scale()) if callable(scale) else 1.0)

        profiled = profile.step(-permitted, dt)
        vx = shaper.shape(ctx.speed_calibration.to_normalized(profiled), dt)

        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(snapshot.relative_altitude, dt, vx_commanded=vx)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        # The backward-only invariant, enforced where it cannot be argued with.
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(min(0.0, vx), 0.0)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)
        return safe_vx

    def _brake(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        rate: LoopRate,
    ) -> None:
        """Ramp the reverse cruise down to rest, paced by the control loop.

        Pacing is the whole of it. Stepping the profile in a tight loop publishes
        the entire deceleration in under a millisecond onto a depth-1 queue, of
        which the airframe observes approximately the last message -- which is to
        say, the step command the profile exists to avoid. The marker was just
        confirmed and the centering law is about to close on it through the same
        camera, so a pitch transient here is a transient in the measurement the
        next phase depends on.
        """
        dt = rate.period_sec
        limits = profile.limits
        horizon = limits.max_velocity / limits.max_accel + limits.max_accel / limits.max_jerk
        for _ in range(max(1, int(horizon / max(dt, 1e-3)) + 2)):
            self._cruise_backward(ctx, profile, shaper, dt, 0.0)
            if abs(profile.velocity) <= 1e-6:
                break
            rate.tick()

    # ---------------------------------------------------- phase 3: centering

    def _center_over_marker(
        self,
        ctx: MissionContext,
        centering: ArucoCenteringController,
        sensor: ArucoMarkerSensor,
    ) -> _PhaseOutcome:
        """Close the loop on the marker until the landing gate is satisfied.

        The law is in
        :class:`~mvp_mission_bebop.controllers.rtl_guidance.ArucoCenteringController`;
        this method is sequencing, transmission and telemetry. Two properties
        are worth noting at this level rather than at the law's:

        *The vertical axis is not ours.* ``vz`` comes from the altitude governor
        every cycle and passes through the failsafe's hold window. Centering is a
        horizontal manoeuvre and the aircraft must hold its altitude while it
        converges -- descending onto a marker it has not finished centering on is
        how a precision landing becomes an ordinary one.

        *Losing the marker does not end the phase.* It holds station and keeps
        counting against the window, because the commonest cause is the airframe
        having drifted the pad to the edge of a near-nadir frame, and the
        commonest cure is the station-keeping that the loss itself commands.
        """
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.centering_timeout_sec)

        logger.info(
            "Centering on marker %d: tolerance %.3f m radial, %d settle cycles at <= %.3f m/s, "
            "ceiling %.3f m/s, window %.1f s.",
            sensor.target_id,
            centering.tolerance_m,
            max(1, int(rtl_cfg.centering_settle_cycles)),
            rtl_cfg.settle_max_speed_mps,
            centering.max_speed_mps,
            rtl_cfg.centering_timeout_sec,
        )

        # The projection the whole phase rests on assumes this angle. Assert it
        # once more on entry rather than trusting that the search leg's last
        # re-assertion landed.
        self._assert_tilt(ctx)

        cycle = 0
        since_tilt = 0.0
        command: Optional[CenteringCommand] = None
        while deadline.active:
            if ctx.interrupted():
                self._halt_translation(ctx)
                return _PhaseOutcome.ABORTED

            health = self._check_health(ctx)
            if health is not None:
                ctx.failsafe.trigger_emergency_land(health)
                return _PhaseOutcome.FAILED

            frame = self._acquire_frame(ctx, _CENTERING_FRAME_TIMEOUT_SEC)
            dt = rate.tick()
            if frame is not None:
                ctx.failsafe.notify_frame_received()

            since_tilt += dt
            if since_tilt >= _TILT_REASSERT_SEC:
                since_tilt = 0.0
                self._assert_tilt(ctx)

            observation = sensor.observe(frame)
            command = centering.update(observation, dt)
            self._transmit_centering(ctx, command, dt)

            if cycle % _STREAM_DECIMATION == 0:
                self._publish_centering_stream(ctx, frame, command, sensor.target_id)
                logger.info(
                    "Centering: err=(%+.3f, %+.3f) m, radial %.3f m | cmd=(vx=%.3f, vy=%.3f) "
                    "| settle %d/%d | %s",
                    command.ex_body_m,
                    command.ey_body_m,
                    command.radial_error_m,
                    command.vx,
                    command.vy,
                    centering.settle_cycles,
                    max(1, int(rtl_cfg.centering_settle_cycles)),
                    command.note,
                )

            if command.settled:
                logger.info(
                    "Centred over marker %d after %.1f s: radial error %.3f m at %.3f m/s. "
                    "Authorizing the terminal landing.",
                    sensor.target_id,
                    deadline.elapsed_sec,
                    command.radial_error_m,
                    command.commanded_speed_mps,
                )
                self._halt_translation(ctx)
                return _PhaseOutcome.ACHIEVED

            cycle += 1

        self._halt_translation(ctx)
        logger.warning(
            "Centering did not settle within %.1f s. Last radial error %s m, marker %s.",
            rtl_cfg.centering_timeout_sec,
            f"{command.radial_error_m:.3f}" if command is not None else "unknown",
            "in view" if command is not None and command.tracking else "not in view",
        )
        return _PhaseOutcome.EXHAUSTED

    def _transmit_centering(
        self, ctx: MissionContext, command: CenteringCommand, dt: float
    ) -> None:
        """Send one centering command through the full safety boundary.

        Saturate rather than raise, as everywhere else on the command path: a
        rounding error must not end a mission. ``vyaw`` is passed as an explicit
        literal zero and then forced to zero again by the supervisor, which is
        redundant on purpose -- the yaw invariant is the one that protects the
        odometry every other subsystem reads.
        """
        snapshot = ctx.odom_supervisor.snapshot()
        vz = ctx.governor.compute_vz(
            snapshot.relative_altitude, dt, vx_commanded=command.vx
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

    def _halt_translation(self, ctx: MissionContext) -> None:
        """Command a horizontal stop while leaving the vertical axis governed.

        Sent explicitly because the airframe latches: leaving the centering
        phase without a zero translation hands the touchdown sequence an
        aircraft still drifting under the last correction it was given.
        """
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(0.0, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, 0.0)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

    # ------------------------------------------------------- degraded return

    def _legacy_return(
        self, ctx: MissionContext, guidance: RTLGuidanceController
    ) -> StepStatus:
        """Fly the closed-loop odometric return, unchanged, then land.

        Reached only when no marker detector could be constructed. The law and
        both of its phases are exactly what they were before the ArUco work; the
        only difference is that arriving here is now an explicitly logged
        degradation rather than the nominal design.
        """
        # The operator is about to watch a return leg flown blind, so point the
        # camera where the aircraft is going rather than at the ground it is
        # passing over.
        try:
            ctx.drone.camera_control(tilt=ctx.params.gimbal.search_tilt_deg, pan=0.0)
            ctx.current_tilt_deg = ctx.params.gimbal.search_tilt_deg
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gimbal command failed on the degraded return path: %s", exc)

        rtl_cfg = ctx.params.rtl
        tracker = self._tracker(ctx)
        snapshot = ctx.odom_supervisor.snapshot()

        if tracker is None and not snapshot.has_launch_origin:
            # Neither estimator knows where home is. There is no return leg to
            # fly, and guessing one would be worse than not flying it: descend
            # where the drone stands.
            logger.error(
                "No launch origin was ever frozen and no motion sequence was aggregated; "
                "there is nothing to return to. Descending in place."
            )
            return self._touchdown(ctx, guidance)

        logger.info(
            "Odometric RTL engaged. Cruise %.3f m/s (%.3f normalized), braking accel "
            "%.2f m/s^2, jerk %.2f m/s^3, arrival radius %.2f m, window %.1f s.",
            guidance.cruise_speed_mps,
            rtl_cfg.max_speed,
            rtl_cfg.max_accel_mps2,
            rtl_cfg.max_jerk_mps3,
            rtl_cfg.arrival_radius_m,
            rtl_cfg.timeout_sec,
        )
        if tracker is not None:
            logger.info("Return vector from the aggregated motion sequence: %s", tracker.summary())
        else:
            logger.info(
                "Return vector from odometry: no aggregated motion sequence is available."
            )
        logger.info("Invariants: vyaw == 0.0 (locked), vx <= 0.0 (backward only).")

        status = self._navigate(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._station_keep(ctx, guidance)
        if status is not StepStatus.SUCCESS:
            return status

        return self._touchdown(ctx, guidance)

    # ------------------------------------------------------------- estimation

    @staticmethod
    def _tracker(ctx: MissionContext):
        """The dead-reckoning tracker to navigate on, or ``None``.

        ``None`` covers three distinct situations and all of them mean the same
        thing to the caller: dead reckoning was switched off in the
        configuration, no tracker was wired in, or one was wired in but never
        armed -- which is what a mission that failed before Stage 1 froze its
        origin looks like. In every case the aggregate has no ``(0, 0)`` to be
        relative to, and odometry is the only reference left.
        """
        if not ctx.params.rtl.use_dead_reckoning:
            return None
        tracker = getattr(ctx.drone, "motion_tracker", None)
        if tracker is None or not tracker.armed:
            return None
        return tracker

    def _reference(
        self, ctx: MissionContext, tracker, snapshot: OdometrySnapshot, *, log: bool
    ) -> ReturnReference:
        """Resolve where the origin is, and say so when the two sources differ.

        The disagreement is computed whether or not it is acted on. It is the
        only observable this mission produces that bears on which estimator to
        trust, and it costs a subtraction: a flight that lands two metres from
        the pad is otherwise a mystery, and with this line in the log it is a
        measurement of either the speed calibration or the optical flow.
        """
        odometric = ReturnReference.from_snapshot(snapshot)
        if tracker is None:
            return odometric

        state = tracker.state()
        ex, ey, distance = state.body_frame_origin_error()
        reckoned = ReturnReference(
            ex_body_m=ex,
            ey_body_m=ey,
            distance_m=distance,
            x_m=state.x_m,
            y_m=state.y_m,
            source="dead_reckoning",
        )

        if log and snapshot.has_launch_origin:
            disagreement = math.hypot(
                reckoned.ex_body_m - odometric.ex_body_m,
                reckoned.ey_body_m - odometric.ey_body_m,
            )
            level = (
                logging.WARNING
                if disagreement > ctx.params.rtl.dead_reckoning_disagreement_warn_m
                else logging.DEBUG
            )
            logger.log(
                level,
                "Return vector: dead reckoning (%+.2f, %+.2f) m vs odometry (%+.2f, %+.2f) m "
                "-- they disagree by %.2f m. Navigating on %s.",
                reckoned.ex_body_m,
                reckoned.ey_body_m,
                odometric.ex_body_m,
                odometric.ey_body_m,
                disagreement,
                reckoned.source,
            )
        return reckoned

    # ------------------------------------------------------------- navigation

    def _navigate(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Fly the reverse of the aggregated displacement vector back to (0, 0).

        The return leg translates, so it holds altitude two-sided like every
        other phase that does. The window closes when this method returns,
        whichever way it returns -- including into the touchdown sequence, which
        must not be able to climb under any circumstances.
        """
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            return self._navigate_home(ctx, guidance)

    def _navigate_home(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.timeout_sec)
        tracker = self._tracker(ctx)
        distance = float("inf")
        cycle = 0

        while deadline.active:
            if ctx.interrupted():
                return StepStatus.ABORTED

            health = self._check_health(ctx)
            if health is not None:
                ctx.failsafe.trigger_emergency_land(health)
                return StepStatus.FAILURE

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            reference = self._reference(
                ctx, tracker, snapshot, log=cycle % _STREAM_DECIMATION == 0
            )
            command = self._emit(
                ctx, guidance, snapshot, dt, RTLPhase.NAVIGATING, reference=reference
            )
            distance = command.distance_m

            if cycle % _STREAM_DECIMATION == 0:
                self._publish_telemetry(ctx, command, deadline.elapsed_sec)
                logger.info(
                    "RTL navigating on %s: dist=%.2f m (arrive <= %.2f m) | err=(%.2f, %.2f) m | "
                    "cmd=(vx=%.3f, vy=%.3f, vz=%.3f) normalized | v=%.3f m/s | alt=%.2f m | %s",
                    command.reference_source,
                    command.distance_m,
                    rtl_cfg.arrival_radius_m,
                    command.ex_body_m,
                    command.ey_body_m,
                    command.vx,
                    command.vy,
                    command.vz,
                    snapshot.horizontal_speed,
                    snapshot.relative_altitude,
                    command.note,
                )

            if command.arrived or command.ready_to_land:
                # Either criterion ends the return leg. Settlement is the
                # stronger evidence and is preferred when it is available, but
                # it must not be the only way out: its positional-sigma term
                # depends on odometry quality the Bebop does not guarantee at
                # hover, so waiting for it alone could keep a drone that is
                # already over the origin airborne until the window expired.
                logger.info(
                    "Return leg complete at %.2f m after %.1f s (%s). Proceeding to landing. %s",
                    command.distance_m,
                    deadline.elapsed_sec,
                    "settlement confirmed" if command.arrived else "landing commit",
                    guidance.settlement,
                )
                return StepStatus.SUCCESS

            cycle += 1

        logger.warning(
            "RTL window of %.1f s expired with %.2f m remaining. Proceeding to landing.",
            rtl_cfg.timeout_sec,
            distance,
        )
        return StepStatus.SUCCESS

    def _station_keep(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Hold position over the origin to dissipate residual kinetic energy."""
        with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
            return self._hold_over_origin(ctx, guidance)

    def _hold_over_origin(
        self, ctx: MissionContext, guidance: RTLGuidanceController
    ) -> StepStatus:
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.final_hover_delay_sec)
        tracker = self._tracker(ctx)

        logger.info(
            "Station keeping over the origin for %.1f s to damp residual motion...",
            rtl_cfg.final_hover_delay_sec,
        )

        command = None
        while deadline.active:
            if ctx.interrupted():
                return StepStatus.ABORTED

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            reference = self._reference(ctx, tracker, snapshot, log=False)
            command = self._emit(
                ctx, guidance, snapshot, dt, RTLPhase.STATION_KEEPING, reference=reference
            )

        snapshot = ctx.odom_supervisor.snapshot()
        logger.info(
            "Station keeping complete: residual speed %.3f m/s at %.2f m from origin (%s).",
            snapshot.horizontal_speed,
            command.distance_m if command is not None else snapshot.body_frame_launch_error()[2],
            command.reference_source if command is not None else "odometry",
        )
        return StepStatus.SUCCESS

    # --------------------------------------------------------------- terminal

    def _touchdown(self, ctx: MissionContext, guidance: RTLGuidanceController) -> StepStatus:
        """Land, and confirm touchdown from odometry.

        Motor disarm cannot be confirmed on this airframe. ``BebopDrone.land``
        publishes an ``Empty`` message and returns immediately with no
        acknowledgement (``nectar/control/bebop/drone.py:165``), and ``is_armed``
        falls through to the base class's ``None`` because the Bebop publishes no
        armed state on any topic. Odometric touchdown is the strongest
        confirmation this platform can supply, so that is what is asserted here
        rather than a disarm signal that does not exist.
        """
        rtl_cfg = ctx.params.rtl
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(rtl_cfg.touchdown_timeout_sec)

        logger.info(
            "Executing terminal landing at the launch origin (window %.1f s)...",
            rtl_cfg.touchdown_timeout_sec,
        )
        self._assert_land(ctx, rtl_cfg.land_burst_count)

        settled_cycles = 0
        stagnant_elapsed = 0.0
        required_cycles = max(2, rtl_cfg.settle_min_samples // 2)
        confirmed = False
        assisted = False

        start_altitude = ctx.odom_supervisor.snapshot().relative_altitude
        # Progress is tracked against the *lowest* altitude reached, not against
        # the previous sample. Comparing consecutive samples makes any descent
        # slower than one epsilon per cycle indistinguishable from no descent at
        # all, which is the opposite of what the check is for.
        lowest_altitude = start_altitude
        stall_elapsed = 0.0

        while deadline.active:
            if ctx.interrupted():
                logger.warning("Emergency raised during touchdown; continuing to command land.")
                ctx.drone.land()
                break

            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            altitude = snapshot.relative_altitude

            # --- primary: the airframe is low and still -----------------
            grounded = altitude <= rtl_cfg.touchdown_altitude_m
            still = snapshot.speed <= rtl_cfg.settle_max_speed_mps
            settled_cycles = settled_cycles + 1 if (grounded and still) else 0
            if settled_cycles >= required_cycles:
                confirmed = True
                break

            # --- fallback: the descent has stopped moving ---------------
            # The absolute threshold is measured against a ground reference
            # calibrated before launch. If that datum drifted in flight the
            # threshold may never be crossed even with the drone on the ground,
            # and the step would report an unconfirmed landing for a landing
            # that plainly happened. Descent stagnation under a commanded
            # landing is the platform-independent touchdown signature.
            if altitude < lowest_altitude - rtl_cfg.descent_stall_epsilon_m:
                lowest_altitude = altitude
                stagnant_elapsed = 0.0
                stall_elapsed = 0.0
            else:
                stagnant_elapsed += dt
                stall_elapsed += dt

            descended = start_altitude - altitude
            # A descent has to have actually happened, and have been substantial,
            # before its cessation means anything. Without this the counter
            # accumulated during the pre-descent stall and then confirmed
            # touchdown on the first centimetre of movement -- reporting the
            # drone as landed while it was still most of a metre up.
            meaningful = descended >= _MIN_CONFIRMED_DESCENT_RATIO * rtl_cfg.descent_stall_epsilon_m
            if stagnant_elapsed >= rtl_cfg.touchdown_stagnation_sec and still and meaningful:
                logger.info(
                    "Touchdown inferred from descent stagnation: %.2f m descended, then no "
                    "further progress for %.1f s at %.2f m.",
                    descended,
                    stagnant_elapsed,
                    altitude,
                )
                confirmed = True
                break

            # --- escalation: the landing request is not being honoured ---
            # land() publishes an Empty and returns; nothing acknowledges it.
            # If the altitude has not moved at all after descent_stall_sec, the
            # firmware is not acting on it, and re-sending the same request more
            # times will not change that. Command the descent explicitly, then
            # re-assert the landing.
            if not assisted and stall_elapsed >= rtl_cfg.descent_stall_sec and descended <= 0.0:
                assisted = True
                logger.warning(
                    "No descent %.1f s after the landing request (altitude %.2f m). "
                    "Commanding assisted descent at %.3f normalized.",
                    stall_elapsed,
                    altitude,
                    rtl_cfg.assisted_descent_speed,
                )
                # The stall counters describe the period before the escalation
                # and must not be carried into it, or the first sample of real
                # descent would satisfy a stagnation test built from them.
                stagnant_elapsed = 0.0
                stall_elapsed = 0.0
                lowest_altitude = altitude

            if assisted and not grounded:
                # Routed through the failsafe like every other command, so the
                # descent-only invariant and the horizontal envelope still hold.
                safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(
                    -abs(rtl_cfg.assisted_descent_speed), 0.0
                )
                safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, 0.0)
                ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)
            else:
                # The Bebop latches its last command, so keep asserting the
                # landing rather than assuming one publish was enough. No
                # velocity is sent on this path: a Twist arriving mid-sequence
                # is the one thing that can talk the firmware out of landing.
                ctx.drone.land()

        if assisted:
            # Whatever the assisted descent achieved, the airframe must end up
            # under the firmware's own landing logic rather than under a velocity
            # command that stops the moment this loop does.
            self._assert_land(ctx, rtl_cfg.land_burst_count)

        # Only a confirmed touchdown completes the RTL. This flag used to be set
        # unconditionally, so a drone still airborne at the timeout reported a
        # completed flight to everything downstream that reads the blackboard.
        ctx.blackboard.rtl_completed = confirmed
        final = ctx.odom_supervisor.snapshot()

        if confirmed:
            logger.info(
                "Touchdown confirmed: altitude %.2f m, speed %.3f m/s%s.",
                final.relative_altitude,
                final.speed,
                " (assisted descent was required)" if assisted else "",
            )
            self._announce("Pouso seguro concluído", "pouso seguro concluído na base de lançamento")
            return StepStatus.SUCCESS

        logger.warning(
            "Landing commanded for %.1f s without odometric confirmation (altitude %.2f m, "
            "descended %.2f m). The land command has been re-asserted throughout and is the "
            "last command sent.",
            rtl_cfg.touchdown_timeout_sec,
            final.relative_altitude,
            start_altitude - final.relative_altitude,
        )
        self._announce("Pouso concluído", "pouso concluído, confirmação odométrica indisponível")
        return StepStatus.SUCCESS

    @staticmethod
    def _assert_land(ctx: MissionContext, burst: int) -> None:
        """Zero the airframe, then request landing, repeatedly.

        The zero Twist goes first and the land request last, so the final thing
        on the wire is the landing. Reversing that order lets a velocity command
        land after the request and hold the firmware in piloting state.
        """
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        for _ in range(max(1, burst)):
            ctx.drone.land()

    # ---------------------------------------------------------------- helpers

    def _emit(
        self,
        ctx: MissionContext,
        guidance: RTLGuidanceController,
        snapshot: OdometrySnapshot,
        dt: float,
        phase: RTLPhase,
        reference: Optional[ReturnReference] = None,
    ) -> GuidanceCommand:
        """Compute one cycle of guidance and transmit it."""
        vz_governor = ctx.governor.compute_vz(snapshot.relative_altitude, dt)
        command = guidance.compute(snapshot, vz_governor, dt, phase=phase, reference=reference)

        # Saturate rather than raise: a rounding error must not end the mission.
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(command.vz, command.vyaw)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)
        return command

    def _check_health(self, ctx: MissionContext) -> Optional[str]:
        """Return a failure reason, or ``None`` when the system is nominal."""
        if ctx.drone.no_fly:
            return None

        health = ctx.odom_supervisor.telemetry_health()
        if health is TelemetryHealth.NEVER_RECEIVED:
            return "No odometry received during RTL. Verify /bebop/odom is publishing."
        if health is TelemetryHealth.STALE:
            return "Odometry telemetry loss during RTL."
        if ctx.odom_supervisor.is_ceiling_breached():
            return "Altitude ceiling breached during RTL."
        return None

    def _publish_telemetry(
        self, ctx: MissionContext, command: GuidanceCommand, elapsed_sec: float
    ) -> None:
        """Overlay guidance state onto the annotated camera stream."""
        if ctx.handler is None:
            return
        try:
            frame = ctx.grab_frame(timeout_sec=0.05)
            if frame is None:
                return
            ctx.failsafe.notify_frame_received()
            prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
            ctx.publish_annotated_stream(
                frame,
                _NO_DETECTIONS,
                f"{prefix}STEP 5: RTL ({elapsed_sec:.1f}s) | DIST: {command.distance_m:.2f}m "
                f"| VX: {command.vx:.3f} | ALT: {ctx.odom_supervisor.relative_altitude:.2f}m",
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break flight
            self._note_stream_failure(exc)

    def _publish_marker_stream(
        self, ctx: MissionContext, frame: Optional[np.ndarray], status_text: str
    ) -> None:
        """Publish an already-annotated frame with a HUD line.

        The frame handed in is the one the detector drew on: ``Aruco.detect``
        and ``Aruco.pose_estimate`` render the marker outline and the pose axes
        into the caller's buffer in place, so by the time this runs the marker
        is already marked up and only the status band is left to add. That is
        why this exists alongside :meth:`_publish_telemetry` rather than
        replacing it -- the legacy leg has no frame in hand and must grab one.

        An empty detection result is passed rather than ``None``: see
        :class:`_EmptyDetectionResult` for why ``None`` silently disabled this
        entire HUD.
        """
        if ctx.handler is None or frame is None:
            return
        try:
            ctx.publish_annotated_stream(frame, _NO_DETECTIONS, status_text)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break flight
            self._note_stream_failure(exc)

    def _note_stream_failure(self, exc: BaseException) -> None:
        """Report the first publication failure loudly, then stay quiet.

        A telemetry fault must not flood the log of a flight in progress, and it
        must not be invisible either. Logging every occurrence at DEBUG is how a
        HUD that never once published looked exactly like a HUD that was working.
        """
        if not self._stream_failed:
            self._stream_failed = True
            logger.warning(
                "The Stage 5 annotated stream could not be published (%s: %s). The return "
                "leg continues; the operator's video overlay will not update. Further "
                "occurrences are logged at debug level.",
                type(exc).__name__,
                exc,
            )
        else:
            logger.debug("Marker stream publication skipped: %s", exc)

    @staticmethod
    def _hud(
        ctx: MissionContext,
        *,
        marker_id: Optional[int],
        distance_m: Optional[float],
        vx: float,
        vy: float,
        suffix: str = "",
    ) -> str:
        """Compose the Stage 5 HUD line.

        One formatter for both phases. The search leg used to emit a different,
        shorter layout, which left an operator reading two different HUDs across
        a single stage and cost them the two fields that matter most while the
        aircraft is hunting for the pad: which marker is being accepted, and how
        far away the current sighting is.

        Unknowns render as ``--`` rather than as ``nan``. ``DIST: nanm`` is not a
        distance and not a placeholder; it reads as a fault.
        """
        identity = str(marker_id) if marker_id is not None else "--"
        distance = f"{distance_m:.2f}m" if distance_m is not None else "--"
        prefix = "[NO-FLY] " if ctx.drone.no_fly else ""
        return (
            f"{prefix}STEP 5: RTL ARUCO [ID: {identity}] | DIST: {distance} "
            f"| CMD: vx={vx:.3f}, vy={vy:.3f} "
            f"| ALT: {ctx.odom_supervisor.relative_altitude:.2f}m{suffix}"
        )

    def _publish_centering_stream(
        self,
        ctx: MissionContext,
        frame: Optional[np.ndarray],
        command: CenteringCommand,
        target_id: int,
    ) -> None:
        """Render the centering HUD line and publish it."""
        if frame is None:
            return
        self._publish_marker_stream(
            ctx,
            frame,
            self._hud(
                ctx,
                marker_id=command.marker_id if command.tracking else None,
                distance_m=command.slant_range_m if command.tracking else None,
                vx=command.vx,
                vy=command.vy,
                suffix=f" | ERR: {command.radial_error_m:.3f}m" if command.tracking else "",
            ),
        )

    def _acquire_frame(self, ctx: MissionContext, timeout_sec: float) -> Optional[np.ndarray]:
        """Grab one frame, treating any camera fault as a missing frame.

        :class:`ArucoMarkerSensor` already guarantees that detection cannot end a
        flight; this extends the same guarantee to the half of the perception
        path that precedes it. ``grab_frame`` reaches into ``ROSCam.get_frame``
        and ``ImageHandler.take_photo``, and a raise there -- a decode failure on
        a truncated H.264 buffer, a handler torn down under it -- would otherwise
        leave Stage 5 through the runner's generic exception handler and into an
        emergency landing, rather than through the verified touchdown sequence.

        A missing frame is a case both control loops already handle: the cruise
        keeps flying its line, the centering law holds station.
        """
        try:
            return ctx.grab_frame(timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001 - perception never ends a flight
            logger.debug("Frame acquisition failed: %s", exc)
            return None

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        """Dispatch a non-blocking acoustic cue, ignoring any failure."""
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
