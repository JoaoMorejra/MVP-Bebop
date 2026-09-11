"""State estimation primitives: convergence, unit calibration, target tracking.

These modules are deliberately free of ROS, the Nectar SDK, and mission types.
That keeps them exercisable without hardware and makes the control laws that
depend on them testable in isolation.
"""

from mvp_mission_bebop.estimation.calibration import SpeedCalibration, SpeedGainEstimator
from mvp_mission_bebop.estimation.convergence import (
    SettlementCriteria,
    SettlementDetector,
    SettlementReport,
)
from mvp_mission_bebop.estimation.target_tracker import (
    ConstantVelocityTracker,
    TrackEstimate,
    TrackerGains,
)

__all__ = [
    "ConstantVelocityTracker",
    "SettlementCriteria",
    "SettlementDetector",
    "SettlementReport",
    "SpeedCalibration",
    "SpeedGainEstimator",
    "TrackEstimate",
    "TrackerGains",
]
