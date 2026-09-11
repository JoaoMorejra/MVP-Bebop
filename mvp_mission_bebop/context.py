"""Execution context encapsulating shared flight resources and telemetry."""

from __future__ import annotations

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

from nectar.ai.detection import Detector
from nectar.vision import ImageHandler

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.blackboard import EvidenceRecord, MissionBlackboard
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import MissionParameters
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

        self.current_tilt_deg: float = self.params.gimbal.search_tilt_deg
        self.emergency_event = threading.Event()
        self.start_time: float = time.time()
        self.blackboard = MissionBlackboard()

        # Single source of truth for the normalized-command to m/s conversion.
        # Guidance laws work in physical units; this is the only place the two
        # domains meet.
        self.speed_calibration = SpeedCalibration(parameters.kinematics.normalized_to_mps)

    def publish_annotated_stream(
        self, frame: Optional[np.ndarray], result: Any, status_text: str
    ) -> Optional[np.ndarray]:
        """Draw detections and telemetry overlays, then publish to ROS 2 topic."""
        if frame is None:
            return None

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
