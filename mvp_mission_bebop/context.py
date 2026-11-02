"""Execution context encapsulating shared flight resources and telemetry."""

from __future__ import annotations

import copy
import inspect
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, Final, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from std_msgs.msg import String

from nectar.vision import ImageHandler

if TYPE_CHECKING:
    # Annotation only: importing it loads torch (~2 s), which mission.py now
    # does on a background thread (_prepare_inference).
    from nectar.ai.detection import Detector

    from mvp_mission_bebop.controllers.yaw_pattern import YawServoingController
    from mvp_mission_bebop.perception.inspection_worker import InspectionInferenceWorker

from mvp_mission_bebop.actuators.nudge_overlay import NudgeOverlay
from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.blackboard import EvidenceRecord, MissionBlackboard
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.engine.stage_gate import refuse_stage_request, stage_lock
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker
from mvp_mission_bebop.estimation.target_selector import TargetSelector
from mvp_mission_bebop.mission_mode import DEFAULT_MODE, parse_mission_mode
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.classes import display_class_name, is_accident_class, is_victim_class
from mvp_mission_bebop.perception.summary import encode_detection_summary
from mvp_mission_bebop.perception.worker import (
    PerceptionPipeline,
    PerceptionSample,
    SynchronousPerception,
)
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, OdometrySupervisor
from mvp_mission_bebop.telemetry.flat_trim_ack import FlatTrimAckTracker
from mvp_mission_bebop.telemetry.inference_package import (
    build_inference_package,
    drone_occurrence_id,
    format_utc,
)
from mvp_mission_bebop.telemetry.milestones import emit_milestone
from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker

logger = logging.getLogger("MissionContext")

#: The return stage, ``ClosedLoopRTLStep``.
RETURN_STAGE: Final[int] = 5

#: Who asked for a stage jump: the ground station's ``goto_stage`` or the
#: critical-charge return. The runner names it on the jump marker line.
STAGE_REQUEST_SOURCES: Final[Tuple[str, ...]] = ("station", "battery")

#: Stages of a full run. ``mission.py`` overwrites :attr:`MissionContext.stage_numbers`
#: with the selection of a partial one.
FULL_RUN_STAGES: Final[Tuple[int, ...]] = (1, 2, 3, 4, 5)


class MissionContext:
    """Container for drone interfaces, perception pipelines, supervisors, and state."""

    #: Serializes sidecar merges. The hover loop (native photo ACK) and the
    #: inspection worker (victim assessment) both read-modify-write the same
    #: ``accident_metadata_<ts>.json`` through one temporary name.
    _metadata_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        drone: BenchtopDroneProxy,
        detector: Detector,
        handler: ImageHandler,
        odom_supervisor: OdometrySupervisor,
        governor: AltitudeAntiClimbGovernor,
        failsafe: FailsafeSupervisor,
        visual_controller: Union[VisualServoingController, "YawServoingController"],
        parameters: MissionParameters,
        frame_width: int,
        frame_height: int,
    ) -> None:
        self.drone = drone
        self.detector = detector
        self.handler = handler
        self.odom_supervisor = odom_supervisor
        self.governor = governor
        self.failsafe = failsafe
        self.visual_controller = visual_controller
        self.params = parameters

        self.frame_width = frame_width
        self.frame_height = frame_height
        self.frame_center_x = frame_width / 2.0
        self.frame_center_y = frame_height / 2.0

        self.bridge = CvBridge()
        self.image_pub = self.handler.node.create_publisher(
            Image, self.params.network.detection_stream_topic, 1
        )
        self.boxes_pub = self.handler.node.create_publisher(
            String, self.params.network.detection_boxes_topic, 1
        )
        # Offset from the monotonic clock the perception samples are stamped
        # with to ROS time, fixed once so the overlay stamp is comparable with
        # the raw image headers the bridge draws it on.
        self._monotonic_to_ros_sec = (
            self.handler.node.get_clock().now().nanoseconds * 1e-9 - time.monotonic()
        )

        self.current_tilt_deg: float = self.params.gimbal.search_tilt_deg
        #: Whether bounding boxes reach the ground station.
        #:
        #: Inference runs from the takeoff countdown onwards; only rendering is
        #: gated. Closed until Stage 2 confirms a target, then latched open for
        #: the rest of the flight, so the first box the operator sees coincides
        #: with the ``mission.target_found`` narration. The stream keeps its
        #: topic and geometry across the transition: source switches on the
        #: bridge side are what destabilised the cockpit feed before
        #: (``useStreamHealth``).
        self.detection_reveal_enabled: bool = False
        self.emergency_event = threading.Event()
        #: A stage jump commanded by the ground station.
        #:
        #: Distinct from ``emergency_event`` on purpose: both stop the running
        #: step promptly, but one is on its way to the ground and the other is
        #: on its way to a different part of the mission. The runner reads which
        #: is which after the step unwinds.
        self.stage_jump_event = threading.Event()
        #: Stage number requested with ``stage_jump_event``, 1-5.
        self.requested_stage: Optional[int] = None
        #: Origin of ``requested_stage``, one of :data:`STAGE_REQUEST_SOURCES`.
        self.requested_stage_source: Optional[str] = None
        #: Serializes the stage gate and its jump request (executor threads)
        #: against the runner naming the next stage (``engine.stage_gate.stage_lock``).
        self.stage_lock: threading.RLock = threading.RLock()
        #: Set by Stage 5 once it has read ``blackboard.battery_return`` and
        #: chosen its branch; a critical charge after that changes nothing.
        self.return_branch_committed: bool = False
        #: Stage number the runner is executing; ``None`` before the first.
        self.current_stage: Optional[int] = None
        #: Stages this run holds, as the runner numbers them. Read by the
        #: critical-charge return, which goes through the station's stage gate.
        self.stage_numbers: Tuple[int, ...] = FULL_RUN_STAGES
        #: Last ``/bebop/states/flying_state`` from the aircraft, or ``None``.
        self.flying_state: Optional[int] = None
        #: Monotonic takeoff instant of a launch from standby (click plus the
        #: countdown, ``engine/launch.py``); ``None`` counts from Stage 1.
        self.launch_deadline: Optional[float] = None
        #: Emits the countdown ticks of a launch deadline from the go
        #: (``engine/launch.py:CountdownTicker``); ``None`` otherwise.
        self.countdown_ticker: Optional[Any] = None
        #: FlatTrimChanged count from ``states/flat_trim``; ``None`` in unit doubles.
        self.flat_trim_ack: Optional[FlatTrimAckTracker] = None
        #: Publishes this mission's z0 on ``mission/ground_reference``; no-op until wired.
        self.publish_ground_reference: Callable[[float], None] = lambda _z0: None
        #: Native photo acknowledgements from ``states/picture_event``; ``None``
        #: when no driver topic is wired (unit doubles).
        self.picture_ack: Optional[PictureAckTracker] = None
        #: The runner entered the current stage through a station jump.
        self.entered_by_jump: bool = False
        self.start_time: float = time.time()
        self.blackboard = MissionBlackboard()
        #: Persistent target selection shared by Stages 2 to 4, so the track
        #: identity survives the stage boundaries. ``None`` when the vision
        #: configuration is invalid: the steps then fall back to the most
        #: confident detection of each frame.
        self.target_selector: Optional[TargetSelector] = self._build_target_selector(parameters)
        self.nudge: Optional[Any] = None

        # Single source of truth for the normalized-command to m/s conversion.
        # Guidance laws work in physical units; this is the only place the two
        # domains meet, and it now also carries the dead zone and efficiency the
        # dead-reckoning integration needs.
        self.speed_calibration = SpeedCalibration.from_kinematics(parameters.kinematics)

        # Whether the camera can be asked for a *new* frame rather than for
        # whatever it last received. See :meth:`grab_frame`.
        self._camera_blocks_for_new_frames = self._probe_frame_waiting()

        #: Perception pipeline the closed-loop stages read detections from.
        #:
        #: Inline by default, which reproduces the original one-frame-per-cycle
        #: timing. ``mission.py`` replaces it with a started
        #: :class:`~mvp_mission_bebop.perception.worker.PerceptionWorker` so
        #: inference runs off the control thread. ``detector`` stays exposed
        #: for the one-off calls that need exclusive use of it: the countdown
        #: warmup and the Stage 4 evidence capture.
        self.perception: PerceptionPipeline = SynchronousPerception(self)
        #: Background victim inference on the Stage 4 inspection frame, with
        #: its own detector. Started by ``mission.py``; ``None`` in unit
        #: doubles, where the capture then submits nothing.
        self.inspection_worker: Optional["InspectionInferenceWorker"] = None

    @staticmethod
    def _build_target_selector(parameters: MissionParameters) -> Optional[TargetSelector]:
        try:
            return TargetSelector(parameters.vision)
        except (TypeError, ValueError) as exc:
            logger.error("Target selector unavailable (%s); selecting the most confident detection.", exc)
            return None

    # -------------------------------------------------------------- estimation

    def interrupted(self) -> bool:
        """True when the running step must stop early.

        Two things end a step before its own logic would: the operator aborting,
        and the operator jumping to a different stage. Steps react identically —
        stop flying this, unwind, hand control back — so they ask one question.
        Which of the two it was is the runner's business, not theirs.
        """
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def request_stage(self, stage: int, *, source: str = "station") -> None:
        """Ask the runner to continue at ``stage`` (1-5) once this step unwinds.

        Parameters
        ----------
        stage : int
            Stage number, as the runner numbers them.
        source : str
            Who asked, one of :data:`STAGE_REQUEST_SOURCES`; ``"station"`` by
            default, the ground station's ``goto_stage``.

        Raises
        ------
        TypeError
            If ``source`` is not a string.
        ValueError
            If ``source`` is not one of :data:`STAGE_REQUEST_SOURCES`.
        """
        if not isinstance(source, str):
            raise TypeError(f"source must be a string, got {type(source).__name__}")
        if source not in STAGE_REQUEST_SOURCES:
            raise ValueError(f"source must be one of {STAGE_REQUEST_SOURCES}, got {source!r}")
        self.requested_stage_source = source
        self.requested_stage = stage
        self.stage_jump_event.set()

    def request_battery_return(self, reason: str, *, source: str = "battery") -> bool:
        """Jump to Stage 5 on a critical charge, marking the return as forced.

        Goes through the same gate as the station's ``goto_stage``
        (:func:`~mvp_mission_bebop.engine.stage_gate.refuse_stage_request`),
        which accepts Stage 5 from any stage, the climb included.
        ``blackboard.battery_return`` is set before the jump, so the Stage 5
        that runs next flies the return of the profile even with
        ``rtl.enabled = false``. Runs on the executor thread that delivered the
        battery reading, as the station's request does. The gate, the flag and
        the jump request are decided under :attr:`stage_lock`, the lock the
        runner holds while it names the next stage, so a request can never
        land between the runner naming Stage 5 and Stage 5 reading the flag;
        the log and the milestone follow outside it.

        Parameters
        ----------
        reason : str
            ``BatterySupervisor.critical_reason``, for the log.
        source : str
            Who asked, one of :data:`STAGE_REQUEST_SOURCES`: ``"battery"``
            for the supervisor's hook, ``"station"`` for a ``goto_stage 5``
            that arrived on a critical charge (``engine.stage_gate``).

        Returns
        -------
        bool
            ``True`` when the return was requested; when a jump to Stage 5
            is already pending (no second request, no second milestone);
            when the forced return had been left by an operator jump and no
            jump to Stage 5 is pending (requested again, no second
            milestone); when Stage 5 is named but has not
            chosen its branch yet (marked forced, not restarted); and when
            Stage 5 has chosen its branch (nothing changes, nothing is
            narrated, nothing restarts). ``False`` when the gate refuses,
            which for Stage 5 means this run does not hold it (a partial
            ``--stages`` run); nothing is marked or emitted then.

        Raises
        ------
        TypeError
            If ``reason`` or ``source`` is not a string.
        ValueError
            If ``source`` is not one of :data:`STAGE_REQUEST_SOURCES`.
        """
        if not isinstance(reason, str):
            raise TypeError(f"reason must be a string, got {type(reason).__name__}")
        if not isinstance(source, str):
            raise TypeError(f"source must be a string, got {type(source).__name__}")
        if source not in STAGE_REQUEST_SOURCES:
            raise ValueError(f"source must be one of {STAGE_REQUEST_SOURCES}, got {source!r}")

        refusal: Optional[str] = None
        with stage_lock(self):
            from_stage = self.current_stage
            if from_stage == RETURN_STAGE:
                if self.return_branch_committed:
                    outcome = "committed"
                elif self.blackboard.battery_return:
                    outcome = "duplicate"
                else:
                    self.blackboard.battery_return = True
                    outcome = "forced"
            else:
                refusal = refuse_stage_request(
                    RETURN_STAGE,
                    stage_numbers=self.stage_numbers,
                    current_stage=from_stage,
                    takeoff_committed=bool(getattr(self.blackboard, "takeoff_committed", False)),
                    takeoff_complete=bool(getattr(self.blackboard, "takeoff_complete", False)),
                    airborne=False,
                )
                if refusal is not None:
                    outcome = "refused"
                elif self.blackboard.battery_return and (
                    self.stage_jump_event.is_set() and self.requested_stage == RETURN_STAGE
                ):
                    outcome = "duplicate"
                elif self.blackboard.battery_return:
                    # The forced return was left by an operator jump; the hook
                    # is one-shot, so the return is requested again, unnarrated.
                    self.request_stage(RETURN_STAGE, source=source)
                    outcome = "re-requested"
                else:
                    self.blackboard.battery_return = True
                    self.request_stage(RETURN_STAGE, source=source)
                    outcome = "requested"

        if outcome == "refused":
            logger.warning("[BATTERY] return refused: %s (%s).", refusal, reason)
            return False
        if outcome == "committed":
            logger.warning("[BATTERY] %s Stage 5 has already chosen its branch; not restarted.", reason)
            return True
        if outcome == "duplicate":
            logger.info("[BATTERY] return already requested (%s); request from %s ignored.", reason, source)
            return True
        if outcome == "re-requested":
            logger.warning("[BATTERY] return requested again by %s: %s", source, reason)
            return True
        logger.warning("[BATTERY] return forced: %s", reason)
        emit_milestone("mission.battery_return", self._battery_return_payload(from_stage))
        return True

    def request_battery_return_or_land(self, reason: str) -> bool:
        """The battery return hook: :meth:`request_battery_return`, else land.

        The landing in place is reached only when the gate refuses, which
        happens only when this run does not hold Stage 5; it is the one case in
        which the critical charge lands the aircraft directly.

        Parameters
        ----------
        reason : str
            ``BatterySupervisor.critical_reason``.

        Returns
        -------
        bool
            ``True`` when the return was requested or is already running;
            ``False`` when the emergency landing was commanded instead.
        """
        if self.request_battery_return(reason):
            return True
        landing_reason = self._battery_landing_reason()
        logger.critical("[BATTERY] %s Stages in this run: %s.", landing_reason, list(self.stage_numbers))
        self.failsafe.trigger_emergency_land(landing_reason)
        return False

    def _battery_landing_reason(self) -> str:
        """Failsafe reason for the refused return; it says landing, not returning.

        Starts with ``"Battery at"``, the prefix the failsafe maps to its
        spoken clause (``telemetry.failsafe.spoken_reason``).
        """
        battery = getattr(self.failsafe, "battery_supervisor", None)
        percentage = battery.current_percentage() if battery is not None else None
        threshold = getattr(battery, "land_pct", None)
        if percentage is None or threshold is None:
            charge = "Battery at the return threshold"
        else:
            charge = f"Battery at {percentage:.0f}% <= return threshold {threshold:.0f}%"
        return f"{charge}; stage {RETURN_STAGE} is not in this run, landing in place."

    def _battery_return_payload(self, from_stage: Optional[int]) -> Dict[str, Any]:
        """``mission.battery_return`` payload; the mode selects the station's phrase pool."""
        battery = getattr(self.failsafe, "battery_supervisor", None)
        percentage = battery.current_percentage() if battery is not None else None
        threshold = getattr(battery, "land_pct", None)
        try:
            mode = parse_mission_mode(getattr(self.params, "mission_mode", None))
        except (TypeError, ValueError):
            mode = DEFAULT_MODE
        return {
            "battery_pct": None if percentage is None else round(float(percentage), 1),
            "threshold_pct": None if threshold is None else round(float(threshold), 1),
            "from_stage": from_stage,
            "mode": mode.value,
        }

    @property
    def motion_tracker(self) -> Optional["DeadReckoningTracker"]:
        """Aggregated motion sequence, or ``None`` when dead reckoning is off.

        Owned by the actuator proxy rather than by this context, because the
        proxy is where every ``move_velocity`` in the mission passes through and
        an aggregate assembled anywhere else would be only as complete as five
        separate control loops remembering to report themselves. Exposed here so
        the steps have one obvious place to look.
        """
        return getattr(self.drone, "motion_tracker", None)

    # ------------------------------------------------------------- perception

    def _probe_frame_waiting(self) -> bool:
        """Can the camera driver distinguish a new frame from a repeated one?"""
        camera = getattr(self.handler, "camera", None)
        getter = getattr(camera, "get_frame", None)
        if getter is None:
            return False
        try:
            return "wait_for_new" in inspect.signature(getter).parameters
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            return False

    def grab_frame(self, timeout_sec: float = 1.0) -> Optional[np.ndarray]:
        """Acquire one genuinely new camera frame, or ``None`` on timeout.

        The mission's perception loops must go through this rather than through
        ``ImageHandler.take_photo``, because that method cannot deliver what its
        own signature promises against this camera.

        ``take_photo`` selects between a blocking and a polling path on
        ``getattr(camera, "_use_ros_topics", False) or getattr(camera,
        "is_threaded", False)``. ``ROSCam`` defines neither attribute, so the
        polling path always wins -- and that path calls ``camera.get_frame()``
        with no arguments, discarding ``take_photo``'s own ``wait_for_new=True``
        default. ``ROSCam.get_frame`` then returns its cached frame
        unconditionally.

        Three consequences, all of which matter in flight:

        *The video-loss failsafe becomes unreachable.* ``take_photo`` never
        returns ``None`` once a single frame has ever arrived, so the frame
        heartbeat is refreshed forever and the stream timeout in
        ``FailsafeSupervisor.evaluate_system_health`` cannot fire. Losing the
        Bebop's video link produced no abort at all.

        *Every "no frame this cycle" branch becomes dead code*, including the
        station-keeping holds in Stages 2 and 3.

        *Target confirmation can fire on one frozen image.* The hysteresis
        confirmer counts loop iterations, not distinct frames, so three passes
        over an identical cached image satisfy ``confirmation_frames`` and commit
        the mission to an approach -- while the airframe is still physically
        cruising forward past whatever it actually saw.

        ``ROSCam.get_frame(wait_for_new=True, timeout=...)`` is implemented
        correctly, with a frame counter and an event; nothing was reaching it.
        This method does, and falls back to ``take_photo`` for any camera that
        does not offer the parameter -- which is also what keeps the benchtop
        path, where ``take_photo`` is replaced by a fixture, working unchanged.
        """
        if self._camera_blocks_for_new_frames and not self.params.no_fly:
            frame = self.handler.camera.get_frame(wait_for_new=True, timeout=timeout_sec)
        else:
            frame = self.handler.take_photo(timeout_sec=timeout_sec)

        if frame is not None:
            self._observe_frame_geometry(frame)
        return frame

    def _observe_frame_geometry(self, frame: np.ndarray) -> None:
        """Track the live frame size instead of trusting the first one forever.

        The dimensions used to be sampled once at startup and then passed to the
        servoing law for the rest of the mission. The Bebop's H.264 stream can
        renegotiate resolution when the link degrades, and the principal point
        the control law subtracts is derived from these numbers: a change from
        856x480 to 1280x720 would leave the assumed centre 212 px off the true
        one, and the lateral loop would faithfully fly that bias out.
        """
        height, width = frame.shape[:2]
        if width == self.frame_width and height == self.frame_height:
            return

        logger.warning(
            "Camera frame geometry changed from %dx%d to %dx%d; re-centring the optical axis.",
            self.frame_width,
            self.frame_height,
            width,
            height,
        )
        self.frame_width = int(width)
        self.frame_height = int(height)
        self.frame_center_x = self.frame_width / 2.0
        self.frame_center_y = self.frame_height / 2.0

    @staticmethod
    def _result_for_annotation(result: Any) -> Any:
        """Clone a detection result with the labels the station shows.

        Every accident class reads ``Acidente`` and a person ``Vitima``
        (``perception/classes.py:display_class_name``); other classes and the
        result itself are left untouched.
        """
        if result is None:
            return None

        def shown(detection: Any) -> bool:
            name = getattr(detection, "class_name", "")
            return is_accident_class(name) or is_victim_class(name)

        def relabel(detections: Any) -> List[Any]:
            cloned = []
            for d in detections:
                if shown(d):
                    d_copy = copy.copy(d)
                    d_copy.class_name = display_class_name(getattr(d, "class_name", ""))
                    cloned.append(d_copy)
                else:
                    cloned.append(d)
            return cloned

        detections = getattr(result, "detections", None)
        if detections is not None:
            if not any(shown(d) for d in detections):
                return result
            res_copy = copy.copy(result)
            res_copy.detections = relabel(detections)
            return res_copy
        if isinstance(result, list):
            return relabel(result)
        return result

    def publish_annotated_stream(
        self, frame: Optional[np.ndarray], result: Any, status_text: str
    ) -> Optional[np.ndarray]:
        """Draw detections and telemetry overlays, then publish to ROS 2 topic.

        While :attr:`detection_reveal_enabled` is closed the drawing pass is
        skipped and the crosshair and status band are drawn over a copy of the
        raw frame. The copy is load-bearing: Stage 4 records ``frame`` itself
        as the raw evidence after this call, and the SDK's ``draw_detections``
        never writes into its input either.
        """
        if frame is None:
            return None

        if self.detection_reveal_enabled:
            annotated_result = self._result_for_annotation(result)
            annotated = self.detector.draw_detections(
                image=frame,
                result=annotated_result,
                show_labels=True,
                show_confidence=True,
                show_class=True,
                annotator_type="box",
                thickness=2,
                text_scale=0.6,
            )
            if annotated is frame:
                annotated = frame.copy()
        else:
            annotated = frame.copy()

        try:
            ros_img = self.bridge.cv2_to_imgmsg(annotated, encoding="bgr8")
            ros_img.header.stamp = self.handler.node.get_clock().now().to_msg()
            ros_img.header.frame_id = "bebop_camera"
            self.image_pub.publish(ros_img)
        except Exception as exc:
            logger.warning("Annotated stream publishing exception: %s", exc)

        return annotated

    def publish_detection_summary(self, sample: PerceptionSample, status_text: str) -> None:
        """Publish one inference result as the lightweight GCS overlay.

        The counterpart of :meth:`publish_annotated_stream` for the continuous
        perception stages: the boxes and caption travel as a few hundred bytes
        of ``bmg.detections.v1`` JSON instead of a re-encoded 1.2 MB frame, and
        the GCS bridge composites them onto the raw camera stream at the camera
        rate. Failures are logged and swallowed; the overlay is never allowed to
        take the perception worker down.

        Obeys :attr:`detection_reveal_enabled` like the annotated path: while
        closed the summary still flows, carrying the status caption and an
        empty box list, so the bridge keeps compositing from this source
        instead of timing it out and falling back to the raw topic.
        """
        try:
            height, width = sample.frame.shape[:2]
            message = String()
            message.data = encode_detection_summary(
                detections=(
                    self._describe_detections(sample.result)
                    if self.detection_reveal_enabled
                    else []
                ),
                frame_width=int(width),
                frame_height=int(height),
                status=status_text,
                stamp_sec=sample.stamp + self._monotonic_to_ros_sec,
                inference_ms=sample.inference_sec * 1000.0,
            )
            self.boxes_pub.publish(message)
        except Exception as exc:  # noqa: BLE001 - the overlay is never flight-critical
            logger.warning("Detection overlay publishing exception: %s", exc)

    def publish_overlay(
        self, width: int, height: int, status_text: str, markers: Sequence[Any] = ()
    ) -> None:
        """Publish a caption and optional landing-marker geometry as the GCS overlay.

        For stages that draw no detector boxes (the return leg): the station
        composites the caption and the markers onto the raw stream at the
        camera rate. ``bmg.detections.v1`` without markers, ``v2`` with them.
        Failures are logged and swallowed.
        """
        try:
            message = String()
            message.data = encode_detection_summary(
                detections=[],
                frame_width=int(width),
                frame_height=int(height),
                status=status_text,
                stamp_sec=time.monotonic() + self._monotonic_to_ros_sec,
                inference_ms=0.0,
                markers=markers,
            )
            self.boxes_pub.publish(message)
        except Exception as exc:  # noqa: BLE001 - the overlay is never flight-critical
            logger.warning("Overlay publishing exception: %s", exc)

    def record_photographic_evidence(
        self,
        raw_frame: Optional[np.ndarray],
        annotated_frame: Optional[np.ndarray],
        detections: Optional[Sequence[Any]] = None,
        snapshot: Optional[OdometrySnapshot] = None,
        native_photo: Optional[Dict[str, Any]] = None,
    ) -> EvidenceRecord:
        """Persist dual-fidelity evidence plus a forensic metadata sidecar.

        Filenames are a hard external contract: the Electron GCS watches the
        working directory for ``accident_raw_<ts>.png`` and
        ``accident_inspected_<ts>.jpg`` sharing one ``%Y%m%d_%H%M%S`` timestamp,
        and derives the second name from the first by string substitution
        (``electron/main.cjs:354-384``). The extensions are hardcoded there, not
        inferred, so PNG for raw and JPEG for annotated are both load-bearing.

        Writes go through a temporary file and an atomic rename. The watcher
        fires on directory events and gates on file size, so a partially written
        image could previously surface as a truncated capture; after a rename
        the file is complete the first time it is seen. The temporary name is
        dot-prefixed so it cannot match the watcher's prefixes in the interim.
        """
        record = EvidenceRecord()
        if raw_frame is None and annotated_frame is None:
            logger.warning("No frame buffer available to record photographic evidence.")
            return record

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        record.timestamp = timestamp
        output_dir = self.params.output_dir

        # One UTC instant for the identifier and the sidecar, so the occurrence
        # id and its timestamp can never disagree by a second boundary.
        captured_at = datetime.now(timezone.utc)
        record.occurrence_id = drone_occurrence_id(captured_at)

        if raw_frame is not None:
            record.raw_path = self._write_atomic(
                os.path.join(output_dir, f"accident_raw_{timestamp}.png"),
                raw_frame,
                [cv2.IMWRITE_PNG_COMPRESSION, 0],
            )

        if annotated_frame is not None:
            record.annotated_path = self._write_atomic(
                os.path.join(output_dir, f"accident_inspected_{timestamp}.jpg"),
                annotated_frame,
                [cv2.IMWRITE_JPEG_QUALITY, 100],
            )

        if self.params.inspection.write_metadata_sidecar:
            record.detections = self._describe_detections(detections)
            record.metadata_path = self._write_metadata(
                os.path.join(output_dir, f"accident_metadata_{timestamp}.json"),
                record,
                snapshot,
                native_photo,
                captured_at,
            )

        return record

    def update_evidence_metadata(self, path: str, fields: Dict[str, Any]) -> None:
        """Merge ``fields`` into an evidence sidecar, atomically.

        Used for what arrives after the capture: the aircraft's acknowledgement
        of the native photo (``steps/inspection.py:_resolve_native_ack``) and
        the victim assessment (``perception/inspection_worker.py``), from two
        threads; merges are serialized.

        Raises
        ------
        OSError, ValueError
            When the sidecar cannot be read or written.
        """
        with MissionContext._metadata_lock:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload.update(fields)
            directory = os.path.dirname(path) or "."
            base, extension = os.path.splitext(os.path.basename(path))
            temporary = os.path.join(directory, f".{base}.tmp{extension}")
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(temporary, path)

    def _write_atomic(self, path: str, image: np.ndarray, params: List[int]) -> Optional[str]:
        """Encode to a temporary file in the same directory, then rename.

        The temporary name keeps the real extension last: OpenCV selects its
        encoder from the file extension, so a name ending in ``.tmp`` fails with
        "could not find a writer for the specified extension" and silently loses
        the capture. The leading dot is what keeps the interim file from
        matching the GCS watcher's prefixes.
        """
        directory = os.path.dirname(path) or "."
        base, extension = os.path.splitext(os.path.basename(path))
        temporary = os.path.join(directory, f".{base}.tmp{extension}")
        try:
            if not cv2.imwrite(temporary, image, params):
                logger.error("Encoder refused to write %s.", path)
                return None
            os.replace(temporary, path)
        except Exception as exc:  # noqa: BLE001 - evidence loss must not end the flight
            logger.error("Failed to record evidence at %s: %s", path, exc)
            self._discard(temporary)
            return None

        logger.info("Evidence recorded: %s (%d bytes).", path, os.path.getsize(path))
        return path

    def _write_metadata(
        self,
        path: str,
        record: EvidenceRecord,
        snapshot: Optional[OdometrySnapshot],
        native_photo: Optional[Dict[str, Any]] = None,
        captured_at: Optional[datetime] = None,
    ) -> Optional[str]:
        """Write the forensic sidecar describing the capture.

        The sidecar is the handoff to the visual inference agent: its
        ``inference_package`` (``telemetry/inference_package.py``) holds the
        session data, the image reference and the caption exactly as the agent
        consumes them. The GCS watcher (``electron/main.cjs:readCaptureTelemetry``)
        reads ``occurrence_id``, ``latitude``, ``longitude``, ``timestamp_utc``,
        ``telemetry_caption`` and ``inference_package`` and forwards them on
        ``bmg:raw-evidence-ready``; those keys are a contract. ``latitude`` and
        ``longitude`` are the fixed demonstration site
        (``inference_package.SITE_LATITUDE``/``SITE_LONGITUDE``).
        ``native_photo`` is the aircraft's acknowledgement of the 14 MP photo;
        its ``remote_path``/``local_path`` are filled after touchdown by
        ``bebop_mission_control/streamer/media_fetch.py``.

        Parameters
        ----------
        path : str
            Destination of the sidecar.
        record : EvidenceRecord
            The capture being described.
        snapshot : OdometrySnapshot, optional
            Odometry at the capture instant.
        native_photo : dict, optional
            Acknowledgement of the aircraft's native photo.
        captured_at : datetime, optional
            Timezone-aware capture instant. Falls back to the current instant.

        Returns
        -------
        str or None
            ``path`` on success, ``None`` when the write failed.
        """
        if captured_at is None:
            captured_at = datetime.now(timezone.utc)
        occurrence_id = record.occurrence_id or drone_occurrence_id(captured_at)
        record.occurrence_id = occurrence_id

        package = build_inference_package(
            occurrence_id,
            captured_at,
            record.raw_path,
            record.annotated_path,
        )
        caption = package["caption"]

        payload: Dict[str, Any] = {
            "occurrence_id": occurrence_id,
            "latitude": caption.get("latitude"),
            "longitude": caption.get("longitude"),
            "captured_at_utc": format_utc(captured_at),
            "timestamp_utc": caption.get("timestamp_utc"),
            "telemetry_caption": caption,
            "inference_package": package,
            "timestamp": record.timestamp,
            "raw_image": os.path.basename(record.raw_path) if record.raw_path else None,
            "annotated_image": (
                os.path.basename(record.annotated_path) if record.annotated_path else None
            ),
            "frame": {"width": self.frame_width, "height": self.frame_height},
            "gimbal_tilt_deg": self.current_tilt_deg,
            "detections": record.detections,
            "mission": {
                "no_fly": self.params.no_fly,
                "elapsed_sec": round(time.time() - self.start_time, 3),
                "target_classes": list(self.params.vision.target_classes),
                "confidence_threshold": self.params.vision.confidence_threshold,
            },
        }

        if native_photo is not None:
            payload["native_photo"] = dict(native_photo)

        if snapshot is not None:
            payload["odometry"] = {
                "x_m": snapshot.x,
                "y_m": snapshot.y,
                "relative_altitude_m": snapshot.relative_altitude,
                "height_above_floor_m": getattr(
                    snapshot, "height_above_floor_m", snapshot.relative_altitude
                ),
                "raw_altitude_m": snapshot.raw_altitude,
                "yaw_rad": snapshot.yaw,
                "speed_mps": snapshot.speed,
                "launch_origin": {"x_m": snapshot.takeoff_x, "y_m": snapshot.takeoff_y},
                "ground_reference_m": snapshot.ground_reference,
            }

        directory = os.path.dirname(path) or "."
        base, extension = os.path.splitext(os.path.basename(path))
        temporary = os.path.join(directory, f".{base}.tmp{extension}")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(temporary, path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write forensic metadata at %s: %s", path, exc)
            self._discard(temporary)
            return None

        logger.info("Forensic metadata recorded: %s", path)
        return path

    @staticmethod
    def _describe_detections(detections: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
        """Reduce detections to a serializable description."""
        described: List[Dict[str, Any]] = []
        for detection in detections or []:
            try:
                described.append(
                    {
                        "class_name": detection.class_name,
                        "class_id": int(detection.class_id),
                        "confidence": round(float(detection.confidence), 4),
                        "bbox_xyxy": [int(value) for value in detection.bbox],
                        "center_px": [round(float(value), 1) for value in detection.center],
                        "area_px": int(detection.area),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - never lose a capture to metadata
                logger.debug("Skipping undescribable detection: %s", exc)
        return described

    @staticmethod
    def _discard(path: str) -> None:
        """Remove a temporary file, ignoring absence."""
        try:
            os.remove(path)
        except OSError:
            pass
