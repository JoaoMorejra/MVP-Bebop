#!/usr/bin/env python3
"""
Bebop 2 autonomous accident recognition, tracking and inspection mission.

Pipeline:
    1. Takeoff and camera tilt to -20.0 deg (forward search angle).
    2. Real-time annotated image publishing to ROS 2 for live viewing in rqt_image_view.
    3. Linear forward search with YOLO COCO detector ('motorcycle', 'bicycle').
    4. Proportional gimbal tracking: a dedicated PID controller continuously adjusts
       the gimbal tilt to maintain the target centered in the camera frame while the
       drone advances forward with velocity proportional to the remaining angular
       distance to nadir (-80.0 deg). If the target is lost, the drone hovers in place
       and the gimbal reverts to the last confirmed angle for re-acquisition.
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
SEARCH_CAMERA_TILT = -20.0   # Initial search angle
NADIR_CAMERA_TILT = -80.0    # Vertical inspection angle (max nadir for Bebop 2)

# Detection & Tracking Parameters
TARGET_CLASSES = ["motorcycle", "bicycle"]
DEFAULT_CONFIDENCE = 0.50
CONFIRM_FRAMES = 3           # Consecutive frames to confirm detection
CENTER_TOLERANCE_PX = 40.0   # Distance to frame center to consider aligned (pixels)

# Proportional Gimbal Tracking Parameters
GIMBAL_KP = 0.04             # Proportional gain (degrees per pixel of error)
GIMBAL_KD = 0.005            # Derivative gain for gimbal damping
GIMBAL_OUTPUT_LIMIT = 4.0    # Maximum gimbal adjustment per cycle (degrees)
GIMBAL_RECOVERY_STEP = 5.0   # Recovery step-up increment (degrees)
LOST_RECOVERY_FRAMES = 5     # Consecutive lost frames before gimbal recovery initiates
RECOVERY_STABILIZE_FRAMES = 10  # Frames of stable detection after recovery before resuming gimbal

# PID Visual Servoing Parameters
LATERAL_KP = -0.002          # Proportional gain for lateral (vy) centering
LONGITUDINAL_KP = 0.002      # Proportional gain for longitudinal (vx) fine centering at nadir
PID_OUTPUT_DEADBAND = 0.01   # Deadband to suppress hover micro-jitter

# Flight Defaults
DEFAULT_HEIGHT = 1.70         # metres (firmware controlled)
DEFAULT_VELOCITY = 0.25      # normalised velocity [-1.0, 1.0]
DEFAULT_STABILIZE = 4.0      # seconds to stabilise after takeoff
DEFAULT_SEARCH_TIMEOUT = 30.0 # seconds before aborting search to RTL
DEFAULT_HOVER_DURATION = 15.0 # seconds hovering above target for live inspection
DEFAULT_TRACKING_TIMEOUT = 60.0  # Maximum tracking duration (seconds)

# ---------------------------------------------------------------------------
# Camera & Snapshot Helpers
# ---------------------------------------------------------------------------
def _save_frame(frame: Optional[np.ndarray], output_dir: str = ".") -> Optional[str]:
    """Save a BGR frame with detections to disk with timestamp.

    Returns the path to the saved file, or ``None`` on failure.
    """
    if frame is None:
        log.warning("No frame to save")
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"accident_capture_{timestamp}.jpg")
    cv2.imwrite(path, frame)
    log.info("Accident snapshot saved locally: %s", path)
    return path


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
        # 4. Set initial search tilt
        current_tilt = SEARCH_CAMERA_TILT
        drone.camera_control(tilt=current_tilt, pan=0.0)
        log.info("Camera tilt set to search angle: %.1f deg", current_tilt)

        # Mode --no-fly: Camera and YOLO testing only (no takeoff, no motor movement)
        if getattr(args, "no_fly", False):
            log.info("Test mode active (--no-fly): motors disabled")
            log.info("Publishing detection stream to: %s", args.detection_topic)
            log.info("Press Ctrl+C to stop")

            while True:
                frame = handler.take_photo(timeout_sec=1.0)
                if frame is None:
                    continue

                result = detector.detect(frame, conf=args.confidence)
                targets = result.filter_by_class(TARGET_CLASSES)

                detected_str = ", ".join(
                    [f"{t.class_name} ({t.confidence:.2f})" for t in targets]
                ) if targets else "None"
                status = (
                    f"TEST MODE (NO FLY) | TARGETS: {detected_str}"
                    f" | TILT: {current_tilt:.1f}deg"
                )
                process_and_publish_frame(frame, result, status)

                if targets:
                    log.info("Target detected: %s", detected_str)

                time.sleep(0.03)

        # Standard flight: Takeoff and search
        if not drone.takeoff(altitude=args.height):
            log.error("Takeoff failed")
            return
        log.info("Airborne -- stabilising for %.1fs", DEFAULT_STABILIZE)
        drone.delay(DEFAULT_STABILIZE)

        # -------------------------------------------------------------------
        # PHASE 1: Linear Forward Search
        # -------------------------------------------------------------------
        log.info(
            "Starting Phase 1: Forward search (timeout=%.1fs, velocity=%.2f)...",
            args.search_timeout, args.velocity,
        )
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
            status = (
                f"PHASE 1: SEARCHING ({elapsed:.1f}s/{args.search_timeout:.1f}s)"
                f" | TILT: {current_tilt:.1f}deg"
            )
            process_and_publish_frame(frame, result, status)

            if targets:
                confirmed_detections += 1
                best_target = max(targets, key=lambda d: d.confidence)
                log.info(
                    "Detection %d/%d: %s (conf=%.2f)",
                    confirmed_detections, CONFIRM_FRAMES,
                    best_target.class_name, best_target.confidence,
                )
                if confirmed_detections >= CONFIRM_FRAMES:
                    target_found = True
                    log.info(
                        "Target confirmed: %s. Halting search, starting tracking.",
                        best_target.class_name,
                    )
                    drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
                    break
            else:
                confirmed_detections = 0
                drone.move_velocity(
                    vx=args.velocity, vy=0.0, vz=0.0, vyaw=0.0, duration=0.1,
                )

        # -------------------------------------------------------------------
        # PHASE 2: Proportional Gimbal Tracking & Visual Servoing
        # -------------------------------------------------------------------
        if target_found:
            log.info(
                "Starting Phase 2: Proportional gimbal tracking "
                "(%.1f deg -> %.1f deg)...",
                current_tilt, NADIR_CAMERA_TILT,
            )

            # --- PID controllers (Nectar SDK) ---
            # Lateral centering: image err_x -> drone body vy.
            # kp < 0: target right of center (err_x > 0) produces vy < 0 (move right).
            pid_lateral = PIDController(
                kp=LATERAL_KP, ki=0.0, kd=0.0,
                setpoint=0.0,
                output_limits=(-0.25, 0.25),
                output_deadband=PID_OUTPUT_DEADBAND,
            )
            # Longitudinal fine centering at nadir: image err_y -> drone body vx.
            # kp > 0: target below center (err_y > 0) produces vx < 0 (backward).
            pid_longitudinal = PIDController(
                kp=LONGITUDINAL_KP, ki=0.0, kd=0.0,
                setpoint=0.0,
                output_limits=(-0.15, 0.15),
                output_deadband=PID_OUTPUT_DEADBAND,
            )
            # Proportional gimbal tilt: image err_y -> tilt delta (degrees).
            # kp > 0: target below center (err_y > 0) produces negative delta (tilt down).
            pid_gimbal = PIDController(
                kp=GIMBAL_KP, ki=0.0, kd=GIMBAL_KD,
                setpoint=0.0,
                output_limits=(-GIMBAL_OUTPUT_LIMIT, GIMBAL_OUTPUT_LIMIT),
            )
            pid_lateral.reset()
            pid_longitudinal.reset()
            pid_gimbal.reset()

            # Tracking state
            last_confirmed_tilt = current_tilt
            consecutive_lost = 0
            recovery_cooldown = 0
            aligned_at_nadir = False

            tracking_start = time.time()

            while time.time() - tracking_start < DEFAULT_TRACKING_TIMEOUT:
                frame = handler.take_photo(timeout_sec=1.0)
                if frame is None:
                    continue

                result = detector.detect(frame, conf=args.confidence)
                targets = result.filter_by_class(TARGET_CLASSES)

                status = (
                    f"PHASE 2: TRACKING | TILT: {current_tilt:.1f}deg"
                    f" | LOST: {consecutive_lost}"
                    f" | COOLDOWN: {recovery_cooldown}"
                )
                process_and_publish_frame(frame, result, status)

                if targets:
                    consecutive_lost = 0
                    det = max(targets, key=lambda d: d.confidence)
                    cx, cy = det.center
                    err_x = float(cx - frame_cx)
                    err_y = float(cy - frame_cy)
                    dist_to_center = math.sqrt(err_x ** 2 + err_y ** 2)

                    # --- Lateral centering (drone vy) ---
                    vy_cmd = pid_lateral.update(-err_x)

                    # --- Proportional gimbal tracking ---
                    # Record the tilt where detection was confirmed before adjusting
                    last_confirmed_tilt = current_tilt

                    if recovery_cooldown > 0:
                        # Post-recovery stabilization: hold gimbal position,
                        # allow only lateral centering and gentle advance.
                        recovery_cooldown -= 1
                        pid_gimbal.reset()
                    else:
                        # Normal proportional gimbal adjustment
                        gimbal_delta = pid_gimbal.update(err_y)
                        current_tilt = current_tilt + gimbal_delta
                        current_tilt = max(
                            NADIR_CAMERA_TILT,
                            min(SEARCH_CAMERA_TILT, current_tilt),
                        )
                        drone.camera_control(tilt=current_tilt, pan=0.0)

                    # --- Longitudinal control (drone vx) ---
                    if current_tilt > NADIR_CAMERA_TILT:
                        # Advance forward with velocity proportional to the
                        # remaining angular distance to nadir.
                        nadir_range = SEARCH_CAMERA_TILT - NADIR_CAMERA_TILT
                        nadir_progress = (
                            (current_tilt - NADIR_CAMERA_TILT) / nadir_range
                        )
                        vx_cmd = args.velocity * nadir_progress * 0.5
                    else:
                        # At nadir: fine-position centering via longitudinal PID
                        vx_cmd = pid_longitudinal.update(err_y)

                    drone.move_velocity(
                        vx=vx_cmd, vy=vy_cmd, vz=0.0, vyaw=0.0,
                        duration=1.0 / 30.0,
                    )

                    # Check nadir alignment condition
                    if (current_tilt <= NADIR_CAMERA_TILT
                            and dist_to_center <= CENTER_TOLERANCE_PX):
                        log.info(
                            "Target centered at nadir "
                            "(tilt=%.1f deg, dist=%.1f px)",
                            current_tilt, dist_to_center,
                        )
                        aligned_at_nadir = True
                        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
                        break

                else:
                    consecutive_lost += 1
                    # Immediate hover: no drone movement while target is lost
                    drone.move_velocity(
                        vx=0.0, vy=0.0, vz=0.0, vyaw=0.0,
                        duration=1.0 / 30.0,
                    )

                    # Gimbal recovery after sustained detection loss
                    if consecutive_lost >= LOST_RECOVERY_FRAMES:
                        if current_tilt != last_confirmed_tilt:
                            # Step 1: revert to the last angle with confirmed detection
                            current_tilt = last_confirmed_tilt
                            drone.camera_control(tilt=current_tilt, pan=0.0)
                            log.info(
                                "Recovery: gimbal reverted to last confirmed "
                                "tilt: %.1f deg",
                                current_tilt,
                            )
                        elif current_tilt < SEARCH_CAMERA_TILT:
                            # Step 2: progressive elevation toward search angle
                            current_tilt = min(
                                SEARCH_CAMERA_TILT,
                                current_tilt + GIMBAL_RECOVERY_STEP,
                            )
                            drone.camera_control(tilt=current_tilt, pan=0.0)
                            log.info(
                                "Recovery: gimbal stepped up to %.1f deg",
                                current_tilt,
                            )
                        # Reset counter to allow settling before next recovery step
                        consecutive_lost = 0
                        recovery_cooldown = RECOVERY_STABILIZE_FRAMES
                        pid_gimbal.reset()

            # ---------------------------------------------------------------
            # PHASE 3: Hover Inspection & Snapshot over the accident
            # ---------------------------------------------------------------
            log.info(
                "Starting Phase 3: Hover inspection over target for %.1fs...",
                args.hover_duration,
            )
            drone.move_velocity(vx=0.0, vy=0.0, vz=0.0)
            hover_start = time.time()
            photo_taken = False

            while time.time() - hover_start < args.hover_duration:
                frame = handler.take_photo(timeout_sec=1.0)
                if frame is not None:
                    result = detector.detect(frame, conf=args.confidence)
                    targets = result.filter_by_class(TARGET_CLASSES)
                    elapsed_hover = time.time() - hover_start
                    status = (
                        f"PHASE 3: INSPECTING HOVER"
                        f" ({elapsed_hover:.1f}s/{args.hover_duration:.1f}s)"
                        f" | TILT: {current_tilt:.1f}deg"
                    )
                    process_and_publish_frame(frame, result, status)

                    # Capture exactly 1 photo with YOLO bounding box during hover
                    if not photo_taken and targets:
                        photo_frame = detector.draw_detections(
                            image=frame.copy(),
                            result=result,
                            show_labels=True,
                            show_confidence=True,
                            show_class=True,
                            annotator_type="box",
                            thickness=2,
                            text_scale=0.6,
                        )
                        _save_frame(photo_frame)
                        photo_taken = True

                drone.delay(0.1)

            # Fallback if photo not taken yet but frame is available
            if not photo_taken:
                last_frame = handler.take_photo(timeout_sec=1.0)
                if last_frame is not None:
                    last_result = detector.detect(last_frame, conf=args.confidence)
                    photo_frame = detector.draw_detections(
                        image=last_frame.copy(),
                        result=last_result,
                        show_labels=True,
                        show_confidence=True,
                        show_class=True,
                        annotator_type="box",
                        thickness=2,
                        text_scale=0.6,
                    )
                    _save_frame(photo_frame)
                    photo_taken = True

        else:
            log.warning(
                "No target found within search timeout (%.1fs).",
                args.search_timeout,
            )

        # -------------------------------------------------------------------
        # PHASE 4: Return to Launch (RTL)
        # -------------------------------------------------------------------
        log.info("Starting Phase 4: Executing Return To Launch (RTL)...")
        # Tilt camera back to search angle for safe return flight
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
        description=(
            "Bebop 2 autonomous accident detection, proportional gimbal"
            " tracking and live inspection mission."
        ),
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
        help=(
            "Duration in seconds hovering over target for live inspection"
            " (default: %(default)s)"
        ),
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
        help=(
            "ROS 2 topic for publishing real-time annotated image stream"
            " (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--no-fly",
        action="store_true",
        help="Run in camera & YOLO test mode only (disables takeoff and flight)",
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
                "Connection failed -- is the bebop_driver running?\n"
                "  Start it with: ros2 launch ros2_bebop_driver"
                " bebop_node_launch.xml ip:=%s",
                args.ip,
            )
            return

        run_search_and_inspect_mission(drone, args)

    except KeyboardInterrupt:
        log.info("Interrupted by user -- executing emergency stop and landing")
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        drone.land()
    finally:
        drone.cleanup()
        nectar.shutdown()


if __name__ == "__main__":
    main()
