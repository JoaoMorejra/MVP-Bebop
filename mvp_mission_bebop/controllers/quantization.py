"""Actuator quantization compensation for sub-threshold velocity demands.

The Bebop's C++ driver quantizes each normalized velocity component to
``int8(v * 100)``. Any command whose magnitude is under 0.01 truncates to zero
and the airframe simply does not move. The previous code handled this with a
hard floor: every non-deadbanded demand was raised to ``min_effective_speed``
(0.035). That guarantees motion, and it also guarantees the vehicle cannot slow
down -- it arrives at the arrival radius still travelling at 3.5% throttle,
because the floor forbids anything gentler. The floor and the braking profile
were in direct contradiction, and the floor always won.

The fix is to stop treating the floor as an amplitude constraint and treat it as
what it actually is: a constraint on the *instantaneous* command, not on the
*average* one. A first-order sigma-delta modulator integrates the demanded
displacement and discharges it in pulses at the floor amplitude. Mean velocity
tracks the demand all the way down to zero, while every emitted command is
either exactly zero or comfortably above the truncation threshold.

This is the same principle as PWM driving a motor below its stiction current,
applied to a quantized velocity channel.
"""

from __future__ import annotations

import logging
import math
from typing import Final

#: Guard against the emitted value landing just under a quantization boundary
#: through floating-point representation error. Far too small to shift the
#: driver's int8 bucket, large enough to survive a round-trip through binary.
_QUANTIZATION_GUARD: Final[float] = 1e-6

#: Anti-windup bound on the accumulator, expressed in pulse-displacements. The
#: residual is self-limiting in normal operation; this only guards against a
#: pathological sequence of ``dt`` values.
_MAX_RESIDUAL_PULSES: Final[float] = 10.0

#: Reference control period used to express the accumulator bound as a fixed
#: displacement. Deliberately not the caller's ``dt``: a bound scaled by the
#: current interval widens precisely when the loop is running slowly, which is
#: the case it has to contain.
_NOMINAL_PERIOD_SEC: Final[float] = 1.0 / 15.0

logger = logging.getLogger("QuantizedCommandShaper")


class QuantizedCommandShaper:
    """Renders a velocity demand onto a coarsely quantized actuator channel.

    Demands at or above the effective floor pass through, snapped to the
    actuator's quantization grid. Demands below it are accumulated and emitted
    as floor-amplitude pulses whose density reproduces the demanded mean.

    One shaper instance owns one axis; the accumulator is per-axis state.
    """

    __slots__ = ("_floor", "_step", "_residual", "_pulses", "_cycles")

    def __init__(self, min_effective: float, quantization_step: float) -> None:
        if min_effective <= 0.0:
            raise ValueError(f"min_effective must be positive, got {min_effective!r}")
        if quantization_step <= 0.0:
            raise ValueError(f"quantization_step must be positive, got {quantization_step!r}")
        if min_effective < quantization_step:
            raise ValueError(
                f"min_effective ({min_effective!r}) must be at least one quantization "
                f"step ({quantization_step!r}); otherwise every pulse truncates to zero"
            )

        # Snap the floor onto the actuator grid. A floor that is not itself
        # representable (0.035 against a 0.01 step) means every pulse is emitted
        # at a different amplitude than the one the accumulator is debited by,
        # which biases the reproduced mean upward by the rounding error.
        self._step: float = quantization_step
        snapped = max(1.0, round(min_effective / quantization_step)) * quantization_step
        if abs(snapped - min_effective) > _QUANTIZATION_GUARD:
            logger.debug(
                "Effective floor %.4f is not on the %.4f actuator grid; using %.4f.",
                min_effective,
                quantization_step,
                snapped,
            )
        self._floor: float = snapped
        self._residual: float = 0.0
        self._pulses: int = 0
        self._cycles: int = 0

    @property
    def floor(self) -> float:
        """Smallest command amplitude the actuator reproduces."""
        return self._floor

    @property
    def duty_ratio(self) -> float:
        """Fraction of cycles that emitted a pulse, for diagnostics."""
        return self._pulses / self._cycles if self._cycles else 0.0

    @property
    def residual(self) -> float:
        """Undischarged displacement currently held in the accumulator."""
        return self._residual

    def reset(self) -> None:
        """Discard accumulated residual and statistics."""
        self._residual = 0.0
        self._pulses = 0
        self._cycles = 0

    def shape(self, demand: float, dt: float) -> float:
        """Convert a velocity demand into an actuator-realizable command.

        Parameters
        ----------
        demand : float
            Desired velocity, in normalized actuator units.
        dt : float
            Duration this command will be held, in seconds.

        Returns
        -------
        float
            Either zero or a magnitude at or above the effective floor, snapped
            to the quantization grid. Over successive calls the time-average of
            the returned values tracks ``demand``.
        """
        self._cycles += 1

        if dt <= 0.0:
            return 0.0

        magnitude = abs(demand)

        # An exactly-zero demand means hold station: drain the accumulator so a
        # stale residual cannot fire a pulse after the axis was told to stop.
        if magnitude == 0.0:
            self._residual = 0.0
            return 0.0

        pulse_displacement = self._floor * dt

        if magnitude >= self._floor:
            # Above the floor the channel is directly commandable, but the grid
            # is coarse: 0.055 has no representation against a 0.01 step. Fold
            # the outstanding residual back into the demand before snapping, so
            # successive cycles dither between the two adjacent grid levels and
            # the mean lands on the demand rather than on the nearest step.
            #
            # The dither is bounded to a single grid step, which is the whole of
            # what it is for -- it exists to spread one rounding error across
            # several cycles, never to add authority. Unbounded, it is a loaded
            # gun: the residual is a *displacement*, so dividing it by the
            # current ``dt`` converts a bank accumulated over slow cycles into a
            # velocity scaled by the ratio of the two intervals. A stretch of
            # cycles at the ``LoopRate`` ceiling of 0.5 s -- which a blocking
            # frame grab produces routinely -- followed by one normal 1/15 s
            # cycle measured a 0.35 command against a 0.10 cruise cap, a
            # full-authority lurch that defeats every jerk and braking limit
            # upstream of it.
            correction = max(-self._step, min(self._step, self._residual / dt))
            command = self._quantize(demand + correction)
            self._residual += (demand - command) * dt
            self._pulses += 1
        elif abs(self._residual + demand * dt) >= pulse_displacement:
            # Sub-threshold, and enough displacement has accumulated to justify
            # one pulse at the floor amplitude.
            self._residual += demand * dt
            command = self._quantize(math.copysign(self._floor, self._residual))
            self._residual -= command * dt
            self._pulses += 1
        else:
            # Sub-threshold and still charging: commanding anything here would
            # truncate to zero at the driver anyway.
            self._residual += demand * dt
            command = 0.0

        # Bound the bank in absolute displacement, not in units of the current
        # interval. Scaling the bound by ``dt`` let a slow cycle authorize a
        # large residual that a subsequent fast cycle would then discharge at a
        # correspondingly large velocity -- the bound grew in exactly the
        # circumstance it existed to guard against.
        bound = _MAX_RESIDUAL_PULSES * self._floor * _NOMINAL_PERIOD_SEC
        self._residual = max(-bound, min(bound, self._residual))
        return command

    def _quantize(self, value: float) -> float:
        """Snap to the actuator grid so the driver's truncation is a no-op."""
        steps = round(abs(value) / self._step)
        if steps == 0:
            return 0.0
        return math.copysign(steps * self._step + _QUANTIZATION_GUARD, value)
