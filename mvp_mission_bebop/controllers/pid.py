"""PD control with explicit timing and derivative filtering.

The Nectar SDK ships ``nectar.control.PIDController`` and this package uses it
in the visual servoing path, where it is already proven in flight. It is not
usable for the guidance laws in this module, for two reasons that are properties
of its implementation rather than preferences:

*It reads the wall clock internally.* ``PIDController.update`` calls
``time.time()`` and derives ``dt`` from its own previous call
(``nectar/control/pid/pid_controller.py:81-84``). There is no way to drive it
with a measured interval, so a guidance law built on it cannot be stepped
deterministically in a test, and it cannot consume the true interval reported by
:class:`~mvp_mission_bebop.engine.rate.LoopRate`. It also has no maximum-``dt``
guard: a loop that stalls on a blocking frame grab produces a large ``dt``, and
with it an integral jump and a derivative of effectively zero.

*It differentiates raw error.* The derivative term is ``(e_k - e_k-1) / dt`` with
no filtering, so measurement noise is amplified by ``1/dt`` -- at 15 Hz, a
one-centimetre odometry jitter becomes 0.15 m/s of phantom derivative. It also
differentiates the error rather than the measurement, which produces a
derivative kick on every setpoint change.

This controller keeps the SDK's semantics -- same gain meanings, clamped
anti-windup, output deadband -- and adds an explicit ``dt``, a first-order
derivative filter, and derivative-on-measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class PIDGains:
    """Gains and limits for :class:`FilteredPID`."""

    kp: float = 0.0
    ki: float = 0.0
    kd: float = 0.0
    output_limits: Tuple[float, float] = (-1.0, 1.0)
    integral_limits: Tuple[float, float] = (-1.0, 1.0)
    #: Symmetric deadband on the output, matching the SDK's ``output_deadband``.
    output_deadband: float = 0.0
    #: Errors smaller than this are treated as zero. The SDK has no equivalent;
    #: without it a controller chases sensor noise around the setpoint forever.
    error_deadband: float = 0.0
    #: Cutoff of the first-order derivative filter, in hertz. Zero disables it.
    derivative_cutoff_hz: float = 5.0

    def __post_init__(self) -> None:
        low, high = self.output_limits
        if low >= high:
            raise ValueError(f"output_limits must be ordered (low, high), got {self.output_limits!r}")
        low_i, high_i = self.integral_limits
        if low_i >= high_i:
            raise ValueError(
                f"integral_limits must be ordered (low, high), got {self.integral_limits!r}"
            )
        if self.derivative_cutoff_hz < 0.0:
            raise ValueError(
                f"derivative_cutoff_hz must be non-negative, got {self.derivative_cutoff_hz!r}"
            )


class FilteredPID:
    """PID with explicit ``dt``, filtered derivative-on-measurement."""

    __slots__ = ("_gains", "_setpoint", "_integral", "_last_measurement", "_derivative", "_primed")

    def __init__(self, gains: PIDGains, *, setpoint: float = 0.0) -> None:
        self._gains = gains
        self._setpoint = setpoint
        self._integral: float = 0.0
        self._last_measurement: float = 0.0
        self._derivative: float = 0.0
        self._primed: bool = False

    @property
    def gains(self) -> PIDGains:
        """Gains in force."""
        return self._gains

    @property
    def setpoint(self) -> float:
        """Current target value."""
        return self._setpoint

    @property
    def components(self) -> dict:
        """Individual terms, for diagnostics."""
        return {"integral": self._integral, "derivative": self._derivative}

    def set_setpoint(self, setpoint: float) -> None:
        """Change the target without disturbing the derivative.

        Because the derivative is taken on the measurement rather than the
        error, a setpoint change produces no derivative impulse.
        """
        self._setpoint = setpoint

    def reset(self) -> None:
        """Clear integral, derivative, and the priming state."""
        self._integral = 0.0
        self._derivative = 0.0
        self._last_measurement = 0.0
        self._primed = False

    def update(self, measurement: float, dt: float) -> float:
        """Compute the control effort for one cycle.

        Parameters
        ----------
        measurement : float
            Current measured value.
        dt : float
            Interval since the previous update, in seconds. Supply the value
            reported by ``LoopRate.tick`` rather than a nominal period.
        """
        gains = self._gains

        if dt <= 0.0:
            return self._clamp_output(gains.kp * (self._setpoint - measurement))

        error = self._setpoint - measurement
        if abs(error) <= gains.error_deadband:
            error = 0.0

        proportional = gains.kp * error

        self._integral += gains.ki * error * dt
        self._integral = max(
            gains.integral_limits[0], min(gains.integral_limits[1], self._integral)
        )

        if not self._primed:
            # No history yet: a derivative from a single sample is meaningless.
            self._primed = True
            self._last_measurement = measurement
            raw_derivative = 0.0
        else:
            # Derivative on measurement, negated to preserve the usual sign.
            raw_derivative = -(measurement - self._last_measurement) / dt
            self._last_measurement = measurement

        if gains.derivative_cutoff_hz > 0.0:
            # First-order low-pass. alpha = dt / (tau + dt), tau = 1 / (2*pi*fc).
            tau = 1.0 / (2.0 * math.pi * gains.derivative_cutoff_hz)
            alpha = dt / (tau + dt)
            self._derivative += alpha * (raw_derivative - self._derivative)
        else:
            self._derivative = raw_derivative

        output = proportional + self._integral + gains.kd * self._derivative
        return self._clamp_output(output)

    def _clamp_output(self, output: float) -> float:
        gains = self._gains
        clamped = max(gains.output_limits[0], min(gains.output_limits[1], output))
        if abs(clamped) < gains.output_deadband:
            return 0.0
        return clamped
