"""Centralized mission configuration and tunable parameters.

All operational parameters that govern flight behavior, vision processing,
PID controllers, network topics, and safety limits are declared here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple 


@dataclass
class NetworkConfig:
    """Network connection endpoints and ROS 2 topic names."""

    drone_ip: str = "192.168.42.1"
    namespace: str = "bebop"
    camera_raw_topic: str = "/bebop/camera/image_raw"
    detection_stream_topic: str = "/bebop/camera/detections"
    odometry_topic: str = "/bebop/odom"


@dataclass
class GimbalConstraintsConfig:
    """Mechanical and operational angular limits for the camera gimbal (degrees)."""

    search_tilt_deg: float = -20.0
    nadir_tilt_deg: float = -80.0
    max_slew_limit_deg: float = 18.0


@dataclass
class VisionConfig:
    """Computer vision model, inference parameters, and optical tolerances."""

    model_path: str = "yolov8n.pt"
    confidence_threshold: float = 0.50
    target_classes: List[str] = field(default_factory=lambda: ["motorcycle", "bicycle"])
    confirmation_frames: int = 3 
    optical_center_tolerance_px: float = 30.0
    approach_centering_tolerance_px: float = 25.0


@dataclass
class GimbalPIDConfig:
    """High-bandwidth PID gains for vertical optical centering."""

    kp: float = 18.0
    ki: float = 0.0
    kd: float = 1.2
    output_limits: Tuple[float, float] = (-18.0, 18.0)


@dataclass
class LateralPIDConfig:
    """PID gains for horizontal centering via body-frame lateral velocity."""

    kp: float = 0.12
    ki: float = 0.0
    kd: float = 0.01
    output_limits: Tuple[float, float] = (-0.04, 0.04)
    deadband: float = 0.005


@dataclass
class AltitudeGovernorConfig:
    """Anti-climb altitude governor parameters preventing ultrasonic climb."""

    deadband_m: float = 0.03
    kp: float = 0.80
    kd: float = 0.03
    max_descent_speed: float = 0.08


@dataclass
class FlightKinematicsConfig:
    """Translational velocity caps and geometric safety envelopes."""

    target_altitude_m: float = 1.00
    altitude_ceiling_margin_m: float = 0.25
    forward_cruise_velocity: float = 0.05
    max_approach_forward_speed: float = 0.04
    takeoff_stabilize_duration_sec: float = 4.0
    hover_duration_sec: float = 7.0
    countdown_sec: float = 0.0


@dataclass
class ReturnToLaunchConfig:
    """Closed-loop odometry Return-to-Launch navigation parameters."""

    max_speed: float = 0.05
    kp: float = 0.08
    arrival_radius_m: float = 0.12
    timeout_sec: float = 35.0
    final_hover_delay_sec: float = 2.0


@dataclass
class TimeoutsConfig:
    """Execution timeouts and sensor watchdog windows."""

    search_timeout_sec: float = 30.0
    tracking_timeout_sec: float = 45.0
    target_recovery_timeout_sec: float = 4.0
    odometry_heartbeat_timeout_sec: float = 3.0
    video_stream_timeout_sec: float = 8.0


@dataclass
class MissionParameters:
    """Root container consolidating all configurable mission subsystems."""

    network: NetworkConfig = field(default_factory=NetworkConfig)
    gimbal: GimbalConstraintsConfig = field(default_factory=GimbalConstraintsConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    gimbal_pid: GimbalPIDConfig = field(default_factory=GimbalPIDConfig)
    lateral_pid: LateralPIDConfig = field(default_factory=LateralPIDConfig)
    governor: AltitudeGovernorConfig = field(default_factory=AltitudeGovernorConfig)
    kinematics: FlightKinematicsConfig = field(default_factory=FlightKinematicsConfig)
    rtl: ReturnToLaunchConfig = field(default_factory=ReturnToLaunchConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    no_fly: bool = False
    output_dir: str = "."

    def to_dict(self) -> dict:
        """Convert parameter dataclasses to standard dictionary."""
        import dataclasses
        return dataclasses.asdict(self)

    def update_from_dict(self, data: dict) -> None:
        """Update existing instance hierarchically with values from a dictionary."""
        def _apply(target, d):
            for k, v in d.items():
                if hasattr(target, k):
                    curr = getattr(target, k)
                    if isinstance(v, dict) and hasattr(curr, "__dataclass_fields__"):
                        _apply(curr, v)
                    else:
                        setattr(target, k, v)
        _apply(self, data)

    def save_to_file(self, file_path: str) -> None:
        """Save parameters as JSON to disk."""
        import json
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_file(cls, file_path: str) -> MissionParameters:
        """Load parameters from JSON file on disk, falling back to defaults if missing/invalid."""
        import json
        import os
        params = cls()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                params.update_from_dict(data)
            except Exception as e:
                import logging
                logging.getLogger("MissionParameters").warning("Failed to load %s: %s", file_path, e)
        return params
