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
from typing import TYPE_CHECKING, Final, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - the estimation layer stays config-free
    from mvp_mission_bebop.parameters import FlightKinematicsConfig

logger = logging.getLogger("SpeedCalibration")

#: Sanity band for an identified gain. Outside this the estimate is rejected as
#: degenerate rather than propagated into a control law.
_MIN_PLAUSIBLE_GAIN: Final[float] = 0.05
_MAX_PLAUSIBLE_GAIN: Final[float] = 20.0

#: Commands below this magnitude carry too little signal to identify a gain.
_MIN_IDENTIFICATION_COMMAND: Final[float] = 0.03


@dataclass(frozen=True)
class SpeedCalibration:
    """Map between normalized actuator command and metres per second.

    Two distinct questions are answered here and it is worth keeping them apart.

    *What speed does this command ask for?* -- :meth:`to_mps` and its inverse.
    A pure gain, because that is what a guidance law needs: it converts a demand
    it computed in m/s into the domain the driver speaks, and back. Both are
    exactly as they have always been.

    *How far did the drone actually travel?* -- :meth:`displacement`. That is a
    different question and it has a different answer, which is the whole of what
    makes dead reckoning more than multiplication. A command of 0.5 held for ten
    seconds does not displace the airframe by ten times the speed 0.5 maps to:

    * Commands under :attr:`command_deadzone` never reach the airframe at all.
      The driver quantizes to ``int8(v * 100)``, so 0.008 is transmitted as an
      exact zero -- and the sigma-delta shaper in
      :mod:`~mvp_mission_bebop.controllers.quantization` deliberately emits long
      runs of sub-threshold demand, so this is systematic, not incidental.
    * The airframe spends the leading edge of each command accelerating into it
      and part of the next one shedding it, so the mean speed over the interval
      is below the commanded one. :attr:`translation_efficiency` is that ratio.
    * Roll and pitch authority are not the same number, so the cross-track axis
      gets its own gain when one has been measured.

    Notes
    -----
    Every default here reproduces the naive 1:1 conversion exactly, except the
    dead zone, which is a documented property of the driver rather than a tuning
    choice. They are placeholders and not measurements: fly a known command for a
    known duration, divide the measured displacement by ``speed * duration``, and
    set ``translation_efficiency`` to what comes out.
    """

    normalized_to_mps: float = 1.0
    #: Cross-track gain. ``None`` inherits the longitudinal one.
    lateral_to_mps: Optional[float] = None
    #: Vertical gain. ``None`` inherits the longitudinal one.
    vertical_to_mps: Optional[float] = None
    #: Normalized magnitude below which the driver transmits an exact zero.
    command_deadzone: float = 0.0
    #: Fraction of a held interval spent at the commanded speed.
    translation_efficiency: float = 1.0

    def __post_init__(self) -> None:
        if self.normalized_to_mps <= 0.0:
            raise ValueError(
                f"normalized_to_mps must be positive, got {self.normalized_to_mps!r}"
            )
        for name in ("lateral_to_mps", "vertical_to_mps"):
            value = getattr(self, name)
            if value is not None and value <= 0.0:
                raise ValueError(f"{name} must be positive when set, got {value!r}")
        if self.command_deadzone < 0.0:
            raise ValueError(
                f"command_deadzone must be non-negative, got {self.command_deadzone!r}"
            )
        if not 0.0 < self.translation_efficiency <= 2.0:
            raise ValueError(
                f"translation_efficiency must lie in (0, 2], got "
                f"{self.translation_efficiency!r}"
            )

    # ------------------------------------------------------------ construction

    @classmethod
    def from_kinematics(cls, kinematics_cfg: "FlightKinematicsConfig") -> "SpeedCalibration":
        """Build from the mission's kinematics section.

        The single place the configuration layer and the unit conversion meet,
        so a new calibration field cannot be added in one and forgotten in the
        other.
        """
        return cls(
            normalized_to_mps=kinematics_cfg.normalized_to_mps,
            lateral_to_mps=kinematics_cfg.lateral_normalized_to_mps,
            vertical_to_mps=kinematics_cfg.vertical_normalized_to_mps,
            command_deadzone=kinematics_cfg.command_deadzone_normalized,
            translation_efficiency=kinematics_cfg.translation_efficiency,
        )

    # -------------------------------------------------------------- properties

    @property
    def is_identity(self) -> bool:
        """True while the longitudinal gain is the uncalibrated 1:1 placeholder."""
        return abs(self.normalized_to_mps - 1.0) < 1e-9

    @property
    def lateral_gain(self) -> float:
        """Cross-track gain in force, inheriting the longitudinal one if unset."""
        return self.normalized_to_mps if self.lateral_to_mps is None else self.lateral_to_mps

    @property
    def vertical_gain(self) -> float:
        """Vertical gain in force, inheriting the longitudinal one if unset."""
        return self.normalized_to_mps if self.vertical_to_mps is None else self.vertical_to_mps

    @property
    def is_displacement_naive(self) -> bool:
        """True while :meth:`displacement` is a plain speed-times-time product."""
        return (
            self.command_deadzone <= 0.0
            and abs(self.translation_efficiency - 1.0) < 1e-9
            and self.lateral_to_mps is None
        )

    # ------------------------------------------------------------- conversions

    def to_mps(self, normalized: float) -> float:
        """Convert a normalized command into metres per second."""
        return normalized * self.normalized_to_mps

    def to_normalized(self, mps: float) -> float:
        """Convert a physical velocity into a normalized command."""
        return mps / self.normalized_to_mps

    def effective_mps(self, normalized: float, *, axis: str = "forward") -> float:
        """Speed the airframe actually reaches under a held command.

        Unlike :meth:`to_mps` this is an estimate of behaviour rather than a
        restatement of intent: a command inside the driver's dead zone produces
        no motion whatsoever, so it maps to exactly zero.

        Parameters
        ----------
        normalized : float
            Commanded component, normalized to [-1, 1].
        axis : str
            ``"forward"``, ``"lateral"`` or ``"vertical"``. Selects the gain.
        """
        if abs(normalized) < self.command_deadzone:
            return 0.0
        gain = {
            "forward": self.normalized_to_mps,
            "lateral": self.lateral_gain,
            "vertical": self.vertical_gain,
        }[axis]
        return normalized * gain

    def displacement(
        self, vx: float, vy: float, duration_sec: float
    ) -> Tuple[float, float]:
        """Body-frame displacement produced by holding a command, in metres.

        Returns
        -------
        Tuple[float, float]
            ``(forward_m, left_m)`` in the body FLU frame, so a leftward
            translation is positive. Zero duration, or a command inside the
            driver's dead zone, contributes exactly nothing.
        """
        if duration_sec <= 0.0:
            return 0.0, 0.0
        effective = duration_sec * self.translation_efficiency
        return (
            self.effective_mps(vx, axis="forward") * effective,
            self.effective_mps(vy, axis="lateral") * effective,
        )

    def warn_if_uncalibrated(self, context: str) -> None:
        """Emit a one-line reminder that absolute units are not yet meaningful."""
        if self.is_identity:
            logger.warning(
                "%s is running with an uncalibrated speed gain (normalized_to_mps=1.0). "
                "Profile shapes are correct but absolute m/s values are nominal. "
                "Set kinematics.normalized_to_mps once measured in flight.",
                context,
            )
        if abs(self.translation_efficiency - 1.0) < 1e-9:
            logger.warning(
                "%s is dead reckoning with translation_efficiency=1.0, which assumes the "
                "airframe reaches its commanded speed instantly and sheds it instantly. "
                "Fly a known command for a known duration and set "
                "kinematics.translation_efficiency to measured_displacement / (speed * duration).",
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
