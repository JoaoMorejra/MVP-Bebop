"""Control laws: guidance, servoing, profiling, and actuator shaping.

Every module here is free of ROS and of wall-clock reads, so the flight
mathematics can be exercised without hardware.
"""

from mvp_mission_bebop.controllers.altitude_hold import AltitudeHoldGovernor
from mvp_mission_bebop.controllers.anti_climb import AltitudeAntiClimbGovernor
from mvp_mission_bebop.controllers.geometry import (
    CameraIntrinsics,
    GroundProjection,
    project_to_ground,
)
from mvp_mission_bebop.controllers.pid import FilteredPID, PIDGains
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.rtl_guidance import (
    GuidanceCommand,
    ReturnReference,
    RTLGuidanceController,
    RTLPhase,
)
from mvp_mission_bebop.controllers.visual_servoing import (
    ServoCommand,
    TrackingPhase,
    VisualServoingController,
)

__all__ = [
    "AltitudeAntiClimbGovernor",
    "AltitudeHoldGovernor",
    "CameraIntrinsics",
    "FilteredPID",
    "GroundProjection",
    "GuidanceCommand",
    "JerkLimitedProfile",
    "PIDGains",
    "ProfileLimits",
    "QuantizedCommandShaper",
    "RTLGuidanceController",
    "RTLPhase",
    "ReturnReference",
    "ServoCommand",
    "TrackingPhase",
    "VisualServoingController",
    "braking_velocity",
    "project_to_ground",
]
