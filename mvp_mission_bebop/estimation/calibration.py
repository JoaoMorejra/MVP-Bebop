"""Conversion between normalized actuator commands and physical velocity.

The Bebop presents two incompatible unit systems and nothing in the SDK
reconciles them. ``BebopDrone.move_velocity`` clamps each component to [-1, 1]
and publishes it as a Twist (``nectar/control/bebop/drone.py:216-231``) -- a
throttle fraction, not a speed. ``/bebop/odom`` reports position and velocity in
metres. The previous RTL compared a distance in metres against a normalized
``max_speed`` and logged every command as "m/s", which is how a braking profile
ends up with no physical meaning at all.

Guidance in this package works in metres per second and converts only at the
actuator boundary, through this module.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Final, List, Optional, Tuple

logger = logging.getLogger("SpeedCalibration")

#: Sanity band for an identified gain. Outside this the estimate is rejected as
#: degenerate rather than propagated into a control law.
_MIN_PLAUSIBLE_GAIN: Final[float] = 0.05
_MAX_PLAUSIBLE_GAIN: Final[float] = 20.0

#: Commands below this magnitude carry too little signal to identify a gain.
_MIN_IDENTIFICATION_COMMAND: Final[float] = 0.03


@dataclass(frozen=True)
class SpeedCalibration:
    """Affine map between normalized command and metres per second.

    Parameters
    ----------
    normalized_to_mps : float
        Speed, in m/s, produced by a unit normalized command.

    Notes
    -----
    The default configured value is 1.0, which makes the two domains
    numerically identical and reproduces the historical behaviour exactly. That
    is a deliberate placeholder, not a measurement: until it is calibrated, the
    guidance profiles are correctly *shaped* but their absolute accelerations
    are not in real m/s^2.
    """

    normalized_to_mps: float

    def __post_init__(self) -> None:
        if self.normalized_to_mps <= 0.0:
            raise ValueError(
                f"normalized_to_mps must be positive, got {self.normalized_to_mps!r}"
            )

    @property
    def is_identity(self) -> bool:
        """True while the calibration is the uncalibrated 1:1 placeholder."""
        return abs(self.normalized_to_mps - 1.0) < 1e-9

    def to_mps(self, normalized: float) -> float:
        """Convert a normalized command into metres per second."""
        return normalized * self.normalized_to_mps

    def to_normalized(self, mps: float) -> float:
        """Convert a physical velocity into a normalized command."""
        return mps / self.normalized_to_mps

    def warn_if_uncalibrated(self, context: str) -> None:
        """Emit a one-line reminder that absolute units are not yet meaningful."""
        if self.is_identity:
            logger.warning(
                "%s is running with an uncalibrated speed gain (normalized_to_mps=1.0). "
                "Profile shapes are correct but absolute m/s values are nominal. "
                "Set kinematics.normalized_to_mps once measured in flight.",
                context,
            )


class SpeedGainEstimator:
    """Online least-squares identification of the command-to-speed gain.

    Accumulates (commanded magnitude, measured speed) pairs during steady
    cruise and reports the ratio that best explains them. The result is
    **advisory only**: it is logged so the operator can transfer it into the
    configuration, and never fed back into a live control law. An estimator that
    degenerates mid-flight -- because the drone was blocked, or odometry
    drifted -- would otherwise become a new failure mode in the guidance path.
    """

    __slots__ = ("_commands", "_speeds", "_capacity")

    def __init__(self, capacity: int = 200) -> None:
        if capacity < 2:
            raise ValueError(f"capacity must be at least 2, got {capacity!r}")
        self._capacity = capacity
        self._commands: List[float] = []
        self._speeds: List[float] = []

    @property
    def sample_count(self) -> int:
        """Usable observations accumulated so far."""
        return len(self._commands)

    def reset(self) -> None:
        """Discard accumulated observations."""
        self._commands.clear()
        self._speeds.clear()

    def observe(self, commanded_magnitude: float, measured_speed_mps: float) -> None:
        """Record one steady-state pair, if it carries enough signal."""
        if abs(commanded_magnitude) < _MIN_IDENTIFICATION_COMMAND:
            return
        if measured_speed_mps < 0.0:
            return

        self._commands.append(abs(commanded_magnitude))
        self._speeds.append(measured_speed_mps)
        if len(self._commands) > self._capacity:
            del self._commands[0]
            del self._speeds[0]

    def estimate(self) -> Optional[Tuple[float, float]]:
        """Best-fit gain through the origin, with its dispersion.

        Returns
        -------
        Optional[Tuple[float, float]]
            ``(gain, sigma)`` in m/s per unit command, or ``None`` when there is
            not enough data or the fit falls outside the plausible band.
        """
        if len(self._commands) < 10:
            return None

        # Least squares through the origin: gain = sum(u*v) / sum(u^2).
        numerator = sum(u * v for u, v in zip(self._commands, self._speeds))
        denominator = sum(u * u for u in self._commands)
        if denominator <= 0.0:
            return None

        gain = numerator / denominator
        if not (_MIN_PLAUSIBLE_GAIN <= gain <= _MAX_PLAUSIBLE_GAIN):
            return None

        ratios = [v / u for u, v in zip(self._commands, self._speeds) if u > 0.0]
        sigma = statistics.pstdev(ratios) if len(ratios) > 1 else 0.0
        return gain, sigma

    def report(self) -> str:
        """Human-readable summary for the mission log."""
        result = self.estimate()
        if result is None:
            return f"speed gain not identifiable ({self.sample_count} samples)"
        gain, sigma = result
        return (
            f"identified speed gain {gain:.3f} m/s per unit command "
            f"(sigma {sigma:.3f}, {self.sample_count} samples). "
            f"Set kinematics.normalized_to_mps to this value to calibrate."
        )
