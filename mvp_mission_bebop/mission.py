#!/usr/bin/env python3
"""
Bebop 2 autonomous accident recognition, tracking and inspection mission.

Pipeline:
    1. Takeoff and camera tilt to -45.0° (forward search angle).
    2. Real-time annotated image publishing to ROS 2 for live viewing in rqt_image_view.
    3. Linear forward search with YOLO COCO detector ('motorcycle', 'bicycle').
    4. Progressive visual tracking: aligns drone with PID and progressively tilts
       the gimbal from -45.0° to -80.0° (nadir) without losing sight of target.
    5. Nadir hover inspection directly above the target for detailed live inspection.
    6. Autonomous return-to-launch (RTL) and landing.

All components strictly use Nectar SDK APIs (Black Bee Drones).
   
"""

import argparse
import logging
import math
import os
import time
from typing import Optional

import cv2
import numpy as np
from cv_bridge import CvBridge
from sensor_msgs.msg import Image

import nectar
from nectar.ai.detection import Detector
from nectar.control import BebopConfig, DroneFactory, PIDController
from nectar.vision import ImageHandler, QoSReliability, ROSConfig

log = logging.getLogger("bebop_mission")

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
BEBOP_IP = "192.168.42.1"
BEBOP_NAMESPACE = "bebop"
CAMERA_TOPIC = "/bebop/camera/image_raw"
DETECTION_TOPIC = "/bebop/camera/detections"

# Camera Angles (negative = down)
SEARCH_CAMERA_TILT = -45.0   # Initial search angle
NADIR_CAMERA_TILT = -80.0    # Vertical inspection angle (max nadir for Bebop 2)
TILT_STEP_DEG = 5.0          # Progressive tilt adjustment step

# Detection & Tracking Parameters
TARGET_CLASSES = ["motorcycle", "bicycle"]
DEFAULT_CONFIDENCE = 0.50
CONFIRM_FRAMES = 3           # Consecutive frames to confirm detection
CENTER_TOLERANCE_PX = 40.0   # Distance to frame center to consider aligned (pixels)
TILT_ADVANCE_TOLERANCE = 80.0 # Error radius to allow advancing camera tilt (pixels)

# Flight Defaults
DEFAULT_HEIGHT = 1.0         # metres (firmware controlled)
DEFAULT_VELOCITY = 0.25      # normalised velocity [-1.0, 1.0]
DEFAULT_STABILIZE = 4.0      # seconds to stabilise after takeoff
DEFAULT_SEARCH_TIMEOUT = 60.0 # seconds before aborting search to RTL
DEFAULT_HOVER_DURATION = 30.0 # seconds hovering above target for live inspection


# ---------------------------------------------------------------------------
# Mission State Machine
# ---------------------------------------------------------------------------
def run_search_and_inspect_mission(drone, args: argparse.Namespace) -> None:
    """Execute the full autonomous accident search, tracking, and inspection."""
    
    # 1. Initialize YOLO Detector (Nectar AI Detector)
    log.info("Loading YOLO detector (yolov8n.pt)...")
    detector = Detector("yolov8n.pt", confidence_threshold=args.confidence)
    detector.load()
    log.info("Detector loaded. Target classes: %s", TARGET_CLASSES)

    # 2. Camera Setup (Nectar ImageHandler)
    cam_config = ROSConfig(
        topic=CAMERA_TOPIC,
        compressed=False,
        reliability=QoSReliability.BEST_EFFORT,
    )
    handler = ImageHandler(
        image_source=CAMERA_TOPIC,
        config=cam_config,
    )
    handler.open()
    log.info("Camera opened on %s", CAMERA_TOPIC)

    # 3. Initialize ROS 2 publisher for rqt_image_view
    bridge = CvBridge()
    image_pub = handler.node.create_publisher(Image, args.detection_topic, 1)
    log.info("Publishing real-time detection stream on: %s", args.detection_topic)

    # Obtain sample frame to get image dimensions
    log.info("Waiting for first camera frame...")
    sample_frame = handler.take_photo(timeout_sec=5.0)
    if sample_frame is None:
        log.error("Failed to receive camera stream from Bebop. Aborting.")
        handler.cleanup()
        return

    h, w = sample_frame.shape[:2]
    frame_cx, frame_cy = w / 2.0, h / 2.0

    # Helper function to process, annotate, and publish live video frame
    def process_and_publish_frame(frame: np.ndarray, result, status_text: str) -> np.ndarray:
        if frame is None:
            return None
        annotated = detector.draw_detections(
            image=frame,
            result=result,
            show_labels=True,
            show_confidence=True,
            show_class=True,
            annotator_type="box",
            thickness=2,
            text_scale=0.6,
        )
        # Overlay mission status and telemetry text
        cv2.rectangle(annotated, (10, 10), (w - 10, 45), (0, 0, 0), -1)
        cv2.putText(
            annotated,
            status_text,
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        # Publish to ROS 2 topic for rqt_image_view
        try:
            msg = bridge.cv2_to_imgmsg(annotated, encoding="bgr8")
            msg.header.stamp = handler.node.get_clock().now().to_msg()
            msg.header.frame_id = "bebop_camera"
            image_pub.publish(msg)
        except Exception as e:
            log.warning("Failed to publish image message: %s", e)
        return annotated

    try:
        # 4. Set initial search tilt (-45.0°) and Takeoff
        current_tilt = SEARCH_CAMERA_TILT
        drone.camera_control(tilt=current_tilt, pan=0.0)
        log.info("Camera tilt set to search angle: %.1f°", current_tilt)

        if not drone.takeoff(altitude=args.height):
            log.error("Takeoff failed")
            return
        log.info("Airborne — stabilising for %.1fs", DEFAULT_STABILIZE)
        drone.delay(DEFAULT_STABILIZE)

        # -------------------------------------------------------------------
        # FASE 1: Linear Forward Search
        # -------------------------------------------------------------------
        log.info("Starting Phase 1: Forward search (timeout=%.1fs, velocity=%.2f)...", args.search_timeout, args.velocity)
        search_start_time = time.time()
        confirmed_detections = 0
        target_found = False

        while time.time() - search_start_time < args.search_timeout:
            frame = handler.take_photo(timeout_sec=1.0)
            if frame is None:
                continue

            result = detector.detect(frame, conf=args.confidence)
            targets = result.filter_by_class(TARGET_CLASSES)

            elapsed = time.time() - search_start_time
            status = f"PHASE 1: SEARCHING ({elapsed:.1f}s/{args.search_timeout:.1f}s) | TILT: {current_tilt:.1f}deg"
            process_and_publish_frame(frame, result, status)

            if targets:
                confirmed_detections += 1
                best_target = max(targets, key=lambda d: d.confidence)
                log.info("Detection %d/%d: %s (conf=%.2f)", confirmed_detections, CONFIRM_FRAMES, best_target.class_name, best_target.confidence)
                if confirmed_detections >= CONFIRM_FRAMES:
                    target_found = True
                    log.info("Target confirmed: %s! Halting search and starting tracking.", best_target.class_name)
                    drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
                    break
            else:
                confirmed_detections = 0
                drone.move_velocity(vx=args.velocity, vy=0.0, vz=0.0, vyaw=0.0, duration=0.1)

        # -------------------------------------------------------------------
        # FASE 2 & 3: Progressive Tracking & Gimbal Transition (45° -> 80°)
        # -------------------------------------------------------------------
        if target_found:
            log.info("Starting Phase 2: Progressive visual tracking & gimbal tilt (-45° -> -80°)...")
            
            # Initialize PID controllers for visual servoing
            pid_x = PIDController(kp=-0.002, ki=0.0, kd=0.0, setpoint=0.0, output_limits=(-0.25, 0.25))
            pid_y = PIDController(kp=-0.002, ki=0.0, kd=0.0, setpoint=0.0, output_limits=(-0.25, 0.25))
            pid_x.reset()
            pid_y.reset()

            tracking_start = time.time()
            max_tracking_time = 45.0  # Max tracking timeout before proceeding to inspect
            aligned_at_nadir = False

            while time.time() - tracking_start < max_tracking_time:
                frame = handler.take_photo(timeout_sec=1.0)
                if frame is None:
                    continue

                result = detector.detect(frame, conf=args.confidence)
                targets = result.filter_by_class(TARGET_CLASSES)

                status = f"PHASE 2: TRACKING | TILT: {current_tilt:.1f}deg | ALIGNED: {aligned_at_nadir}"
                process_and_publish_frame(frame, result, status)

                if targets:
                    det = max(targets, key=lambda d: d.confidence)
                    cx, cy = det.center
                    err_x = float(cx - frame_cx)
                    err_y = float(cy - frame_cy)
                    dist_to_center = math.sqrt(err_x**2 + err_y**2)

                    # Command centering velocities via PID
                    vx_cmd = pid_y.update(err_y)
                    vy_cmd = pid_x.update(-err_x)
                    drone.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=0.0, vyaw=0.0, duration=1.0 / 30.0)

                    # Progressive gimbal tilt step when target is within tracking bounds
                    if dist_to_center < TILT_ADVANCE_TOLERANCE and current_tilt > NADIR_CAMERA_TILT:
                        current_tilt = max(NADIR_CAMERA_TILT, current_tilt - TILT_STEP_DEG)
                        drone.camera_control(tilt=current_tilt, pan=0.0)
                        log.info("Gimbal stepped down to %.1f° (dist_err=%.1fpx)", current_tilt, dist_to_center)

                    # Check if reached nadir (-80°) and well-centered
                    elif current_tilt <= NADIR_CAMERA_TILT and dist_to_center <= CENTER_TOLERANCE_PX:
                        log.info("Target perfectly centered in nadir view (tilt=%.1f°, dist=%.1fpx)!", current_tilt, dist_to_center)
                        aligned_at_nadir = True
                        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
                        break
                else:
                    # Hover gently if target briefly obscured while searching in frame
                    drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, duration=1.0 / 30.0)

            # ---------------------------------------------------------------
            # FASE 4: Hover Inspection over the accident
            # ---------------------------------------------------------------
            log.info("Starting Phase 3: Hover inspection over target for %.1fs...", args.hover_duration)
            drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
            hover_start = time.time()

            while time.time() - hover_start < args.hover_duration:
                frame = handler.take_photo(timeout_sec=1.0)
                if frame is not None:
                    result = detector.detect(frame, conf=args.confidence)
                    elapsed_hover = time.time() - hover_start
                    status = f"PHASE 3: INSPECTING HOVER ({elapsed_hover:.1f}s/{args.hover_duration:.1f}s) | TILT: {current_tilt:.1f}deg"
                    process_and_publish_frame(frame, result, status)
                drone.delay(0.1)

        else:
            log.warning("No target found within search timeout (%.1fs).", args.search_timeout)

        # -------------------------------------------------------------------
        # FASE 5: Return to Launch (RTL)
        # -------------------------------------------------------------------
        log.info("Starting Phase 4: Executing Return To Launch (RTL)...")
        # Tilt camera back to -45° for safe return flight
        drone.camera_control(tilt=SEARCH_CAMERA_TILT, pan=0.0)
        
        drone.rtl()

        log.info("Mission completed successfully.")

    finally:
        log.info("Cleaning up mission resources...")
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        drone.land()
        handler.cleanup()


# ---------------------------------------------------------------------------
# Entry Point & CLI Arguments
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="Bebop 2 autonomous accident detection, progressive tracking and live inspection mission."
    )
    parser.add_argument(
        "--height",
        type=float,
        default=DEFAULT_HEIGHT,
        help="Takeoff altitude in metres (default: %(default)s)",
    )
    parser.add_argument(
        "--velocity",
        type=float,
        default=DEFAULT_VELOCITY,
        help="Forward search velocity [-1.0, 1.0] (default: %(default)s)",
    )
    parser.add_argument(
        "--search-timeout",
        type=float,
        default=DEFAULT_SEARCH_TIMEOUT,
        help="Search timeout in seconds before initiating RTL (default: %(default)s)",
    )
    parser.add_argument(
        "--hover-duration",
        type=float,
        default=DEFAULT_HOVER_DURATION,
        help="Duration in seconds hovering over target for live inspection (default: %(default)s)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_CONFIDENCE,
        help="YOLO detection confidence threshold (default: %(default)s)",
    )
    parser.add_argument(
        "--ip",
        default=BEBOP_IP,
        help="Bebop Wi-Fi IP address (default: %(default)s)",
    )
    parser.add_argument(
        "--detection-topic",
        default=DETECTION_TOPIC,
        help="ROS 2 topic for publishing real-time annotated image stream (default: %(default)s)",
    )
    args = parser.parse_args()

    # ---- Nectar SDK lifecycle ----
    nectar.init()
    config = BebopConfig(
        name="bebop_mission",
        start_driver=False,
        ip=args.ip,
        namespace=BEBOP_NAMESPACE,
    )
    drone = DroneFactory.create("bebop", config)

    try:
        if not drone.connect():
            log.error(
                "Connection failed — is the bebop_driver running?\n"
                "  Start it with: ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=%s",
                args.ip,
            )
            return

        run_search_and_inspect_mission(drone, args)

    except KeyboardInterrupt:
        log.info("Interrupted by user — executing emergency stop and landing")
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        drone.land()
    finally:
        drone.cleanup()
        nectar.shutdown()


if __name__ == "__main__":
    main()
