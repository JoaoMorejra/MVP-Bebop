"""Rate shaping of the operator's keyboard nudge.

The station sends a ternary intent per axis (-1, 0, +1) for forward, left and
yaw-left. A step of that size on a normalized velocity is a step in commanded
pitch or roll, which the Bebop flies as a jolt; this shaper turns it into a
linear ramp up (``attack_sec`` to full authority) and down (``release_sec`` to
zero, never crossing it), and saturates the horizontal pair as a vector so a
diagonal is no faster than a single axis.

Pure: no clock, ``dt`` is injected.
"""

from __future__ import annotations

import math
from typing import Any, Tuple

Triple = Tuple[float, float, float]


def _ramp(current: float, target: float, attack_rate: float, release_rate: float, dt: float) -> float:
    if current * target < 0.0:
        step = release_rate * dt
        return max(0.0, current - step) if current > 0.0 else min(0.0, current + step)
    if abs(target) < abs(current):
        step = release_rate * dt
        return max(target, current - step) if current > target else min(target, current + step)
    step = attack_rate * dt
    return min(target, current + step) if target > current else max(target, current - step)


class NudgeShaper:
    """Attack and release ramps with vector saturation.

    Parameters
    ----------
    cfg : NudgeConfig
        ``max_horizontal``, ``max_vyaw``, ``attack_sec``, ``release_sec``.

    Raises
    ------
    TypeError
        If a field is not numeric.
    ValueError
        If a limit is outside ``(0, 1]`` or a time is not positive.
    """

    def __init__(self, cfg: Any) -> None:
        values = {}
        for name in ("max_horizontal", "max_vyaw", "attack_sec", "release_sec"):
            value = getattr(cfg, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"nudge.{name} must be a number, got {type(value).__name__}")
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"nudge.{name} must be positive and finite, got {value!r}")
            values[name] = float(value)
        if values["max_horizontal"] > 1.0 or values["max_vyaw"] > 1.0:
            raise ValueError("nudge.max_horizontal and nudge.max_vyaw are normalized and must not exceed 1")
        self._max_h: float = values["max_horizontal"]
        self._max_yaw: float = values["max_vyaw"]
        self._attack_sec: float = values["attack_sec"]
        self._release_sec: float = values["release_sec"]
        self._output: Triple = (0.0, 0.0, 0.0)

    @property
    def output(self) -> Triple:
        """Last shaped output."""
        return self._output

    def reset(self) -> None:
        """Zero the output at once."""
        self._output = (0.0, 0.0, 0.0)

    def zero_yaw(self) -> None:
        """Zero the yaw output at once, keeping the horizontal ramps."""
        self._output = (self._output[0], self._output[1], 0.0)

    def step(self, intent: Triple, dt: float) -> Triple:
        """Advance the ramps one cycle.

        Parameters
        ----------
        intent : Tuple[float, float, float]
            ``(forward, left, yaw_left)`` in ``[-1, 1]``. A non-finite component
            is read as zero, so the output decays instead of propagating it.
        dt : float
            Elapsed interval, s. Non-positive or non-finite holds the output.

        Returns
        -------
        Tuple[float, float, float]
            Shaped ``(vx, vy, vyaw)``, normalized.
        """
        if not math.isfinite(dt) or dt <= 0.0:
            return self._output
        clean = []
        for value in intent:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = 0.0
            clean.append(max(-1.0, min(1.0, number)) if math.isfinite(number) else 0.0)
        ix, iy, iyaw = clean
        tx, ty = ix * self._max_h, iy * self._max_h
        magnitude = math.hypot(tx, ty)
        if magnitude > self._max_h:
            tx, ty = tx * self._max_h / magnitude, ty * self._max_h / magnitude
        tyaw = iyaw * self._max_yaw

        h_attack, h_release = self._max_h / self._attack_sec, self._max_h / self._release_sec
        y_attack, y_release = self._max_yaw / self._attack_sec, self._max_yaw / self._release_sec
        cx, cy, cyaw = self._output
        ox = _ramp(cx, tx, h_attack, h_release, dt)
        oy = _ramp(cy, ty, h_attack, h_release, dt)
        oyaw = _ramp(cyaw, tyaw, y_attack, y_release, dt)
        magnitude = math.hypot(ox, oy)
        if magnitude > self._max_h:
            ox, oy = ox * self._max_h / magnitude, oy * self._max_h / magnitude
        self._output = (ox, oy, oyaw)
        return self._output
