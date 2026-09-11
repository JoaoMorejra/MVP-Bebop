"""Odometry supervisor, ground calibration, and body-frame vector transformations."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import List, Optional, Tuple

import numpy as np
from nav_msgs.msg import Odometry

from mvp_mission_bebop.parameters import FlightKinematicsConfig, TimeoutsConfig

logger = logging.getLogger("OdometrySupervisor")


class OdometrySupervisor:
    """Manages odometry telemetry, ground origin calibration, and body-frame projections."""

    def __init__(
        self,
        kinematics_cfg: FlightKinematicsConfig,
        timeouts_cfg: TimeoutsConfig,
    ) -> None:
        self.kinematics_cfg = kinematics_cfg
        self.timeouts_cfg = timeouts_cfg
        self.altitude_ceiling: float = (
            kinematics_cfg.target_altitude_m + kinematics_cfg.altitude_ceiling_margin_m
        )

        self._lock = threading.Lock()
        self.ground_reference_altitude: Optional[float] = None
        self.current_raw_altitude: float = 0.0
        self.current_x: float = 0.0
        self.current_y: float = 0.0
        self.current_vx: float = 0.0
        self.current_vy: float = 0.0
        self.current_vz: float = 0.0
        self.current_yaw: float = 0.0
        self.takeoff_x: Optional[float] = None
        self.takeoff_y: Optional[float] = None
        self.last_odometry_timestamp: float = 0.0

        self._sample_buffer_z: List[float] = []
        self._sample_buffer_x: List[float] = []
        self._sample_buffer_y: List[float] = []

    def odometry_callback(self, msg: Odometry) -> None:
        """Process nav_msgs/Odometry updates."""
        with self._lock:
            self.current_raw_altitude = float(msg.pose.pose.position.z)
            self.current_x = float(msg.pose.pose.position.x)
            self.current_y = float(msg.pose.pose.position.y)

            # Linear velocities from driver optical-flow / IMU fusion
            self.current_vx = float(msg.twist.twist.linear.x)
            self.current_vy = float(msg.twist.twist.linear.y)
            self.current_vz = float(msg.twist.twist.linear.z)

            # Extract yaw angle from orientation quaternion
            q = msg.pose.pose.orientation
            siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
            cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
            self.current_yaw = math.atan2(siny_cosp, cosy_cosp)

            self.last_odometry_timestamp = time.time()

            if self.ground_reference_altitude is None:
                self._sample_buffer_z.append(self.current_raw_altitude)
                self._sample_buffer_x.append(self.current_x)
                self._sample_buffer_y.append(self.current_y)
                if len(self._sample_buffer_z) > 50:
                    self._sample_buffer_z.pop(0)
                    self._sample_buffer_x.pop(0)
                    self._sample_buffer_y.pop(0)

    def calibrate_ground_reference(self) -> bool:
        """Calculate and freeze static ground-level coordinates (x0, y0, z0)."""
        with self._lock:
            if not self._sample_buffer_z:
                self.ground_reference_altitude = self.current_raw_altitude
                self.takeoff_x = self.current_x
                self.takeoff_y = self.current_y
            else:
                self.ground_reference_altitude = float(np.mean(self._sample_buffer_z))
                self.takeoff_x = float(np.mean(self._sample_buffer_x))
                self.takeoff_y = float(np.mean(self._sample_buffer_y))

        logger.info(
            "Ground launch reference calibrated: x0=%.3f m, y0=%.3f m, z0=%.3f m. Altitude ceiling: %.3f m.",
            self.takeoff_x,
            self.takeoff_y,
            self.ground_reference_altitude,
            self.altitude_ceiling,
        )
        return True

    def freeze_hover_takeoff_origin(self) -> bool:
        """Freeze stabilized airborne coordinates as the definitive horizontal return target.

        Eliminates ground-effect lift-off transients from corrupting horizontal origin.
        """
        with self._lock:
            self.takeoff_x = self.current_x
            self.takeoff_y = self.current_y

        logger.info(
            "Airborne hover takeoff origin stabilized: x0=%.3f m, y0=%.3f m (alt_rel=%.3f m).",
            self.takeoff_x,
            self.takeoff_y,
            self.relative_altitude,
        )
        return True

    def get_current_horizontal_speed(self) -> float:
        """Compute instantaneous horizontal ground speed magnitude in m/s."""
        with self._lock:
            return math.hypot(self.current_vx, self.current_vy)

    def is_hover_settled(self, max_speed_mps: float = 0.04) -> bool:
        """Verify drone is effectively motionless in hover (speed <= threshold)."""
        return self.get_current_horizontal_speed() <= max_speed_mps

    @property
    def relative_altitude(self) -> float:
        """Compute current altitude relative to calibrated ground level."""
        with self._lock:
            if self.ground_reference_altitude is None:
                return self.current_raw_altitude
            return self.current_raw_altitude - self.ground_reference_altitude

    def get_body_frame_launch_error(self) -> Tuple[float, float, float]:
        """Calculate error vector pointing to origin rotated into Body Frame (FLU).

        Returns
        -------
        Tuple[float, float, float]
            ex_body (forward/backward), ey_body (left/right), scalar distance (m).
        """
        with self._lock:
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
        """Verify if current relative altitude exceeds the safety ceiling."""
        return self.relative_altitude > self.altitude_ceiling

    def is_telemetry_healthy(self, timeout_sec: Optional[float] = None) -> bool:
        """Verify odometry update rate within specified timeout window."""
        timeout = timeout_sec or self.timeouts_cfg.odometry_heartbeat_timeout_sec
        with self._lock:
            if self.last_odometry_timestamp == 0.0:
                return True
            return (time.time() - self.last_odometry_timestamp) <= timeout
