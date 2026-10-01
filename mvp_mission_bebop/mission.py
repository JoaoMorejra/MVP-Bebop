#!/usr/bin/env python3
"""Autonomous Search, Tracking, Nadir Inspection and RTL Mission for Parrot Bebop 2.

Entry point executing the deterministic 5-step mission pipeline via Nectar SDK.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from typing import List, Optional

from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Int32, String, UInt8
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data

import nectar
from nectar.control import BebopConfig, DroneFactory
from nectar.vision import ImageHandler, QoSReliability, ROSConfig

from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy
from mvp_mission_bebop.actuators.simulator import KinematicSimulator
from mvp_mission_bebop.config_audit import log_envelope_divergences
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.visual_servoing import VisualServoingController
from mvp_mission_bebop.engine.runner import MissionRunner
from mvp_mission_bebop.engine.stage_gate import StageRequestHandler
from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.estimation.dead_reckoning import DeadReckoningTracker
from mvp_mission_bebop.parameters import BatteryConfig, MissionParameters, VisionConfig
from mvp_mission_bebop.perception.inference_device import (
    CUDA_WARMUP_SAMPLES,
    available_devices,
    build_device_detector,
    model_cache_key,
    normalize_inference_device,
    select_detector,
)
from mvp_mission_bebop.perception.worker import PerceptionWorker, detector_kwargs, normalize_imgsz
from mvp_mission_bebop.steps import (
    ClosedLoopRTLStep,
    ForwardSearchStep,
    NadirInspectionStep,
    TakeoffStep,
    VisualServoingStep,
)
from mvp_mission_bebop.telemetry.battery import (
    BATTERY_WARNING_PCT,
    BatterySupervisor,
    normalize_percentage,
)
from mvp_mission_bebop.telemetry.bench import BenchTelemetryPublisher
from mvp_mission_bebop.telemetry.failsafe import FailsafeSupervisor
from mvp_mission_bebop.telemetry.milestones import emit_milestone
from mvp_mission_bebop.telemetry.mission_parameters import spoken_parameters
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor
from mvp_mission_bebop.telemetry.picture_ack import PictureAckTracker

# Mission logs go to stdout deliberately. The Electron GCS registers its step
# matcher on the child's stdout pipe and looks for the literal "[STEP N:"
# (electron/main.cjs:698-716), while basicConfig defaults to stderr -- so the
# bmg:step-change event could never fire. Both pipes are forwarded to the
# renderer, so the only visible change is that step tracking now works.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
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
        "--fly",
        action="store_true",
        default=None,
        help=(
            "Arm the motors for a real flight, overriding a persisted no_fly. "
            "Required because --no-fly could previously only ever be set, never cleared."
        ),
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
        "--aruco-id",
        type=int,
        default=None,
        help=(
            "ID do marcador ArUco de decolagem e pouso para a etapa de RTL "
            "(default: %(default)s)."
        ),
    )
    def _parse_dict_arg(value: str):
        try:
            return int(value)
        except ValueError:
            return value

    parser.add_argument(
        "--aruco-dict",
        type=_parse_dict_arg,
        default=None,
        help=(
            "ArUco/AprilTag dictionary: integer order (4-7), OpenCV enum code "
            "(e.g. 20), or string name (e.g. DICT_APRILTAG_36h11, tag36h11)."
        ),
    )
    parser.add_argument(
        "--aruco-size",
        type=float,
        default=None,
        help="Tamanho físico do marcador ArUco em metros (default: 0.20m).",
    )
    parser.add_argument(
        "--params-json",
        default=None,
        help="JSON string with custom mission parameters overrides.",
    )
    parser.add_argument(
        "--stages",
        default=None,
        help=(
            "Comma-separated subset of the five stages to execute, in the order "
            "given (e.g. --stages 2 or --stages 1,4). Intended for benchtop "
            "rehearsal of a single routine; omitting it flies the whole sequence."
        ),
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Parameter store to load and rewrite (default: mission_config.json "
            "beside this module, which is the file the GCS reads)."
        ),
    )
    parser.add_argument(
        "--bench-frame",
        default=None,
        help=(
            "Image served as the camera under --no-fly when no video arrives "
            "(default: the bundled benchtop frame, else a black frame)."
        ),
    )
    return parser.parse_args()


#: The five deterministic stages, by the number the ground station shows.
STAGE_NUMBERS = (1, 2, 3, 4, 5)


def parse_stage_selection(raw: Optional[str]) -> Optional[List[int]]:
    """Read ``--stages`` into an ordered list of stage numbers.

    ``None`` means the whole sequence. The selection is taken literally and in
    the order written: a bench operator asking for stage 5 alone wants the RTL
    routine by itself, not the four stages that normally precede it.
    """
    if raw is None:
        return None
    selection: List[int] = []
    for token in str(raw).replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            number = int(token)
        except ValueError as error:
            raise SystemExit(f"--stages: '{token}' is not a stage number.") from error
        if number not in STAGE_NUMBERS:
            raise SystemExit(f"--stages: stage {number} does not exist (1-5).")
        if number not in selection:
            selection.append(number)
    if not selection:
        raise SystemExit("--stages was given but selects no stage.")
    return selection


def main() -> None:
    """CLI initialization and mission lifecycle execution."""
    args = parse_arguments(MissionParameters())
    config_file = args.config or os.path.join(os.path.dirname(__file__), "mission_config.json")
    params = MissionParameters.load_from_file(config_file)

    if args.params_json:
        # A malformed payload is fatal, not a warning. This carries the whole
        # parameter document the operator just edited in the GCS; swallowing it
        # and flying the file on disk instead is indistinguishable, from the
        # cockpit, from the interface having no effect at all.
        try:
            custom_data = json.loads(args.params_json)
        except json.JSONDecodeError as error:
            raise SystemExit(
                f"--params-json is not valid JSON ({error}); refusing to fly a "
                f"configuration the operator did not approve."
            ) from error
        if not isinstance(custom_data, dict):
            raise SystemExit(
                "--params-json must be a JSON object of MissionParameters fields."
            )
        params.update_from_dict(custom_data)

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
    # The three ArUco overrides are read through ``getattr`` with a default,
    # like ``--rtl-velocity`` above, so that a caller holding an older Namespace
    # -- a test, or a GCS build that predates these flags -- does not raise here.
    if getattr(args, "aruco_id", None) is not None:
        params.rtl.target_aruco_id = args.aruco_id
    if getattr(args, "aruco_dict", None) is not None:
        params.rtl.marker_dict = args.aruco_dict
    if getattr(args, "aruco_size", None) is not None:
        params.rtl.tag_size = args.aruco_size
    if args.model_path:
        params.vision.model_path = args.model_path
    if args.ip:
        params.network.drone_ip = args.ip
    if args.detection_topic:
        params.network.detection_stream_topic = args.detection_topic
    # ``--no-fly`` is ``store_true``, so it can only ever set the flag, and the
    # merged configuration is written back to disk a few lines below. One
    # benchtop run therefore used to stamp ``no_fly: true`` into
    # mission_config.json permanently, and every later invocation -- the real
    # flight included -- silently loaded it and reported a successful mission it
    # never flew. ``--fly`` is the missing counterpart, and the flag is excluded
    # from what gets persisted so the latch cannot form again.
    if args.fly and args.no_fly:
        parser_error = "--fly and --no-fly are mutually exclusive."
        raise SystemExit(parser_error)
    if args.no_fly:
        params.no_fly = True
    if args.fly:
        params.no_fly = False
    if args.countdown is not None:
        params.kinematics.countdown_sec = args.countdown

    try:
        # Persist the tuning, never the arming state: see the note above on the
        # one-way latch. ``no_fly`` is a per-invocation decision made at the
        # command line, so it is restored to whatever the file already held
        # before the file is rewritten.
        persisted = MissionParameters.load_from_file(config_file).no_fly if os.path.exists(
            config_file
        ) else False
        armed_for_this_run = params.no_fly
        params.no_fly = persisted
        params.save_to_file(config_file)
        params.no_fly = armed_for_this_run
    except Exception as save_err:
        logger.warning("Could not persist active mission config: %s", save_err)

    logger.info(
        "Active parameters: altitude=%.2fm, velocity=%.3fm/s, hover=%.1fs, "
        "search_timeout=%.1fs, confidence=%.2f, confirmation_frames=%d, "
        "classes=%s, rtl_radius=%.2fm, countdown=%.1fs, no_fly=%s, "
        "aruco_id=%d, aruco_dict=%s, aruco_size=%.3fm, stabilize=%.1fs, "
        "search_tilt=%.0fdeg, nadir_freeze_tilt=%.0fdeg (tol %.1fdeg), rtl_timeout=%.0fs",
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
        params.rtl.target_aruco_id,
        params.rtl.marker_dict,
        params.rtl.tag_size,
        params.kinematics.takeoff_stabilize_duration_sec,
        params.gimbal.search_tilt_deg,
        params.gimbal.nadir_tilt_deg,
        params.gimbal.nadir_tilt_tolerance_deg,
        params.rtl.timeout_sec,
    )
    # Advisory only: a value outside its documented envelope is reported on the
    # ground, where the operator can still act on it, and the mission proceeds.
    log_envelope_divergences(params)

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

    # Odometry is the only state feedback this airframe offers, and the SDK does
    # not subscribe to it: BebopDrone owns nine publishers and no subscribers.
    odom_supervisor = OdometrySupervisor(
        kinematics_cfg=params.kinematics,
        timeouts_cfg=params.timeouts,
        calibration_cfg=params.calibration,
    )
    calibration = SpeedCalibration.from_kinematics(params.kinematics)

    # Normalized on the ground, like the detector input size below: a malformed
    # threshold costs a log line here instead of disabling the net in flight.
    try:
        params.battery.land_pct = normalize_percentage(params.battery.land_pct, "battery.land_pct")
    except (TypeError, ValueError) as exc:
        fallback = BatteryConfig().land_pct
        logger.error("Invalid battery.land_pct (%s); using %.0f%%.", exc, fallback)
        params.battery.land_pct = fallback
    battery_supervisor = BatterySupervisor(params.battery)

    # D2: the station speaks the numbers of this document and of no other.
    # Emitted after every normalization above, so it is exactly what the steps
    # will fly.
    emit_milestone("mission.parameters", spoken_parameters(params, calibration))

    # The aggregated motion sequence. Fed by the actuator proxy, so every
    # ``move_velocity`` the mission transmits is accounted for without any step
    # having to remember to report itself, and armed by Stage 1 at the same
    # instant the horizontal origin is frozen -- the two have to agree on which
    # point ``(0, 0)`` is, or the return leg flies home to a place the drone was
    # never at.
    motion_tracker = (
        DeadReckoningTracker(calibration, params.dead_reckoning)
        if params.dead_reckoning.enabled
        else None
    )

    # Under --no-fly the simulator stands in for the airframe, integrating every
    # command and publishing synthetic odometry through the supervisor's public
    # ingress. It runs from takeoff onward, so by the time the return leg starts
    # the drone is genuinely displaced by the cruise rather than by a constant
    # injected for the occasion.
    simulator = KinematicSimulator(odom_supervisor, calibration) if params.no_fly else None
    actuator = BenchtopDroneProxy(
        raw_drone,
        no_fly=params.no_fly,
        simulator=simulator,
        motion_tracker=motion_tracker,
    )

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

    # Initialize perception. The input size is normalized here, before takeoff,
    # so a malformed value from the parameter sheet costs a log line on the
    # ground instead of an exception inside a flight stage. Falling back to the
    # native size is the conservative choice: it is the validated detector
    # configuration, only slower.
    try:
        params.vision.inference_imgsz = normalize_imgsz(params.vision.inference_imgsz)
    except (TypeError, ValueError) as exc:
        logger.error("Invalid vision.inference_imgsz (%s); using the model's native size.", exc)
        params.vision.inference_imgsz = None
    try:
        params.vision.inference_device = normalize_inference_device(params.vision.inference_device)
    except (TypeError, ValueError) as exc:
        logger.error("Invalid vision.inference_device (%s); using AUTO.", exc)
        params.vision.inference_device = "AUTO"
    inference_backends = available_devices()
    logger.info(
        "YOLO detector %s (input size %s), device %s; station devices %s.",
        params.vision.model_path,
        params.vision.inference_imgsz or "native",
        params.vision.inference_device,
        ", ".join(f"{name}={backend}" for name, backend in inference_backends.items()),
    )

    def _build_detector(device: str):
        return build_device_detector(
            device, params.vision.model_path, params.vision.confidence_threshold, inference_backends
        )

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
            import cv2
            import numpy as np
            test_img_path = args.bench_frame or os.path.join(
                os.path.dirname(__file__), "accident_raw_20260903_033713.png"
            )
            if not os.path.exists(test_img_path):
                if args.bench_frame:
                    logger.warning("Bench frame %s not found.", args.bench_frame)
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

    # Device selection doubles as the pre-flight warmup: every candidate runs
    # its first, expensive inferences here on the ground, on the first real
    # frame, under the mission's own load (D3, perception.inference_device).
    selection_started = time.monotonic()
    warm_kwargs = detector_kwargs(None, params.vision.inference_imgsz)
    detector, inference_report = select_detector(
        params.vision.inference_device,
        list(inference_backends),
        _build_detector,
        frame=sample_frame,
        cache_path=os.environ.get("BMG_INFERENCE_CACHE")
        or os.path.join(os.path.expanduser("~"), ".cache", "bmg", "inference_device.json"),
        cache_key=model_cache_key(params.vision.model_path, params.vision.inference_imgsz),
        detect_kwargs=warm_kwargs,
    )
    if detector.device == "CUDA":
        # Resident on the GPU with its kernels already selected, so the first
        # search cycle runs at full cadence.
        for _ in range(CUDA_WARMUP_SAMPLES):
            detector.detect(sample_frame, **warm_kwargs)
    chosen = inference_report["candidates"].get(detector.device, {})
    logger.info(
        "[TIMING] inference_device=%s p50_ms=%s p95_ms=%s selection_ms=%d cached=%s candidates=%s",
        detector.device,
        chosen.get("p50_ms"),
        chosen.get("p95_ms"),
        int((time.monotonic() - selection_started) * 1000.0),
        inference_report["cached"],
        json.dumps(inference_report["candidates"]),
    )

    # Telemetry gets its own node rather than riding on the camera handler's.
    # Both are registered with the same shared Nectar executor, so this adds no
    # spin loop -- it just stops an odometry subscription from sharing a
    # lifecycle with the video pipeline.
    telemetry_node = Node("bebop_mission_telemetry", start_parameter_services=False)
    if simulator is None:
        telemetry_node.create_subscription(
            Odometry,
            params.network.odometry_topic,
            odom_supervisor.odometry_callback,
            qos_profile_sensor_data,
        )
    else:
        # The simulator is the only writer of the supervisor on the bench. With
        # the aircraft powered on the table the driver keeps publishing a
        # stationary /bebop/odom, and feeding both into the supervisor
        # interleaves two airframes: the altitude governor would see the
        # simulated 1.8 m and the real 0 m on alternate samples.
        bench_publisher = BenchTelemetryPublisher(
            telemetry_node, params.network.namespace, clock=telemetry_node.get_clock()
        )
        simulator.set_state_listener(bench_publisher.publish)
        logger.info(
            "[NO-FLY BENCHTOP] Simulated airframe published on %s and %s; %s is not read.",
            bench_publisher.odometry_topic,
            bench_publisher.flying_state_topic,
            params.network.odometry_topic,
        )
    # The driver publishes this topic reliable and transient-local at depth 1,
    # so a matching subscription receives the last reported charge on creation
    # instead of waiting up to one 500 ms state period for the next one.
    battery_topic = f"/{params.network.namespace.strip('/')}/states/battery"
    telemetry_node.create_subscription(
        BatteryState,
        battery_topic,
        battery_supervisor.battery_callback,
        QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
    )
    logger.info(
        "Battery net on %s: land in place at <= %.0f%%, warning at <= %.0f%%.",
        battery_topic,
        params.battery_land_pct,
        BATTERY_WARNING_PCT,
    )
    nectar.add_node(telemetry_node)

    if simulator is not None:
        # Seed the supervisor so ground calibration has something to work with.
        simulator.publish_initial_state()
        # Drive the simulation from the executor, at the rate the real driver
        # publishes odometry. Advancing it only when the mission thread issues a
        # command would freeze the simulated state through any phase that sends
        # none -- the post-takeoff hover, for one, which then never completes
        # its climb and leaves every altitude-dependent law on its fallback path.
        telemetry_node.create_timer(
            1.0 / params.kinematics.control_loop_hz, simulator.integrate
        )

    # Two-sided altitude hold unless the configuration explicitly declines it.
    # The descent-only governor is kept as the escape hatch rather than deleted:
    # it is the reference behaviour above the setpoint, and a field session that
    # finds the hold misbehaving needs a way back that is not a code change.
    governor_class = (
        AltitudeHoldGovernor if params.governor.hold_enabled else AltitudeAntiClimbGovernor
    )
    governor = governor_class(
        target_altitude=params.kinematics.target_altitude_m,
        config=params.governor,
    )
    logger.info(
        "Vertical axis: %s at %.2f m (descent <= %.3f, ascent <= %.3f normalized).",
        governor_class.__name__,
        params.kinematics.target_altitude_m,
        params.governor.max_descent_speed,
        params.governor.climb_authority,
    )
    failsafe = FailsafeSupervisor(
        drone_actuator=actuator,
        odom_supervisor=odom_supervisor,
        timeouts_cfg=params.timeouts,
        kinematics_cfg=params.kinematics,
        battery_supervisor=battery_supervisor,
    )
    visual_controller = VisualServoingController(
        gimbal_config=params.gimbal,
        gimbal_pid_cfg=params.gimbal_pid,
        lateral_pid_cfg=params.lateral_pid,
        vision_cfg=params.vision,
        kinematics_cfg=params.kinematics,
        calibration=calibration,
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

    # Inference off the control thread. The worker idles until a stage engages
    # it, so the countdown warmup and the Stage 4 capture keep exclusive use of
    # the camera and detector, and the Stage 5 marker search is not competing
    # with it for frames.
    period = params.vision.inference_min_period_sec
    if isinstance(period, bool) or not isinstance(period, (int, float)) or not math.isfinite(period) or period < 0:
        fallback_period = VisionConfig().inference_min_period_sec
        logger.error("Invalid vision.inference_min_period_sec (%r); using %.2f s.", period, fallback_period)
        params.vision.inference_min_period_sec = fallback_period
    perception = PerceptionWorker(ctx, min_period_sec=params.vision.inference_min_period_sec)
    ctx.perception = perception
    perception.start()

    # The aircraft's own FlyingStateChanged. Confirms the takeoff before
    # `mission.takeoff` is raised (steps.takeoff.takeoff_confirmed); the driver
    # publishes it reliable and transient-local at depth 1.
    flying_state_topic = f"/{params.network.namespace.strip('/')}/states/flying_state"

    def _on_flying_state(message: UInt8) -> None:
        ctx.flying_state = int(message.data)

    telemetry_node.create_subscription(
        UInt8,
        flying_state_topic,
        _on_flying_state,
        QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
    )

    # Native photo acknowledgements (RecordPictureV2), JSON from the driver;
    # reliable, volatile, depth 10 on the driver side.
    ctx.picture_ack = PictureAckTracker()
    telemetry_node.create_subscription(
        String,
        f"/{params.network.namespace.strip('/')}/states/picture_event",
        lambda message: ctx.picture_ack.update(message.data),
        10,
    )

    def _release_mission_resources() -> None:
        perception.stop()
        telemetry_node.destroy_node()

    all_steps = {
        1: TakeoffStep,
        2: ForwardSearchStep,
        3: VisualServoingStep,
        4: NadirInspectionStep,
        5: ClosedLoopRTLStep,
    }

    selection = parse_stage_selection(getattr(args, "stages", None))
    if selection is None:
        steps = [factory() for factory in all_steps.values()]
    else:
        steps = [all_steps[number]() for number in selection]
        logger.warning(
            "Partial run: stages %s only. The remaining stages are not executed, "
            "so any state they would establish is absent.",
            ", ".join(str(n) for n in selection),
        )

    stage_numbers = list(all_steps) if selection is None else list(selection)

    runner = MissionRunner(
        context=ctx,
        steps=steps,
        on_finalize=_release_mission_resources,
        stage_numbers=stage_numbers,
    )

    # The ground station's stage control.
    #
    # One Int32 naming the stage the operator wants next. It lands on the same
    # executor as the odometry subscription; an accepted request sets the
    # context's jump event, the running step unwinds through its own abort
    # path, and the runner continues at the requested stage. Which requests are
    # accepted is decided by the mission's progress (engine.stage_gate), with
    # altitude as an additional condition for Stage 1.
    stage_gate = StageRequestHandler(
        ctx, stage_numbers, lambda: odom_supervisor.snapshot().relative_altitude
    )

    def _on_stage_request(message: Int32) -> None:
        stage_gate(int(message.data))

    stage_topic = f"/{params.network.namespace.strip('/')}/mission/goto_stage"
    telemetry_node.create_subscription(Int32, stage_topic, _on_stage_request, 10)
    logger.info("Stage control listening on %s", stage_topic)

    # Registration is explicit rather than a constructor side effect, so the
    # runner can be constructed in a test off the main thread.
    runner.install_signal_handlers()

    try:
        succeeded = runner.run()
        if motion_tracker is not None:
            logger.info("Motion sequence summary -- %s", motion_tracker.summary())
    finally:
        # Idempotent: whichever of this and the interrupt handler arrives first
        # performs the teardown, and the other becomes a no-op. The previous
        # arrangement ran cleanup three times on every Ctrl-C.
        runner.finalize()

    sys.exit(runner.exit_code(succeeded))


if __name__ == "__main__":
    main()
