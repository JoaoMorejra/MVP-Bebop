"""Centralized mission configuration and tunable parameters.

All operational parameters that govern flight behavior, vision processing,
PID controllers, network topics, and safety limits are declared here.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("MissionParameters")


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
    #: Maximum gimbal angular rate, in degrees per second. Previously declared
    #: and never read; it now rate-limits every tilt command so the pitch axis
    #: cannot step discontinuously between control cycles.
    max_slew_limit_deg: float = 18.0
    #: How aggressively the gimbal chases the target's measured bearing. 1.0
    #: points the optical axis straight at the target each cycle; lower values
    #: lag deliberately, trading tracking bandwidth for steadiness.
    tracking_gain: float = 1.0


@dataclass
class VisionConfig:
    """Computer vision model, inference parameters, and optical tolerances."""

    model_path: str = "yolov8n.pt"
    confidence_threshold: float = 0.50
    target_classes: List[str] = field(default_factory=lambda: ["motorcycle", "bicycle"])
    confirmation_frames: int = 3
    #: Frames of sustained loss required before confirmation is withdrawn.
    #: Separate from ``confirmation_frames`` so acquisition and loss can have
    #: independent hysteresis instead of one counter that zeroes on any miss.
    release_frames: int = 2
    optical_center_tolerance_px: float = 35.0
    approach_centering_tolerance_px: float = 30.0
    #: Vertical centering tolerance as a multiple of the horizontal one.
    centering_vertical_ratio: float = 1.5
    #: Lateral safety corridor half-width during approach, as a multiple of the
    #: horizontal tolerance. Forward motion freezes outside it.
    approach_corridor_ratio: float = 1.6
    #: Per-axis deadband at nadir, as a multiple of the horizontal tolerance.
    nadir_deadband_ratio: float = 0.50
    #: Radial alignment threshold at nadir, as a multiple of the tolerance.
    nadir_alignment_ratio: float = 1.5
    #: Consecutive out-of-corridor frames before reverting to centering.
    decentered_frames_to_revert: int = 10
    #: Consecutive out-of-window frames before leaving the nadir phase.
    nadir_exit_frames: int = 8
    #: Consecutive lost frames tolerated before entering recovery.
    lost_frames_tolerance: int = 3
    #: Camera field of view, used by the IBVS pinhole geometry to convert
    #: pixel error into a bearing. Bebop 2 front camera, digitally stabilized.
    horizontal_fov_deg: float = 80.0
    vertical_fov_deg: float = 50.0
    #: Use the geometric IBVS law. Setting this false reverts the approach to
    #: the previous open-loop gimbal ramp without a code change -- the escape
    #: hatch for a field session where the altitude estimate proves unusable.
    ibvs_enabled: bool = True
    #: Relative altitude below which the ground projection is not trusted and
    #: the controller falls back to the open-loop ramp.
    min_altitude_for_ibvs_m: float = 0.35
    #: Ground range at which the approach is considered to be over the target.
    nadir_range_threshold_m: float = 0.25
    #: Per-cycle gimbal step used by the open-loop fallback, in degrees.
    legacy_gimbal_ramp_deg: float = 2.0


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

    kp: float = 0.35
    ki: float = 0.0
    kd: float = 0.03
    output_limits: Tuple[float, float] = (-0.22, 0.22)
    deadband: float = 0.005
    min_effective_velocity: float = 0.06


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
    forward_cruise_velocity: float = 0.20
    max_approach_forward_speed: float = 0.15
    takeoff_stabilize_duration_sec: float = 4.0
    hover_duration_sec: float = 7.0
    countdown_sec: float = 0.0
    #: Metres per second produced by a unit normalized velocity command.
    #:
    #: ``BebopDrone.move_velocity`` publishes a Twist normalized to [-1, 1]
    #: (nectar/control/bebop/drone.py:216-231), while /bebop/odom reports
    #: metres. Guidance laws work in m/s and convert at the actuator boundary
    #: using this constant. The default of 1.0 is *not* calibrated: it makes
    #: the two domains numerically identical, reproducing the historical
    #: behaviour. Calibrate in the field and set it here.
    normalized_to_mps: float = 1.0
    #: Translational acceleration ceiling for jerk-limited velocity profiles.
    max_accel_mps2: float = 0.30
    #: Jerk ceiling. Bounds the rate of change of acceleration, which is what
    #: actually stops the airframe from pitching sharply on stop.
    max_jerk_mps3: float = 1.20
    #: Target cadence for closed-loop mission control loops.
    control_loop_hz: float = 15.0


@dataclass
class ReturnToLaunchConfig:
    """Closed-loop odometry Return-to-Launch navigation parameters."""

    max_speed: float = 0.10
    #: Longitudinal PD trim applied on top of the feedforward braking profile.
    #: Both gains were declared and never read by the previous implementation,
    #: which had no control law at all -- only a piecewise ramp in displacement.
    kp: float = 0.15
    kd: float = 0.01
    lateral_kp: float = 0.18
    lateral_kd: float = 0.02
    max_lateral_speed: float = 0.05
    braking_distance_m: float = 0.45
    deadband_m: float = 0.03
    #: Smallest command the driver does not truncate to zero. The Bebop C++
    #: driver quantizes to int8(v * 100), so anything under 0.01 normalized
    #: becomes a no-op. Applied by duty-cycling, never as a hard floor.
    min_effective_speed: float = 0.035
    #: Driver command quantization step, in normalized units.
    quantization_step: float = 0.01
    settle_cycles: int = 3
    arrival_radius_m: float = 0.20
    timeout_sec: float = 60.0
    final_hover_delay_sec: float = 2.0
    #: Acceleration and jerk ceilings for the RTL velocity profile, in m/s.
    max_accel_mps2: float = 0.25
    max_jerk_mps3: float = 1.00
    #: Multivariate settlement window. Arrival requires every sample inside the
    #: arrival radius, low positional variance, and low mean speed, sustained
    #: across the window -- not N consecutive loop iterations.
    settle_window_sec: float = 1.20
    settle_min_samples: int = 6
    settle_max_speed_mps: float = 0.05
    settle_max_position_sigma_m: float = 0.06
    #: Terminal touchdown verification.
    touchdown_timeout_sec: float = 8.0
    touchdown_altitude_m: float = 0.15
    land_burst_count: int = 3


@dataclass
class CalibrationConfig:
    """Robust statistics governing ground reference (z0) calibration."""

    #: Minimum odometry samples required before a calibration is accepted.
    min_ground_samples: int = 12
    #: Outlier rejection threshold, in robust sigmas (MAD * 1.4826) from the
    #: median. Replaces the previous plain arithmetic mean, which let a single
    #: spurious altitude sample bias the whole ground reference.
    mad_outlier_sigma: float = 3.0
    #: Maximum tolerated dispersion of the altitude buffer. Above this the
    #: surface is not flat enough (or the sensor is not settled) to trust.
    max_ground_dispersion_m: float = 0.15
    #: Ring buffer depth for pre-takeoff samples.
    sample_buffer_size: int = 50


@dataclass
class InspectionConfig:
    """Stochastic motionlessness verification before evidence capture."""

    settle_window_sec: float = 1.00
    settle_min_samples: int = 5
    settle_max_speed_mps: float = 0.03
    settle_max_position_sigma_m: float = 0.04
    #: Give up waiting for stillness and capture anyway after this long.
    settle_timeout_sec: float = 6.0
    #: Write a forensic metadata sidecar alongside the image pair.
    write_metadata_sidecar: bool = True


@dataclass
class TimeoutsConfig:
    """Execution timeouts and sensor watchdog windows."""

    search_timeout_sec: float = 30.0
    tracking_timeout_sec: float = 60.0
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
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    inspection: InspectionConfig = field(default_factory=InspectionConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    no_fly: bool = False
    output_dir: str = "."

    def to_dict(self) -> Dict[str, Any]:
        """Convert parameter dataclasses to a plain nested dictionary.

        The Electron GCS invokes this through ``python3 -c`` to recover the
        defaults when ``mission_config.json`` is unreadable
        (``electron/main.cjs:602-604``), so the method name and the nested key
        layout are part of the external contract.
        """
        return dataclasses.asdict(self)

    def update_from_dict(self, data: Dict[str, Any]) -> None:
        """Update this instance hierarchically from a nested dictionary.

        Unknown keys are ignored, which is what lets new configuration fields be
        introduced without invalidating an on-disk config written by an older
        build, or a ``--params-json`` payload sent by an older GCS.
        """

        def _apply(target: Any, values: Dict[str, Any]) -> None:
            for key, value in values.items():
                if not hasattr(target, key):
                    continue
                current = getattr(target, key)
                if isinstance(value, dict) and dataclasses.is_dataclass(current):
                    _apply(current, value)
                else:
                    setattr(target, key, value)

        _apply(self, data)

    def save_to_file(self, file_path: str) -> None:
        """Save parameters as JSON to disk."""
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_file(cls, file_path: str) -> MissionParameters:
        """Load parameters from JSON on disk, falling back to defaults if missing or invalid."""
        params = cls()
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                params.update_from_dict(data)
            except Exception as exc:  # noqa: BLE001 - a bad config must not block flight
                logger.warning("Failed to load %s: %s. Using defaults.", file_path, exc)
        return params
