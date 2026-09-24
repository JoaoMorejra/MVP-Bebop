"""Execution context encapsulating shared flight resources and telemetry."""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from std_msgs.msg import String

from nectar.ai.detection import Detector
from nectar.vision import ImageHandler

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.blackboard import EvidenceRecord, MissionBlackboard
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.perception.summary import encode_detection_summary
from mvp_mission_bebop.perception.worker import (
    PerceptionPipeline,
    PerceptionSample,
    SynchronousPerception,
)
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot, OdometrySupervisor

logger = logging.getLogger("MissionContext")


class MissionContext:
    """Container for drone interfaces, perception pipelines, supervisors, and state."""

    def __init__(
        self,
        drone: BenchtopDroneProxy,
        detector: Detector,
        handler: ImageHandler,
        odom_supervisor: OdometrySupervisor,
        governor: AltitudeAntiClimbGovernor,
        failsafe: FailsafeSupervisor,
        visual_controller: VisualServoingController,
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
        self.start_time: float = time.time()
        self.blackboard = MissionBlackboard()

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

    # -------------------------------------------------------------- estimation

    def interrupted(self) -> bool:
        """True when the running step must stop early.

        Two things end a step before its own logic would: the operator aborting,
        and the operator jumping to a different stage. Steps react identically —
        stop flying this, unwind, hand control back — so they ask one question.
        Which of the two it was is the runner's business, not theirs.
        """
        return self.emergency_event.is_set() or self.stage_jump_event.is_set()

    def request_stage(self, stage: int) -> None:
        """Ask the runner to continue at ``stage`` (1-5) once this step unwinds."""
        self.requested_stage = stage
        self.stage_jump_event.set()

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
        heartbeat is refreshed forever and the eight-second stream timeout in
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
            annotated = self.detector.draw_detections(
                image=frame,
                result=result,
                show_labels=True,
                show_confidence=True,
                show_class=True,
                annotator_type="box",
                thickness=2,
                text_scale=0.6,
            )
        else:
            annotated = frame.copy()

        cx_int = int(self.frame_center_x)
        cy_int = int(self.frame_center_y)
        cv2.drawMarker(annotated, (cx_int, cy_int), (0, 255, 255), cv2.MARKER_CROSS, 20, 1)
        cv2.rectangle(annotated, (10, 10), (self.frame_width - 10, 48), (20, 20, 20), -1)
        cv2.putText(
            annotated,
            status_text,
            (20, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

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

    def record_photographic_evidence(
        self,
        raw_frame: Optional[np.ndarray],
        annotated_frame: Optional[np.ndarray],
        detections: Optional[Sequence[Any]] = None,
        snapshot: Optional[OdometrySnapshot] = None,
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
            )

        return record

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
    ) -> Optional[str]:
        """Write the forensic sidecar describing the capture.

        Purely additive: the GCS assembles its report client-side and reads no
        such file, so the schema here is free to evolve.
        """
        payload: Dict[str, Any] = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
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

        if snapshot is not None:
            payload["odometry"] = {
                "x_m": snapshot.x,
                "y_m": snapshot.y,
                "relative_altitude_m": snapshot.relative_altitude,
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
