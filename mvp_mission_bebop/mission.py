#!/usr/bin/env python3
"""
Autonomous Search, Tracking, Nadir Inspection and RTL Mission for Parrot Bebop 2.

This module implements a deterministic 5-step state machine mission using the
Nectar SDK (Black Bee Drones) for aerial inspection of motorcycles and bicycles.

Key Innovations:
    1. Active Anti-Climb Altitude Governor:
       Prevents the Bebop 2 from climbing when an obstacle (e.g. bicycle/motorcycle)
       passes beneath its downward ultrasonic sensor. Monitors inertial odometry (z_rel)
       and injects gentle corrective downward velocity (vz <= 0) when z_rel exceeds
       the target altitude, guaranteeing that the takeoff altitude remains the hard ceiling.
    2. Modulated Low-Speed Flight (Smooth & Controlled Pace):
       All translational speeds are strictly clamped to gentle, plausible inspection paces:
       - Forward search: 0.05 (~0.35 m/s)
       - Target approach: <= 0.04 (~0.25 m/s)
       - Lateral corrections: <= 0.04 (~0.20 m/s)
       - Vertical descent rate: <= 0.08
       - RTL return speed: <= 0.05
    3. Visually Coupled Gimbal Tracking:
       Image-Based Visual Servoing (IBVS) with high-bandwidth PID tracking (-20° to -80°).
       Drone forward velocity (vx) is strictly coupled to vertical optical centering:
       if the target drifts outside the center tolerance (> 25 px), the drone immediately
       halts forward advance (vx = 0.0), letting the gimbal re-center the target before advancing.
    4. Closed-Loop Odometry Return-to-Launch (Odom-RTL):
       Freezes initial ground takeoff coordinates (x0, y0) during pre-flight calibration.
       In Step 5 (RTL), navigates in closed-loop back to (x0, y0) using 2D body-frame
       rotation of the position error vector. Landing is strictly executed ONLY when the
       drone reaches a 12 cm radius of the takeoff point, completely clear of the obstacle.
    5. Universal Safe Landing on Ctrl+C (Absolute Prohibition of Motor Cutoff):
       Under NO circumstance does the system kill motors or disarm abruptly in mid-air.
       Regardless of how many times Ctrl+C is pressed, it ALWAYS executes a controlled,
       immediate landing sequence (drone.land()), halting translation and bringing the
       drone smoothly to the ground.
    6. Robust Video Watchdog:
       Active frame polling during takeoff stabilization and resilient watchdog timeout
       (8.0 s) preventing false failsafe triggers caused by Wi-Fi video latency.
    7. Full Benchtop Simulation (--no-fly):
       Runs the complete 5-step state machine with real hardware gimbal actuation,
       live YOLO inference, PID tracking, and photo capture without spinning motors.
"""

import argparse
import enum
import logging
import math
import os
import signal
import sys
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np
from cv_bridge import CvBridge
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image

import nectar
from nectar.ai.detection import Detector
from nectar.control import BebopConfig, DroneFactory, PIDController
from nectar.vision import ImageHandler, QoSReliability, ROSConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BebopMission")

# ---------------------------------------------------------------------------
# Hardware and Network Parameters
# ---------------------------------------------------------------------------
BEBOP_DEFAULT_IP: str = "192.168.42.1"
BEBOP_NAMESPACE: str = "bebop"
CAMERA_RAW_TOPIC: str = f"/{BEBOP_NAMESPACE}/camera/image_raw"
DETECTION_STREAM_TOPIC: str = f"/{BEBOP_NAMESPACE}/camera/detections"
ODOMETRY_TOPIC: str = f"/{BEBOP_NAMESPACE}/odom"

# ---------------------------------------------------------------------------
# Camera Gimbal Constraints (degrees, negative indicates downward pitch)
# ---------------------------------------------------------------------------
GIMBAL_SEARCH_TILT_DEG: float = -20.0
GIMBAL_NADIR_TILT_DEG: float = -80.0
GIMBAL_MAX_SLEW_LIMIT_DEG: float = 18.0

# ---------------------------------------------------------------------------
# Detection and Computer Vision Parameters
# ---------------------------------------------------------------------------
TARGET_CLASSES: List[str] = ["motorcycle", "bicycle"]
DEFAULT_DETECTION_CONFIDENCE: float = 0.50
DETECTION_CONFIRMATION_FRAMES: int = 3
OPTICAL_CENTER_TOLERANCE_PX: float = 30.0

# ---------------------------------------------------------------------------
# Closed-Loop Control Parameters (Smooth & Low-Speed Modulated)
# ---------------------------------------------------------------------------
GIMBAL_PID_KP: float = 18.0
GIMBAL_PID_KD: float = 1.2
GIMBAL_OUTPUT_LIMIT: float = 18.0

# Lateral velocity: gentle micro-corrections
LATERAL_PID_KP: float = 0.12
LATERAL_PID_KD: float = 0.01
LATERAL_OUTPUT_LIMIT: float = 0.04
LATERAL_DEADBAND: float = 0.005

# Forward approach velocity cap
MAX_APPROACH_FORWARD_SPEED: float = 0.04
APPROACH_CENTERING_TOLERANCE_PX: float = 25.0

# Altitude Governor Parameters (Anti-Climb Control)
ALTITUDE_DEADBAND_M: float = 0.03
ALTITUDE_GOVERNOR_KP: float = 0.80
ALTITUDE_MAX_DESCENT_SPEED: float = 0.08

# ---------------------------------------------------------------------------
# Closed-Loop RTL Parameters
# ---------------------------------------------------------------------------
RTL_MAX_SPEED: float = 0.05
RTL_KP: float = 0.08
RTL_ARRIVAL_RADIUS_M: float = 0.12
RTL_TIMEOUT_SEC: float = 35.0

# ---------------------------------------------------------------------------
# Operational Flight Defaults
# ---------------------------------------------------------------------------
DEFAULT_TARGET_ALTITUDE_M: float = 1.00
ALTITUDE_CEILING_MARGIN_M: float = 0.25
DEFAULT_CRUISE_VELOCITY: float = 0.05
DEFAULT_TAKEOFF_STABILIZE_SEC: float = 4.0
DEFAULT_SEARCH_TIMEOUT_SEC: float = 30.0
DEFAULT_TRACKING_TIMEOUT_SEC: float = 45.0
DEFAULT_HOVER_DURATION_SEC: float = 7.0
TARGET_RECOVERY_TIMEOUT_SEC: float = 4.0
ODOMETRY_TIMEOUT_SEC: float = 3.0
VIDEO_STREAM_TIMEOUT_SEC: float = 8.0


class MissionState(enum.Enum):
    """Finite state machine states governing the autonomous mission."""

    IDLE = "IDLE"
    CALIBRATING_FLAT_TRIM = "CALIBRATING_FLAT_TRIM"
    TAKEOFF_AND_STABILIZING = "TAKEOFF_AND_STABILIZING"
    FORWARD_SEARCH = "FORWARD_SEARCH"
    TARGET_APPROACH = "TARGET_APPROACH"
    TARGET_RECOVERY = "TARGET_RECOVERY"
    HOVER_INSPECTION = "HOVER_INSPECTION"
    RETURN_TO_LAUNCH = "RETURN_TO_LAUNCH"
    SAFE_LANDING = "SAFE_LANDING"
    COMPLETED = "COMPLETED"
    FAILSAFE_ABORT = "FAILSAFE_ABORT"


class OdometrySupervisor:
    """
    Manages Bebop 2 inertial odometry, zero-altitude ground calibration,
    takeoff launch origin tracking, and strict altitude ceiling enforcement.
    """

    def __init__(self, target_altitude: float, ceiling_margin: float = ALTITUDE_CEILING_MARGIN_M) -> None:
        self.target_altitude = target_altitude
        self.ceiling_margin = ceiling_margin
        self.altitude_ceiling = target_altitude + ceiling_margin
        self.ground_reference_altitude: Optional[float] = None
        self.current_raw_altitude: float = 0.0
        self.current_x: float = 0.0
        self.current_y: float = 0.0
        self.current_yaw: float = 0.0
        self.takeoff_x: Optional[float] = None
        self.takeoff_y: Optional[float] = None
        self.last_odometry_timestamp: float = 0.0
        self.sample_buffer_z: List[float] = []
        self.sample_buffer_x: List[float] = []
        self.sample_buffer_y: List[float] = []

    def odometry_callback(self, msg: Odometry) -> None:
        """Process incoming nav_msgs/Odometry updates."""
        self.current_raw_altitude = float(msg.pose.pose.position.z)
        self.current_x = float(msg.pose.pose.position.x)
        self.current_y = float(msg.pose.pose.position.y)

        # Extract yaw angle from quaternion
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.current_yaw = math.atan2(siny_cosp, cosy_cosp)

        self.last_odometry_timestamp = time.time()
        if self.ground_reference_altitude is None:
            self.sample_buffer_z.append(self.current_raw_altitude)
            self.sample_buffer_x.append(self.current_x)
            self.sample_buffer_y.append(self.current_y)
            if len(self.sample_buffer_z) > 50:
                self.sample_buffer_z.pop(0)
                self.sample_buffer_x.pop(0)
                self.sample_buffer_y.pop(0)

    def calibrate_ground_reference(self) -> bool:
        """
        Calculates and freezes the static ground-level coordinates (x0, y0, z0).
        Must be executed after flat trim while the drone remains motionless on the ground.
        """
        if not self.sample_buffer_z:
            self.ground_reference_altitude = self.current_raw_altitude
            self.takeoff_x = self.current_x
            self.takeoff_y = self.current_y
        else:
            self.ground_reference_altitude = float(np.mean(self.sample_buffer_z))
            self.takeoff_x = float(np.mean(self.sample_buffer_x))
            self.takeoff_y = float(np.mean(self.sample_buffer_y))

        logger.info(
            "Ground launch reference calibrated: x0=%.3f m, y0=%.3f m, z0=%.3f m. Altitude ceiling locked at %.3f m.",
            self.takeoff_x,
            self.takeoff_y,
            self.ground_reference_altitude,
            self.altitude_ceiling,
        )
        return True

    @property
    def relative_altitude(self) -> float:
        """Computes current altitude relative to calibrated ground level."""
        if self.ground_reference_altitude is None:
            return self.current_raw_altitude
        return self.current_raw_altitude - self.ground_reference_altitude

    def get_body_frame_launch_error(self) -> Tuple[float, float, float]:
        """
        Calculates position error vector pointing from the drone back to the
        takeoff origin (x0, y0) rotated into the drone's Body Frame (FLU).

        Returns
        -------
        Tuple[float, float, float]:
            ex_body: forward/backward error in body frame (meters)
            ey_body: left/right error in body frame (meters)
            distance: scalar Euclidean distance to takeoff origin (meters)
        """
        if self.takeoff_x is None or self.takeoff_y is None:
            return 0.0, 0.0, 0.0

        dx_odom = self.takeoff_x - self.current_x
        dy_odom = self.takeoff_y - self.current_y
        dist = math.hypot(dx_odom, dy_odom)

        psi = self.current_yaw
        ex_body = math.cos(psi) * dx_odom + math.sin(psi) * dy_odom
        ey_body = -math.sin(psi) * dx_odom + math.cos(psi) * dy_odom
        return ex_body, ey_body, dist

    def is_ceiling_breached(self) -> bool:
        """Checks if current relative altitude exceeds the strict safety ceiling."""
        return self.relative_altitude > self.altitude_ceiling

    def is_telemetry_healthy(self, timeout_sec: float = ODOMETRY_TIMEOUT_SEC) -> bool:
        """Validates odometry update rate within specified timeout window."""
        if self.last_odometry_timestamp == 0.0:
            return True
        return (time.time() - self.last_odometry_timestamp) <= timeout_sec


class AltitudeAntiClimbGovernor:
    """
    Active Anti-Climb Governor for Bebop 2.

    Prevents ultrasound-induced climbing when passing over obstacles.
    If the relative altitude exceeds target takeoff altitude (z_rel > h_target),
    this controller generates gentle negative vertical velocity (vz <= 0) to push
    the drone back down to the target ceiling, counteracting the sonar autopilot reaction.
    Never commands positive vz (climbing is strictly prohibited).
    """

    def __init__(
        self,
        target_altitude: float,
        deadband: float = ALTITUDE_DEADBAND_M,
        kp: float = ALTITUDE_GOVERNOR_KP,
        max_descent: float = ALTITUDE_MAX_DESCENT_SPEED,
    ) -> None:
        self.target_altitude = target_altitude
        self.deadband = deadband
        self.kp = kp
        self.max_descent = max_descent
        self._last_error: float = 0.0
        self._last_time: float = time.time()

    def compute_vz(self, current_relative_alt: float) -> float:
        """
        Computes corrective downward velocity command.

        Returns
        -------
        float: vz <= 0.0. Negative if above target altitude + deadband, 0.0 otherwise.
        """
        alt_error = current_relative_alt - self.target_altitude
        if alt_error > self.deadband:
            dt = max(1e-3, time.time() - self._last_time)
            d_error = (alt_error - self._last_error) / dt
            vz_correction = -(self.kp * alt_error + 0.03 * d_error)
            vz_cmd = max(-self.max_descent, min(0.0, vz_correction))
            logger.debug(
                "Anti-Climb Governor active: alt=%.2fm > target=%.2fm. Corrective vz=%.2f",
                current_relative_alt, self.target_altitude, vz_cmd,
            )
        else:
            vz_cmd = 0.0

        self._last_error = alt_error
        self._last_time = time.time()
        return vz_cmd


class FlightActuatorProxy:
    """
    Encapsulates flight control dispatch with support for hardware-in-the-loop
    and benchtop (--no-fly) execution.

    In --no-fly mode:
      - Motors: Takeoff, move_velocity, and land commands are simulated (no propeller movement).
      - Gimbal: Camera control commands are LIVE and PHYSICALLY EXECUTED on the Bebop.
    """

    def __init__(self, drone, no_fly: bool = False) -> None:
        self.drone = drone
        self.no_fly = no_fly

    def flat_trim(self) -> None:
        self.drone.flat_trim()

    def takeoff(self, altitude: float) -> bool:
        if self.no_fly:
            logger.info("[NO-FLY BENCHTOP] Simulated Takeoff to %.2f m. Actuators remain unpowered.", altitude)
            return True
        return self.drone.takeoff(altitude=altitude)

    def land(self) -> bool:
        if self.no_fly:
            logger.info("[NO-FLY BENCHTOP] Simulated Landing. System safe.")
            return True
        return self.drone.land()

    def camera_control(self, tilt: float, pan: float = 0.0) -> None:
        """Always physically executed on hardware, even in --no-fly mode."""
        self.drone.camera_control(tilt=tilt, pan=pan)

    def snapshot(self) -> None:
        self.drone.snapshot()

    def move_velocity(self, vx: float, vy: float, vz: float, vyaw: float, duration: Optional[float] = None) -> None:
        if self.no_fly:
            logger.debug("[NO-FLY] move_velocity simulated: vx=%.3f, vy=%.3f, vz=%.3f, vyaw=%.3f", vx, vy, vz, vyaw)
            return
        self.drone.move_velocity(vx=vx, vy=vy, vz=vz, vyaw=vyaw, duration=duration)

    def delay(self, seconds: float) -> None:
        self.drone.delay(seconds)


class FailsafeSupervisor:
    """
    Continuous supervisory observer validating multi-module integrity,
    kinematic constraint invariants, and emergency intervention triggers.
    Always uses controlled landing (drone.land()). Never kills motors.
    """

    def __init__(self, actuator: FlightActuatorProxy, odom_supervisor: OdometrySupervisor, handler: ImageHandler) -> None:
        self.actuator = actuator
        self.odom_supervisor = odom_supervisor
        self.handler = handler
        self.last_valid_frame_timestamp: float = time.time()
        self.failsafe_active: bool = False

    def notify_frame_received(self) -> None:
        """Registers a fresh frame arrival timestamp."""
        self.last_valid_frame_timestamp = time.time()

    def assert_kinematics(self, vz: float, vyaw: float) -> None:
        """
        Enforces kinematic safety invariants:
          1. Absolute zero yaw rate (vyaw == 0.0).
          2. Non-positive vz (vz <= 0.001): climbing commands are strictly prohibited.
        """
        if abs(vyaw) > 1e-4:
            raise ValueError(
                f"Kinematic constraint violation: vyaw={vyaw:.4f}. "
                "Yaw rotation is strictly prohibited during all flight phases."
            )
        if vz > 1e-4:
            raise ValueError(
                f"Kinematic constraint violation: vz={vz:.4f} > 0. "
                "Climbing velocity commands are strictly prohibited."
            )

    def evaluate_system_health(self) -> Tuple[bool, str]:
        """
        Inspects all submodules for failure modes.
        Returns status flag and failure diagnosis reason.
        """
        if self.failsafe_active:
            return False, "Failsafe already engaged."

        if not self.odom_supervisor.is_telemetry_healthy():
            return False, "Odometry telemetry stream loss (heartbeat timeout)."

        if self.odom_supervisor.is_ceiling_breached():
            rel_alt = self.odom_supervisor.relative_altitude
            ceiling = self.odom_supervisor.altitude_ceiling
            return False, f"Inviolable altitude ceiling breached: {rel_alt:.2f} m > {ceiling:.2f} m."

        frame_age = time.time() - self.last_valid_frame_timestamp
        if frame_age > VIDEO_STREAM_TIMEOUT_SEC:
            return False, f"Camera stream loss (frame age {frame_age:.1f}s > {VIDEO_STREAM_TIMEOUT_SEC:.1f}s)."

        return True, "Nominal"

    def trigger_emergency_land(self, reason: str) -> None:
        """
        Executes immediate controlled landing, nullifying all velocities
        and issuing the terminal landing sequence. Never cuts motors.
        """
        self.failsafe_active = True
        logger.critical("FAILSAFE ENGAGED: %s. Halting actuators and executing controlled emergency land.", reason)
        try:
            self.actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            self.actuator.land()
        except Exception as exc:
            logger.critical("Secondary exception during emergency landing execution: %s", exc)


def record_photographic_evidence(
    raw_frame: Optional[np.ndarray],
    annotated_frame: Optional[np.ndarray],
    output_dir: str = ".",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Saves dual-fidelity photographic evidence to disk:
    1. Lossless PNG of pristine uncompressed optical frame.
    2. High-quality JPEG (100% quality index) of annotated inspection frame.
    """
    if raw_frame is None and annotated_frame is None:
        logger.warning("No frame buffer available to record photographic evidence.")
        return None, None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    raw_path: Optional[str] = None
    annotated_path: Optional[str] = None

    if raw_frame is not None:
        raw_path = os.path.join(output_dir, f"accident_raw_{timestamp}.png")
        cv2.imwrite(raw_path, raw_frame, [cv2.IMWRITE_PNG_COMPRESSION, 0])
        logger.info("Raw photographic evidence stored: %s", raw_path)

    if annotated_frame is not None:
        annotated_path = os.path.join(output_dir, f"accident_inspected_{timestamp}.jpg")
        cv2.imwrite(annotated_path, annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
        logger.info("Annotated photographic evidence stored: %s", annotated_path)

    return raw_path, annotated_path


def execute_mission(drone, actuator: FlightActuatorProxy, args: argparse.Namespace) -> None:
    """
    Executes the 5-step autonomous mission state machine under full supervisory control.
    """
    logger.info("Initializing Nectar AI Detector (YOLO architecture)...")
    detector = Detector(args.model_path, confidence_threshold=args.confidence)
    detector.load()
    logger.info("Detector successfully loaded. Monitored target classes: %s", TARGET_CLASSES)

    cam_config = ROSConfig(
        topic=CAMERA_RAW_TOPIC,
        compressed=False,
        reliability=QoSReliability.BEST_EFFORT,
    )
    handler = ImageHandler(
        image_source=CAMERA_RAW_TOPIC,
        config=cam_config,
    )
    handler.open()
    logger.info("Optical sensor pipeline opened on %s", CAMERA_RAW_TOPIC)

    bridge = CvBridge()
    image_pub = handler.node.create_publisher(Image, args.detection_topic, 1)

    # Initial frame reception check
    logger.info("Acquiring initial calibration frame...")
    sample_frame = handler.take_photo(timeout_sec=5.0)
    if sample_frame is None:
        logger.critical("Fatal: Unable to receive initial video frame from Bebop driver. Aborting.")
        handler.cleanup()
        return

    frame_height, frame_width = sample_frame.shape[:2]
    frame_center_x: float = frame_width / 2.0
    frame_center_y: float = frame_height / 2.0
    logger.info(
        "Optical sensor dimensions: %d x %d px (Center: cx=%.1f, cy=%.1f)",
        frame_width, frame_height, frame_center_x, frame_center_y,
    )

    odom_supervisor = OdometrySupervisor(
        target_altitude=args.height,
        ceiling_margin=ALTITUDE_CEILING_MARGIN_M,
    )
    handler.node.create_subscription(
        Odometry,
        ODOMETRY_TOPIC,
        odom_supervisor.odometry_callback,
        10,
    )
    logger.info("Subscribed to odometry telemetry on %s", ODOMETRY_TOPIC)

    # Active Anti-Climb Governor
    altitude_governor = AltitudeAntiClimbGovernor(
        target_altitude=args.height,
        deadband=ALTITUDE_DEADBAND_M,
        kp=ALTITUDE_GOVERNOR_KP,
        max_descent=ALTITUDE_MAX_DESCENT_SPEED,
    )

    failsafe = FailsafeSupervisor(actuator, odom_supervisor, handler)
    failsafe.notify_frame_received()

    def publish_annotated_stream(frame: np.ndarray, result, status_text: str) -> np.ndarray:
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
        cx_int, cy_int = int(frame_center_x), int(frame_center_y)
        cv2.drawMarker(annotated, (cx_int, cy_int), (0, 255, 255), cv2.MARKER_CROSS, 20, 1)

        cv2.rectangle(annotated, (10, 10), (frame_width - 10, 48), (20, 20, 20), -1)
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
            ros_img = bridge.cv2_to_imgmsg(annotated, encoding="bgr8")
            ros_img.header.stamp = handler.node.get_clock().now().to_msg()
            ros_img.header.frame_id = "bebop_camera"
            image_pub.publish(ros_img)
        except Exception as exc:
            logger.warning("Annotated stream publishing exception: %s", exc)
        return annotated

    current_tilt_deg: float = GIMBAL_SEARCH_TILT_DEG
    state: MissionState = MissionState.IDLE
    target_confirmed: bool = False
    rtl_dispatched: bool = False

    try:
        # Pre-align gimbal to initial search inclination
        actuator.camera_control(tilt=current_tilt_deg, pan=0.0)

        # ===================================================================
        # STEP 1 / FUNCTION 1: IMU Calibration, Zero Altitude, Takeoff & Stabilize
        # ===================================================================
        state = MissionState.CALIBRATING_FLAT_TRIM
        logger.info("--- [STEP 1 / FUNCTION 1]: Executing IMU Flat Trim and Ground Reference Calibration ---")
        actuator.flat_trim()
        actuator.delay(2.0)
        odom_supervisor.calibrate_ground_reference()
        logger.info("Flat trim completed. Relative ground launch reference established at (0, 0, 0).")

        state = MissionState.TAKEOFF_AND_STABILIZING
        logger.info(
            "Issuing Takeoff command (Target Altitude: %.2f m, Ceiling: %.2f m)...",
            args.height, odom_supervisor.altitude_ceiling,
        )
        if not actuator.takeoff(altitude=args.height):
            raise RuntimeError("Autonomous takeoff command rejected by Bebop platform.")

        logger.info("Stabilizing in hover for %.1f s and polling camera stream...", DEFAULT_TAKEOFF_STABILIZE_SEC)
        # Active frame polling during hover stabilization
        stabilize_start = time.time()
        while (time.time() - stabilize_start) < DEFAULT_TAKEOFF_STABILIZE_SEC:
            poll_frame = handler.take_photo(timeout_sec=0.5)
            if poll_frame is not None:
                failsafe.notify_frame_received()
            time.sleep(0.05)

        # Post-takeoff timestamp refresh
        failsafe.notify_frame_received()

        # Verify safety bounds post-takeoff (only in real flight)
        if not actuator.no_fly:
            healthy, reason = failsafe.evaluate_system_health()
            if not healthy:
                failsafe.trigger_emergency_land(reason)
                return

        logger.info(
            "Step 1 Complete: Stabilized at relative altitude %.2f m (Safety Ceiling: %.2f m).",
            odom_supervisor.relative_altitude, odom_supervisor.altitude_ceiling,
        )

        # ===================================================================
        # STEP 2 / FUNCTION 2: Linear Forward Search with Strict Kinematic Lock
        # ===================================================================
        state = MissionState.FORWARD_SEARCH
        logger.info(
            "--- [STEP 2 / FUNCTION 2]: Initiating Linear Search (Timeout: %.1f s, Velocity: %.3f) ---",
            args.search_timeout, args.velocity,
        )
        logger.info("Kinematic Constraint Active: vz <= 0.0, vyaw = 0.0 strictly enforced.")

        search_start_time = time.time()
        consecutive_detections: int = 0
        failsafe.notify_frame_received()

        while (time.time() - search_start_time) < args.search_timeout:
            if not actuator.no_fly:
                healthy, reason = failsafe.evaluate_system_health()
                if not healthy:
                    failsafe.trigger_emergency_land(reason)
                    return

            frame = handler.take_photo(timeout_sec=1.0)
            if frame is None:
                continue
            failsafe.notify_frame_received()

            result = detector.detect(frame, conf=args.confidence)
            targets = result.filter_by_class(TARGET_CLASSES)

            # Active anti-climb correction
            vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)

            elapsed = time.time() - search_start_time
            mode_prefix = "[NO-FLY] " if actuator.no_fly else ""
            telemetry_text = (
                f"{mode_prefix}STEP 2: SEARCH ({elapsed:.1f}s/{args.search_timeout:.1f}s) "
                f"| TILT: {current_tilt_deg:.1f}deg | ALT_REL: {odom_supervisor.relative_altitude:.2f}m"
            )
            publish_annotated_stream(frame, result, telemetry_text)

            if targets:
                consecutive_detections += 1
                best_target = max(targets, key=lambda d: d.confidence)
                logger.info(
                    "Target detected (%d/%d): class='%s', confidence=%.2f, relative_alt=%.2f m",
                    consecutive_detections,
                    DETECTION_CONFIRMATION_FRAMES,
                    best_target.class_name,
                    best_target.confidence,
                    odom_supervisor.relative_altitude,
                )
                if consecutive_detections >= DETECTION_CONFIRMATION_FRAMES:
                    target_confirmed = True
                    logger.info("Step 2 Complete: Target '%s' confirmed. Halting search translation.", best_target.class_name)
                    failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                    break
            else:
                consecutive_detections = 0
                failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                # Gentle cruise search speed
                actuator.move_velocity(vx=args.velocity, vy=0.0, vz=vz_cmd, vyaw=0.0)
                time.sleep(0.04)

        # ===================================================================
        # STEP 3 / FUNCTION 3: Visual Servoing, Gimbal Pitch & Coupled Guidance
        # ===================================================================
        if target_confirmed:
            state = MissionState.TARGET_APPROACH
            logger.info("--- [STEP 3 / FUNCTION 3]: High-Bandwidth Visual Servoing & Coupled Guidance ---")
            logger.info("Tracking objective: pitch gimbal from -20.0 deg to -80.0 deg with target centered.")

            # Gentle lateral micro-correction PID
            pid_lateral = PIDController(
                kp=LATERAL_PID_KP,
                ki=0.0,
                kd=LATERAL_PID_KD,
                setpoint=0.0,
                output_limits=(-LATERAL_OUTPUT_LIMIT, LATERAL_OUTPUT_LIMIT),
                output_deadband=LATERAL_DEADBAND,
            )
            # High-responsiveness gimbal PID
            pid_gimbal = PIDController(
                kp=GIMBAL_PID_KP,
                ki=0.0,
                kd=GIMBAL_PID_KD,
                setpoint=0.0,
                output_limits=(-GIMBAL_OUTPUT_LIMIT, GIMBAL_OUTPUT_LIMIT),
            )
            pid_lateral.reset()
            pid_gimbal.reset()

            last_confirmed_tilt_deg: float = current_tilt_deg
            tracking_start_time = time.time()
            approach_finished: bool = False
            failsafe.notify_frame_received()

            while (time.time() - tracking_start_time) < DEFAULT_TRACKING_TIMEOUT_SEC:
                if not actuator.no_fly:
                    healthy, reason = failsafe.evaluate_system_health()
                    if not healthy:
                        failsafe.trigger_emergency_land(reason)
                        return

                frame = handler.take_photo(timeout_sec=1.0)
                if frame is None:
                    continue
                failsafe.notify_frame_received()

                result = detector.detect(frame, conf=args.confidence)
                targets = result.filter_by_class(TARGET_CLASSES)

                # Active anti-climb regulation
                vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)

                mode_prefix = "[NO-FLY] " if actuator.no_fly else ""
                telemetry_text = (
                    f"{mode_prefix}STEP 3: TRACKING | TILT: {current_tilt_deg:.1f}deg "
                    f"| ALT_REL: {odom_supervisor.relative_altitude:.2f}m"
                )
                publish_annotated_stream(frame, result, telemetry_text)

                if targets:
                    primary_target = max(targets, key=lambda d: d.confidence)
                    t_cx, t_cy = primary_target.center
                    err_x = float(t_cx - frame_center_x)
                    err_y = float(t_cy - frame_center_y)
                    norm_err_x = err_x / frame_center_x
                    norm_err_y = err_y / frame_center_y
                    total_pixel_error = math.hypot(err_x, err_y)

                    # Update and store valid target tilt orientation
                    last_confirmed_tilt_deg = current_tilt_deg

                    # 1. Lateral Velocity (vy): corrects horizontal offset (vyaw remains 0.0)
                    # Clamped to LATERAL_OUTPUT_LIMIT (0.04)
                    vy_cmd = pid_lateral.update(norm_err_x)

                    # 2. Camera Gimbal Pitch PID:
                    gimbal_delta_deg = pid_gimbal.update(norm_err_y)
                    desired_tilt_deg = current_tilt_deg + gimbal_delta_deg
                    current_tilt_deg = max(GIMBAL_NADIR_TILT_DEG, min(GIMBAL_SEARCH_TILT_DEG, desired_tilt_deg))
                    # Actuate camera gimbal (physically executed in both real flight and --no-fly!)
                    actuator.camera_control(tilt=current_tilt_deg, pan=0.0)

                    # 3. Termination Condition: Nadir Position (-80 deg) and Centered Target
                    if (current_tilt_deg <= (GIMBAL_NADIR_TILT_DEG + 1.5)) and (total_pixel_error <= (OPTICAL_CENTER_TOLERANCE_PX * 1.5)):
                        logger.info(
                            "Step 3 Complete: Target aligned at Nadir (-80.0 deg) with radial error %.1f px.",
                            total_pixel_error,
                        )
                        current_tilt_deg = GIMBAL_NADIR_TILT_DEG
                        actuator.camera_control(tilt=current_tilt_deg, pan=0.0)
                        failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                        actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                        approach_finished = True
                        break

                    # 4. Forward Velocity (vx) Strictly Coupled to Visual Centering:
                    # If target is outside APPROACH_CENTERING_TOLERANCE_PX (25px), drone halts (vx = 0.0)
                    # to allow the gimbal to re-align. Forward speed is capped at MAX_APPROACH_FORWARD_SPEED (0.04).
                    if abs(err_y) < APPROACH_CENTERING_TOLERANCE_PX:
                        alignment_quality = (1.0 - (abs(err_y) / APPROACH_CENTERING_TOLERANCE_PX)) ** 2
                        angular_headroom = (current_tilt_deg - GIMBAL_NADIR_TILT_DEG) / (GIMBAL_SEARCH_TILT_DEG - GIMBAL_NADIR_TILT_DEG)
                        vx_cmd = min(MAX_APPROACH_FORWARD_SPEED, MAX_APPROACH_FORWARD_SPEED * angular_headroom * alignment_quality)
                    else:
                        vx_cmd = 0.0

                    failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    actuator.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=vz_cmd, vyaw=0.0)
                    time.sleep(0.03)

                else:
                    # Target Lost: Enter Deterministic Recovery Sub-routine
                    state = MissionState.TARGET_RECOVERY
                    logger.warning(
                        "Target lost during approach. Halting translation and reverting gimbal to last confirmed tilt (%.1f deg).",
                        last_confirmed_tilt_deg,
                    )
                    failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

                    # Revert gimbal to last known target observation orientation
                    current_tilt_deg = last_confirmed_tilt_deg
                    actuator.camera_control(tilt=current_tilt_deg, pan=0.0)

                    recovery_start_time = time.time()
                    target_recovered: bool = False

                    while (time.time() - recovery_start_time) < TARGET_RECOVERY_TIMEOUT_SEC:
                        if not actuator.no_fly:
                            healthy, reason = failsafe.evaluate_system_health()
                            if not healthy:
                                failsafe.trigger_emergency_land(reason)
                                return

                        vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)
                        failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                        actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

                        rec_frame = handler.take_photo(timeout_sec=1.0)
                        if rec_frame is None:
                            continue
                        failsafe.notify_frame_received()

                        rec_result = detector.detect(rec_frame, conf=args.confidence)
                        rec_targets = rec_result.filter_by_class(TARGET_CLASSES)

                        telemetry_text = (
                            f"{mode_prefix}TARGET RECOVERY | TILT: {current_tilt_deg:.1f}deg "
                            f"| ALT_REL: {odom_supervisor.relative_altitude:.2f}m"
                        )
                        publish_annotated_stream(rec_frame, rec_result, telemetry_text)

                        if rec_targets:
                            logger.info("Target successfully re-acquired during recovery window. Resuming PID tracking.")
                            pid_gimbal.reset()
                            pid_lateral.reset()
                            target_recovered = True
                            state = MissionState.TARGET_APPROACH
                            break
                        time.sleep(0.04)

                    if not target_recovered:
                        logger.error(
                            "Recovery failed: Target not found within %.1f s recovery window. Aborting to RTL.",
                            TARGET_RECOVERY_TIMEOUT_SEC,
                        )
                        break

            if not approach_finished:
                logger.warning("Step 3 did not achieve full nadir lock. Transitioning directly to RTL.")

            else:
                # ===========================================================
                # STEP 4 / FUNCTION 4: Motionless Nadir Hover & High-Res Capture
                # ===========================================================
                state = MissionState.HOVER_INSPECTION
                logger.info(
                    "--- [STEP 4 / FUNCTION 4]: Hovering at Nadir (-80.0 deg) for %.1f s and Capturing Evidence ---",
                    args.hover_duration,
                )

                # Ensure zero horizontal translation, active anti-climb vz
                vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)
                failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

                hover_start_time = time.time()
                evidence_captured: bool = False
                failsafe.notify_frame_received()

                while (time.time() - hover_start_time) < args.hover_duration:
                    if not actuator.no_fly:
                        healthy, reason = failsafe.evaluate_system_health()
                        if not healthy:
                            failsafe.trigger_emergency_land(reason)
                            return

                    vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)
                    failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                    actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)

                    frame = handler.take_photo(timeout_sec=1.0)
                    if frame is not None:
                        failsafe.notify_frame_received()
                        result = detector.detect(frame, conf=args.confidence)
                        targets = result.filter_by_class(TARGET_CLASSES)

                        elapsed_hover = time.time() - hover_start_time
                        telemetry_text = (
                            f"{mode_prefix}STEP 4: HOVER INSPECTION ({elapsed_hover:.1f}s/{args.hover_duration:.1f}s) "
                            f"| TILT: -80.0deg | ALT_REL: {odom_supervisor.relative_altitude:.2f}m"
                        )
                        annotated = publish_annotated_stream(frame, result, telemetry_text)

                        if not evidence_captured and targets:
                            # 1. Trigger onboard Bebop 14MP camera hardware capture
                            actuator.snapshot()
                            # 2. Record lossless local frame and annotated image to disk
                            record_photographic_evidence(frame, annotated)
                            evidence_captured = True
                            logger.info("Step 4: High-resolution photographic evidence successfully recorded.")

                    time.sleep(0.08)

                if not evidence_captured:
                    fallback_frame = handler.take_photo(timeout_sec=1.0)
                    if fallback_frame is not None:
                        actuator.snapshot()
                        fallback_result = detector.detect(fallback_frame, conf=args.confidence)
                        fallback_annotated = detector.draw_detections(
                            image=fallback_frame.copy(),
                            result=fallback_result,
                            show_labels=True,
                            show_confidence=True,
                            show_class=True,
                        )
                        record_photographic_evidence(fallback_frame, fallback_annotated)
                        logger.info("Step 4: Evidence recorded via fallback capture at conclusion of hover.")

                logger.info("Step 4 Complete: Stationary inspection window concluded.")

        else:
            logger.warning("Step 2 concluded without target confirmation (Search timeout exceeded).")

        # ===================================================================
        # STEP 5 / FUNCTION 5: Closed-Loop Odometry RTL and Safe Landing
        # ===================================================================
        state = MissionState.RETURN_TO_LAUNCH
        logger.info("--- [STEP 5 / FUNCTION 5]: Initiating Closed-Loop Odometry RTL to Takeoff Origin ---")

        # 1. Re-orient gimbal to safe frontal transit angle
        actuator.camera_control(tilt=GIMBAL_SEARCH_TILT_DEG, pan=0.0)

        # 2. Active closed-loop odometry navigation back to (x0, y0)
        rtl_start_time = time.time()
        reached_takeoff_origin: bool = False

        while (time.time() - rtl_start_time) < RTL_TIMEOUT_SEC:
            if not actuator.no_fly:
                # During RTL we navigate by odometry; check odometry health specifically
                if not odom_supervisor.is_telemetry_healthy():
                    failsafe.trigger_emergency_land("Odometry telemetry loss during RTL.")
                    return
                if odom_supervisor.is_ceiling_breached():
                    failsafe.trigger_emergency_land("Ceiling breached during RTL.")
                    return

            ex_body, ey_body, dist_to_launch = odom_supervisor.get_body_frame_launch_error()
            vz_cmd = altitude_governor.compute_vz(odom_supervisor.relative_altitude)

            mode_prefix = "[NO-FLY] " if actuator.no_fly else ""
            logger.info(
                "%sRTL Navigating: dist_to_launch=%.2f m (target <= %.2f m) | err_body=(%.2f, %.2f) m | alt_rel=%.2f m",
                mode_prefix, dist_to_launch, RTL_ARRIVAL_RADIUS_M, ex_body, ey_body, odom_supervisor.relative_altitude,
            )

            # Check convergence to takeoff origin
            if dist_to_launch <= RTL_ARRIVAL_RADIUS_M:
                logger.info(
                    "RTL Target Reached: Drone arrived at launch origin (dist=%.2f m <= %.2f m). Halting translation.",
                    dist_to_launch, RTL_ARRIVAL_RADIUS_M,
                )
                failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
                actuator.move_velocity(vx=0.0, vy=0.0, vz=vz_cmd, vyaw=0.0)
                reached_takeoff_origin = True
                break

            # Gentle, low-speed proportional navigation in body frame
            vx_cmd = max(-RTL_MAX_SPEED, min(RTL_MAX_SPEED, RTL_KP * ex_body))
            vy_cmd = max(-RTL_MAX_SPEED, min(RTL_MAX_SPEED, RTL_KP * ey_body))

            failsafe.assert_kinematics(vz=vz_cmd, vyaw=0.0)
            actuator.move_velocity(vx=vx_cmd, vy=vy_cmd, vz=vz_cmd, vyaw=0.0)
            time.sleep(0.04)

        if not reached_takeoff_origin:
            logger.warning("RTL navigation window (%.1f s) concluded. Forcing station-keeping before land.", RTL_TIMEOUT_SEC)

        # 3. Final hover stabilization over takeoff point
        logger.info("Executing final station-keeping hover over launch origin for 2.0 s...")
        actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        actuator.delay(2.0)

        # 4. Terminal landing sequence
        state = MissionState.SAFE_LANDING
        logger.info("Executing safe terminal landing at takeoff origin...")
        actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        actuator.land()
        rtl_dispatched = True
        logger.info("Step 5 Complete: Safe landing finalized and actuators disarmed.")
        state = MissionState.COMPLETED

    except KeyboardInterrupt:
        # Re-raise to trigger top-level emergency landing handler
        raise

    except Exception as exc:
        logger.critical("Critical runtime exception during mission execution: %s", exc, exc_info=True)
        failsafe.trigger_emergency_land(str(exc))

    finally:
        logger.info("Commencing resource de-allocation and node shutdown...")
        if not rtl_dispatched and state != MissionState.COMPLETED:
            actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            actuator.land()
        handler.cleanup()
        logger.info("Mission termination cleanup complete.")


def main() -> None:
    """CLI parsing and lifecycle management for autonomous Bebop 2 mission."""
    parser = argparse.ArgumentParser(
        description="Parrot Bebop 2 autonomous accident inspection and tracking mission via Nectar SDK."
    )
    parser.add_argument(
        "--height",
        type=float,
        default=DEFAULT_TARGET_ALTITUDE_M,
        help="Target takeoff altitude and inviolable safety ceiling in meters (default: %(default)s).",
    )
    parser.add_argument(
        "--velocity",
        type=float,
        default=DEFAULT_CRUISE_VELOCITY,
        help="Normalized cruise velocity [-1.0, 1.0] for forward search (default: %(default)s).",
    )
    parser.add_argument(
        "--search-timeout",
        type=float,
        default=DEFAULT_SEARCH_TIMEOUT_SEC,
        help="Search phase timeout in seconds before aborting to RTL (default: %(default)s).",
    )
    parser.add_argument(
        "--hover-duration",
        type=float,
        default=DEFAULT_HOVER_DURATION_SEC,
        help="Duration in seconds for stationary nadir inspection hover (default: %(default)s).",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=DEFAULT_DETECTION_CONFIDENCE,
        help="YOLO detection confidence threshold (default: %(default)s).",
    )
    parser.add_argument(
        "--model-path",
        default="yolov8n.pt",
        help="YOLO weights file path (default: %(default)s).",
    )
    parser.add_argument(
        "--ip",
        default=BEBOP_DEFAULT_IP,
        help="Bebop 2 Wi-Fi IP address (default: %(default)s).",
    )
    parser.add_argument(
        "--detection-topic",
        default=DETECTION_STREAM_TOPIC,
        help="ROS 2 topic for publishing the real-time annotated image stream (default: %(default)s).",
    )
    parser.add_argument(
        "--no-fly",
        action="store_true",
        help="Benchtop execution mode: runs vision, gimbal actuation, and detection without spinning motors.",
    )
    args = parser.parse_args()

    nectar.init()
    config = BebopConfig(
        name="bebop_mission",
        start_driver=False,
        ip=args.ip,
        namespace=BEBOP_NAMESPACE,
    )
    drone = DroneFactory.create("bebop", config)

    # -----------------------------------------------------------------------
    # Emergency Ctrl+C (SIGINT / SIGTERM) Safe Landing Manager
    # STRICT RULE: NEVER kill motors in mid-air. Always execute controlled land.
    # -----------------------------------------------------------------------
    emergency_landing_in_progress = False

    def handle_operator_emergency(signum=None, frame=None) -> None:
        nonlocal emergency_landing_in_progress
        logger.critical("=" * 65)
        logger.critical("EMERGENCY SIGNAL (Ctrl+C / SIGINT) DETECTED FROM OPERATOR!")
        logger.critical("Halting all velocities and executing immediate controlled safe landing...")
        logger.critical("=" * 65)

        # Repeatedly send zero velocity and landing command
        try:
            for _ in range(5):
                drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
                drone.land()
                time.sleep(0.06)
            logger.info("Emergency land command burst transmitted. Flushing network buffers...")
            time.sleep(1.2)
        except Exception as exc:
            logger.error("Error during emergency land dispatch: %s", exc)

        if not emergency_landing_in_progress:
            emergency_landing_in_progress = True
            try:
                drone.cleanup()
                nectar.shutdown()
            except Exception:
                pass
            logger.info("Emergency safe landing sequence finalized. Exiting.")
            sys.exit(0)

    # Intercept OS signals immediately
    signal.signal(signal.SIGINT, handle_operator_emergency)
    signal.signal(signal.SIGTERM, handle_operator_emergency)

    try:
        if not drone.connect():
            logger.error(
                "Driver connection failure. Ensure ros2_bebop_driver is running:\n"
                "  ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=%s",
                args.ip,
            )
            sys.exit(1)

        actuator = FlightActuatorProxy(drone, no_fly=getattr(args, "no_fly", False))
        execute_mission(drone, actuator, args)

    except KeyboardInterrupt:
        handle_operator_emergency()
    finally:
        if not emergency_landing_in_progress:
            drone.cleanup()
            nectar.shutdown()


if __name__ == "__main__":
    main()
