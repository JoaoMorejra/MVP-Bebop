#!/usr/bin/env python3
"""Autonomous Search, Tracking, Nadir Inspection and RTL Mission for Parrot Bebop 2.

Entry point executing the deterministic 5-step mission pipeline via Nectar SDK.
"""

from __future__ import annotations

import argparse
import logging
import sys

from nav_msgs.msg import Odometry

import nectar
from nectar.ai.detection import Detector
from nectar.control import BebopConfig, DroneFactory
from nectar.vision import ImageHandler, QoSReliability, ROSConfig

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.parameters import MissionParameters
from mvp_mission_bebop.steps import (
    ClosedLoopRTLStep,
    ForwardSearchStep,
    NadirInspectionStep,
    TakeoffStep,
    VisualServoingStep,
)
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BebopMission")


def parse_arguments(default_params: MissionParameters) -> argparse.Namespace:
    """Parse command-line arguments and map to mission parameters."""
    parser = argparse.ArgumentParser(
        description="Parrot Bebop 2 autonomous inspection mission via Nectar SDK."
    )
    parser.add_argument(
        "--height",
        type=float,
        default=None,
        help="Target takeoff altitude and safety ceiling (default: %(default)s m).",
    )
    parser.add_argument(
        "--velocity",
        type=float,
        default=None,
        help="Cruise velocity for forward search (default: %(default)s).",
    )
    parser.add_argument(
        "--search-timeout",
        type=float,
        default=None,
        help="Search phase timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--hover-duration",
        type=float,
        default=None,
        help="Stationary nadir inspection hover duration (default: %(default)s s).",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="YOLO detection confidence threshold (default: %(default)s).",
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="YOLO weights file path.",
    )
    parser.add_argument(
        "--ip",
        default=None,
        help="Bebop 2 Wi-Fi IP address.",
    )
    parser.add_argument(
        "--detection-topic",
        default=None,
        help="ROS 2 topic for publishing the annotated stream.",
    )
    parser.add_argument(
        "--no-fly",
        action="store_true",
        default=None,
        help="Benchtop simulation mode: vision & gimbal active without spinning motors.",
    )
    parser.add_argument(
        "--countdown",
        type=float,
        default=None,
        help="Pre-flight countdown synchronization in seconds.",
    )
    parser.add_argument(
        "--arrival-radius",
        type=float,
        default=None,
        help="RTL landing arrival radius in meters.",
    )
    parser.add_argument(
        "--rtl-velocity",
        type=float,
        default=None,
        help="Cruise velocity for return to launch.",
    )
    parser.add_argument(
        "--params-json",
        default=None,
        help="JSON string with custom mission parameters overrides.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI initialization and mission lifecycle execution."""
    import os
    config_file = os.path.join(os.path.dirname(__file__), "mission_config.json")
    params = MissionParameters.load_from_file(config_file)
    args = parse_arguments(params)

    if args.params_json:
        try:
            import json
            custom_data = json.loads(args.params_json)
            params.update_from_dict(custom_data)
        except Exception as e:
            logger.warning("Failed to apply custom params JSON: %s", e)

    if args.height is not None:
        params.kinematics.target_altitude_m = args.height
    if args.velocity is not None:
        params.kinematics.forward_cruise_velocity = args.velocity
    if getattr(args, "rtl_velocity", None) is not None:
        params.rtl.max_speed = args.rtl_velocity
    if args.search_timeout is not None:
        params.timeouts.search_timeout_sec = args.search_timeout
    if args.hover_duration is not None:
        params.kinematics.hover_duration_sec = args.hover_duration
    if args.confidence is not None:
        params.vision.confidence_threshold = args.confidence
    if args.arrival_radius is not None:
        params.rtl.arrival_radius_m = args.arrival_radius
    if args.model_path:
        params.vision.model_path = args.model_path
    if args.ip:
        params.network.drone_ip = args.ip
    if args.detection_topic:
        params.network.detection_stream_topic = args.detection_topic
    if args.no_fly:
        params.no_fly = args.no_fly
    if args.countdown is not None:
        params.kinematics.countdown_sec = args.countdown

    try:
        params.save_to_file(config_file)
    except Exception as save_err:
        logger.warning("Could not persist active mission config: %s", save_err)

    logger.info(
        "Active parameters: altitude=%.2fm, velocity=%.3fm/s, hover=%.1fs, "
        "search_timeout=%.1fs, confidence=%.2f, confirmation_frames=%d, "
        "classes=%s, rtl_radius=%.2fm, countdown=%.1fs, no_fly=%s",
        params.kinematics.target_altitude_m,
        params.kinematics.forward_cruise_velocity,
        params.kinematics.hover_duration_sec,
        params.timeouts.search_timeout_sec,
        params.vision.confidence_threshold,
        params.vision.confirmation_frames,
        params.vision.target_classes,
        params.rtl.arrival_radius_m,
        params.kinematics.countdown_sec,
        params.no_fly,
    )

    try:
        from mvp_mission_bebop.telemetry.announcer import announce_sync
        announce_sync(
            "Iniciando Missão",
            details={"etapa": "iniciando missão, carregando parâmetros"},
            wait=False,
        )
    except Exception as vocal_err:
        logger.debug("Announce dispatch failure: %s", vocal_err)

    logger.info("Initializing Nectar SDK runtime...")
    nectar.init()

    config = BebopConfig(
        name="bebop_mission",
        start_driver=False,
        ip=params.network.drone_ip,
        namespace=params.network.namespace,
    )
    raw_drone = DroneFactory.create("bebop", config)
    actuator = BenchtopDroneProxy(raw_drone, no_fly=params.no_fly)

    if not actuator.connect():
        erro_msg = f"Falha de conexão com driver do drone no IP {params.network.drone_ip}"
        logger.error(
            "Driver connection failure. Ensure ros2_bebop_driver is running:\n"
            "  ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=%s",
            params.network.drone_ip,
        )
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Falha na inicialização",
                details={"erro": erro_msg},
                priority="CRITICAL",
                wait=True,
            )
        except Exception:
            pass
        nectar.shutdown()
        sys.exit(1)

    # Initialize perception
    logger.info("Loading YOLO detector: %s...", params.vision.model_path)
    detector = Detector(params.vision.model_path, confidence_threshold=params.vision.confidence_threshold)
    detector.load()

    cam_config = ROSConfig(
        topic=params.network.camera_raw_topic,
        compressed=False,
        reliability=QoSReliability.BEST_EFFORT,
    )
    handler = ImageHandler(image_source=params.network.camera_raw_topic, config=cam_config)
    handler.open()

    sample_frame = handler.take_photo(timeout_sec=2.5)
    if sample_frame is None:
        if params.no_fly:
            logger.info("[NO-FLY BENCHTOP] Physical camera not available. Utilizing benchtop test frame.")
            import os
            import cv2
            import numpy as np
            test_img_path = os.path.join(os.path.dirname(__file__), "accident_raw_20260903_033713.png")
            if not os.path.exists(test_img_path):
                test_img_path = os.path.join(os.path.dirname(__file__), "accident_capture_20260901_031108.jpg")
            if os.path.exists(test_img_path):
                sample_frame = cv2.imread(test_img_path)
            if sample_frame is None:
                sample_frame = np.zeros((480, 856, 3), dtype=np.uint8)

            # Supply benchtop frames continuously without timeout log warnings
            handler.take_photo = lambda timeout_sec=1.0: sample_frame.copy()
        else:
            logger.critical("Fatal: Unable to receive initial video frame. Aborting.")
            handler.cleanup()
            raw_drone.cleanup()
            nectar.shutdown()
            sys.exit(1)

    frame_height, frame_width = sample_frame.shape[:2]

    # Initialize telemetry and supervisory nodes
    odom_supervisor = OdometrySupervisor(
        kinematics_cfg=params.kinematics,
        timeouts_cfg=params.timeouts,
    )
    handler.node.create_subscription(
        Odometry,
        params.network.odometry_topic,
        odom_supervisor.odometry_callback,
        10,
    )

    governor = AltitudeAntiClimbGovernor(
        target_altitude=params.kinematics.target_altitude_m,
        config=params.governor,
    )
    failsafe = FailsafeSupervisor(
        drone_actuator=actuator,
        odom_supervisor=odom_supervisor,
        timeouts_cfg=params.timeouts,
    )
    visual_controller = VisualServoingController(
        gimbal_config=params.gimbal,
        gimbal_pid_cfg=params.gimbal_pid,
        lateral_pid_cfg=params.lateral_pid,
        vision_cfg=params.vision,
        kinematics_cfg=params.kinematics,
    )

    ctx = MissionContext(
        drone=actuator,
        detector=detector,
        handler=handler,
        odom_supervisor=odom_supervisor,
        governor=governor,
        failsafe=failsafe,
        visual_controller=visual_controller,
        parameters=params,
        frame_width=frame_width,
        frame_height=frame_height,
    )

    steps = [
        TakeoffStep(),
        ForwardSearchStep(),
        VisualServoingStep(),
        NadirInspectionStep(),
        ClosedLoopRTLStep(),
    ]

    runner = MissionRunner(context=ctx, steps=steps)

    try:
        runner.run()
    finally:
        raw_drone.cleanup()
        nectar.shutdown()


if __name__ == "__main__":
    main()
