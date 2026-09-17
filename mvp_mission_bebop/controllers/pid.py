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

    def bleed_integral(self, retention: float) -> None:
        """Scale the accumulated integral toward zero.

        Exists for a controller that suppresses its own output inside a
        deadband. With the error forced to zero the integral neither grows nor
        decays -- ``ki * 0 * dt`` is nothing -- so a bank accumulated against a
        disturbance survives the disturbance and drives an overshoot the next
        time the band is left. Leaking it is the fix, and leaking it has to be
        the caller's decision rather than this class's: a guidance law that
        holds a steady offset against a steady disturbance wants exactly the
        opposite behaviour.

        Parameters
        ----------
        retention : float
            Fraction of the integral to keep, clamped to ``[0, 1]``.
        """
        self._integral *= max(0.0, min(1.0, retention))

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

        if not math.isfinite(measurement):
            # A non-finite sample is not a small error, it is an absence of
            # information, and the arithmetic below cannot represent that.
            #
            # The failure is silent and permanent, which is what makes it worth
            # a guard rather than a comment. ``0.0 * nan`` is ``nan``, so even
            # ``ki = 0.0`` does not keep NaN out of the integral; the clamp then
            # resolves it to the *upper* bound, because CPython's ``min`` returns
            # its first operand whenever the comparison is False. One corrupt
            # odometry sample therefore pins the controller at full positive
            # authority, and with ``ki = 0`` there is no integral action left to
            # unwind it -- only an explicit ``reset``, which the flight path
            # calls once at engagement. Measured on the RTL longitudinal gains,
            # a single NaN takes the output from 0.075 to the 0.10 saturation
            # limit and holds it there indefinitely.
            #
            # Returning zero demand leaves the airframe coasting on the
            # actuator's latched command rather than lurching, and leaves the
            # filter state clean so the next valid sample resumes normally.
            return 0.0

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
