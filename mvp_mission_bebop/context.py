"""Execution context encapsulating shared flight resources and telemetry."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np
from cv_bridge import CvBridge
from sensor_msgs.msg import Image

from nectar.ai.detection import Detector
from nectar.vision import ImageHandler

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

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
        self.shared_data: Dict[str, Any] = {}

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
    ) -> Tuple[Optional[str], Optional[str]]:
        """Save dual-fidelity photographic evidence: lossless PNG and high-quality JPEG."""
        if raw_frame is None and annotated_frame is None:
            logger.warning("No frame buffer available to record photographic evidence.")
            return None, None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        raw_path: Optional[str] = None
        annotated_path: Optional[str] = None
        output_dir = self.params.output_dir

        if raw_frame is not None:
            raw_path = os.path.join(output_dir, f"accident_raw_{timestamp}.png")
            cv2.imwrite(raw_path, raw_frame, [cv2.IMWRITE_PNG_COMPRESSION, 0])
            logger.info("Raw photographic evidence stored: %s", raw_path)

        if annotated_frame is not None:
            annotated_path = os.path.join(output_dir, f"accident_inspected_{timestamp}.jpg")
            cv2.imwrite(annotated_path, annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
            logger.info("Annotated photographic evidence stored: %s", annotated_path)

        return raw_path, annotated_path
