#!/usr/bin/env python3
"""
Bebop 2 autonomous square flight mission.

Mirrors the ``run_velocity`` pattern from ``nectar/examples/control/basic.py``
adapted exclusively for the Parrot Bebop 2 platform.

The entire flight interface is orchestrated through the Nectar SDK
(Black Bee Drones). Direct ROS 2 message publishing (Twist, Empty, …) is
strictly forbidden — all commands go through ``BebopDrone`` methods.

Prerequisites:
    1. Connect to the Bebop 2 Wi-Fi network (Bebop2-XXXXXX).
    2. Start the ROS 2 driver *before* running this script:
           make driver-bebop          # from the nectar-sdk root
       or manually:
           ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=192.168.42.1

Usage:
    # Default square (velocity=0.3, side=1.0 m, height=1.0 m):
    python3 mission.py

    # Custom parameters:
    python3 mission.py --velocity 0.4 --side 1.5 --height 1.0

    # With live camera capture (saves a frame before landing):
    python3 mission.py --camera

    # As a ROS 2 executable (after colcon build):
    ros2 run mvp_mission_bebop mission
"""

import argparse
import logging
import os
import time
from typing import Optional

import cv2
import numpy as np

import nectar
from nectar.control import BebopConfig, DroneFactory
from nectar.vision import ImageHandler, ROSConfig, QoSReliability

log = logging.getLogger("bebop_mission")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Default Bebop parameters
BEBOP_IP = "192.168.42.1"
BEBOP_NAMESPACE = "bebop"
CAMERA_TOPIC = "/bebop/camera/image_raw"

# Flight defaults
DEFAULT_HEIGHT = 1.0      # metres (ignored by Bebop firmware, kept for API)
DEFAULT_VELOCITY = 0.3    # normalised [-1.0, 1.0]
DEFAULT_SIDE = 1.0        # metres (side length of the square)
DEFAULT_STABILIZE = 5.0   # seconds to stabilise after takeoff / before land
DEFAULT_CAMERA_TILT = -45.0  # degrees (negative = look down)


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------
def _save_frame(frame: Optional[np.ndarray], output_dir: str = ".") -> Optional[str]:
    """Save a BGR frame to disk with a timestamped filename.

    Returns the path to the saved file, or ``None`` on failure.
    """
    if frame is None:
        log.warning("No frame to save")
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"bebop_capture_{timestamp}.jpg")
    cv2.imwrite(path, frame)
    log.info("Frame saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# Flight patterns
# ---------------------------------------------------------------------------
def run_square(drone, args: argparse.Namespace) -> None:
    """Fly a velocity-based square pattern.

    Replicates ``run_velocity`` from ``nectar/examples/control/basic.py``,
    stripped to Bebop-only semantics.
    """
    # Takeoff (altitude is ignored by the Bebop firmware — fixed preset)
    if not drone.takeoff(altitude=args.height):
        log.error("Takeoff failed")
        return
    log.info("Airborne — stabilising for %.1fs", DEFAULT_STABILIZE)
    drone.delay(DEFAULT_STABILIZE)

    # Compute duration per leg: side_length / velocity
    v = args.velocity
    t = args.side / v if v > 0 else 2.0
    log.info("Velocity square: v=%.2f  side=%.1fm  leg_time=%.1fs", v, args.side, t)

    # Four legs of the square (body-frame, normalised velocity)
    for label, vx, vy in [
        ("Forward", v, 0.0),
        ("Left", 0.0, v),
        ("Backward", -v, 0.0),
        ("Right", 0.0, -v),
    ]:
        log.info(label)
        drone.move_velocity(vx=vx, vy=vy, duration=t)
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=5.0)

    log.info("Square complete — returning to hover")
    drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=5.0)

    drone.land()
    log.info("Landed")


def run_square_with_camera(drone, args: argparse.Namespace) -> None:
    """Fly the square pattern with a mid-flight camera capture.

    Uses the Nectar ``ImageHandler`` to subscribe to the Bebop's
    ``/bebop/camera/image_raw`` topic and grab a live frame at the
    midpoint of the mission (after leg 2).
    """
    # Build camera handler (its own internal node joins the SDK executor)
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

    # --- Flight sequence (identical to run_square, with capture inserted) ---
    if not drone.takeoff(altitude=args.height):
        log.error("Takeoff failed")
        handler.cleanup()
        return
    log.info("Airborne — stabilising for %.1fs", DEFAULT_STABILIZE)
    drone.delay(DEFAULT_STABILIZE)

    v = args.velocity
    t = args.side / v if v > 0 else 2.0
    log.info("Velocity square: v=%.2f  side=%.1fm  leg_time=%.1fs", v, args.side, t)

    for label, vx, vy in [
            ("Forward", v, 0.0),
            ("Left", 0.0, v),
            ("Backward", -v, 0.0),
            ("Right", 0.0, -v),
    ]:
        log.info(label)
        drone.move_velocity(vx=vx, vy=vy, duration=t)
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0, duration=5.0)
        
        # Capture at the midpoint (after leg 2)
        log.info("Hovering for camera capture…")
        drone.camera_control(tilt=DEFAULT_CAMERA_TILT, pan=0.0)
        drone.delay(1.0)

        frame = handler.take_photo(timeout_sec=2.0)
        _save_frame(frame) 

        # Restore camera to neutral
        drone.camera_control(tilt=0.0, pan=0.0)
        drone.delay(0.5)

    log.info("Square complete — returning to hover")
    drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
    drone.delay(5.0)

    drone.land()
    log.info("Landed")

    handler.cleanup()
    log.info("Camera released")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="Bebop 2 autonomous square flight mission"
    )
    parser.add_argument(
        "--height",
        type=float,
        default=DEFAULT_HEIGHT,
        help="Takeoff altitude in metres (ignored by Bebop firmware, "
        "kept for API compatibility). Default: %(default)s",
    )
    parser.add_argument(
        "--velocity",
        type=float,
        default=DEFAULT_VELOCITY,
        help="Normalised velocity [-1.0, 1.0] for each leg. Default: %(default)s",
    )
    parser.add_argument(
        "--side",
        type=float,
        default=DEFAULT_SIDE,
        help="Side length of the square in metres. Default: %(default)s",
    )
    parser.add_argument(
        "--ip",
        default=BEBOP_IP,
        help="Bebop Wi-Fi IP address. Default: %(default)s",
    )
    parser.add_argument(
        "--camera",
        action="store_true",
        help="Enable mid-flight camera capture via /bebop/camera/image_raw.",
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
                "  Start it with:  make driver-bebop\n"
                "  or:  ros2 launch ros2_bebop_driver bebop_node_launch.xml ip:=%s",
                args.ip,
            )
            return

        if args.camera:
            run_square_with_camera(drone, args)
        else:
            run_square(drone, args)

    except KeyboardInterrupt:
        log.info("Interrupted — emergency landing")
        drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        drone.land()
    finally:
        drone.cleanup()
        nectar.shutdown()


if __name__ == "__main__":
    main()
