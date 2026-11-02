"""Additive keyboard velocity overlay applied at the proxy.

The operator may trim the autonomous trajectory with W A S D (translate) and
Q R (yaw) without ever interrupting the mission. The overlay sits in
``BenchtopDroneProxy.move_velocity`` after the yaw clamp, so every command of
every stage passes through it and the dead reckoning sees the total, and it is
the single scoped exception to the ``vx == 0`` / ``vyaw == 0`` invariants of
the lateral pattern: bounded by its own ceilings, ramped, and zeroed by a
deadman when the station stops talking.

It has no path to the mission's control flow by construction: it holds no
reference to the failsafe, the runner, the context or any event, and it never
calls ``land``, ``takeoff`` or a ``request_*``. Whether it may act at all is a
``permit`` predicate injected by ``mission.py``; the stage policy lives there.

Semantics:

* no intent message for ``deadman_sec``, ``permit()`` false or the overlay not
  armed: the effective intent is zero and the release ramp takes the nudge to
  zero in ``release_sec``, never as a step;
* :meth:`disarm` latches until :meth:`arm`; arming clears any stale intent;
* the horizontal nudge is multiplied by ``scale_source()`` (the altitude
  governor's horizontal scale), so the altitude error that throttles the
  autonomous demand throttles the nudge as well;
* the yaw the nudge commands is integrated (``applied_vyaw *
  yaw_rate_per_unit_rad_s * dt``) and, past ``yaw_budget_deg`` in one
  direction, yaw in that direction is cut while the other stays free;
* the horizontal total is scaled onto ``max_total_horizontal`` preserving its
  direction, and the yaw total is bounded by ``max(|autonomous yaw|, max_vyaw)``.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Final, Optional, Tuple

from mvp_mission_bebop.controllers.nudge import NudgeShaper

logger = logging.getLogger("NudgeOverlay")

Triple = Tuple[float, float, float]

#: Upper clamp of the interval between two ``apply`` calls, s. Same bound as
#: ``engine.rate.MAX_INTERVAL_SEC``, restated so this module stays free of the
#: engine package.
MAX_INTERVAL_SEC: Final[float] = 0.5

#: Spacing of the ``[NUDGE ...]`` log line while the nudge is active, s.
LOG_PERIOD_SEC: Final[float] = 0.5

_ZERO: Final[Triple] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class NudgeState:
    """Read-only view of the overlay.

    Parameters
    ----------
    intent : Tuple[float, float, float]
        Last operator intent received, ``(forward, left, yaw_left)``.
    applied : Tuple[float, float, float]
        Nudge added on the last :meth:`NudgeOverlay.apply`.
    armed : bool
        Whether the overlay is armed.
    permitted : bool
        Last value of the permit predicate.
    yaw_budget_left_deg : float
        Heading the nudge may still add in the direction already used, degrees.
    """

    intent: Triple
    applied: Triple
    armed: bool
    permitted: bool
    yaw_budget_left_deg: float


class NudgeOverlay:
    """Deadman-guarded additive overlay of the operator's velocity intent.

    Parameters
    ----------
    cfg : NudgeConfig
        Ceilings, ramps, deadman and yaw budget.
    clock : Callable[[], float]
        Monotonic time source, s.
    permit : Callable[[], bool]
        Stage policy; the nudge acts only while it returns ``True``. An
        exception counts as ``False``.
    scale_source : Optional[Callable[[], float]]
        Multiplier of the horizontal nudge, clamped to ``[0, 1]``.
    yaw_rate_per_unit_rad_s : float
        Yaw rate of a unit normalized ``vyaw``, rad/s, for the budget.
    max_total_horizontal : float
        Ceiling of the horizontal total, normalized.

    Raises
    ------
    TypeError
        If ``cfg.enabled`` is not a boolean.
    ValueError
        If ``deadman_sec`` or ``yaw_budget_deg`` is not positive, or the
        totals are outside ``(0, 1]``.
    """

    def __init__(
        self,
        cfg: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        permit: Callable[[], bool] = lambda: True,
        scale_source: Optional[Callable[[], float]] = None,
        yaw_rate_per_unit_rad_s: float = 1.75,
        max_total_horizontal: float = 0.2,
    ) -> None:
        if not isinstance(cfg.enabled, bool):
            raise TypeError("nudge.enabled must be a bool")
        for name in ("deadman_sec", "yaw_budget_deg"):
            value = float(getattr(cfg, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"nudge.{name} must be positive and finite, got {value!r}")
        if not math.isfinite(max_total_horizontal) or not 0.0 < max_total_horizontal <= 1.0:
            raise ValueError(f"max_total_horizontal must be in (0, 1], got {max_total_horizontal!r}")
        if not math.isfinite(yaw_rate_per_unit_rad_s) or yaw_rate_per_unit_rad_s <= 0.0:
            raise ValueError(f"yaw_rate_per_unit_rad_s must be positive, got {yaw_rate_per_unit_rad_s!r}")
        self._cfg = cfg
        self._shaper = NudgeShaper(cfg)
        self._clock = clock
        self._permit = permit
        self._scale_source = scale_source
        self._yaw_rate = float(yaw_rate_per_unit_rad_s)
        self._max_total = float(max_total_horizontal)
        self._max_vyaw = float(cfg.max_vyaw)
        self._deadman = float(cfg.deadman_sec)
        self._budget = float(cfg.yaw_budget_deg)
        self._lock = threading.Lock()
        self._intent: Triple = _ZERO
        self._stamp: Optional[float] = None
        self._armed: bool = False
        self._permitted: bool = False
        self._last_apply: Optional[float] = None
        self._applied: Triple = _ZERO
        self._yaw_deg: float = 0.0
        self._budget_logged: int = 0
        self._last_log: Optional[float] = None

    @property
    def armed(self) -> bool:
        """Whether the overlay is armed."""
        with self._lock:
            return self._armed

    @property
    def enabled(self) -> bool:
        """``nudge.enabled``; a disabled overlay is the identity."""
        return bool(self._cfg.enabled)

    def set_intent(self, vx: float, vy: float, vyaw: float) -> None:
        """Record the operator intent and stamp it.

        Parameters
        ----------
        vx, vy, vyaw : float
            Forward, left and yaw-left intent, each saturated to ``[-1, 1]``.
            A message with a non-finite or non-numeric component is dropped.
        """
        try:
            values = tuple(float(v) for v in (vx, vy, vyaw))
        except (TypeError, ValueError):
            logger.warning("Nudge intent dropped: non-numeric component.")
            return
        if not all(math.isfinite(v) for v in values):
            logger.warning("Nudge intent dropped: non-finite component.")
            return
        clipped = tuple(max(-1.0, min(1.0, v)) for v in values)
        with self._lock:
            self._intent = (clipped[0], clipped[1], clipped[2])
            self._stamp = self._clock()

    def arm(self) -> None:
        """Enable the overlay; any intent received before arming is discarded."""
        with self._lock:
            if not self._armed:
                logger.info("Nudge armed.")
            self._armed = True
            self._intent = _ZERO
            self._stamp = None

    def disarm(self, reason: str) -> None:
        """Latch the overlay off until :meth:`arm`; the nudge is zeroed at once."""
        with self._lock:
            if self._armed:
                logger.info("Nudge disarmed: %s.", reason)
            self._armed = False
            self._intent = _ZERO
            self._stamp = None
            self._shaper.reset()
            self._applied = _ZERO

    def horizontal_magnitude(self) -> float:
        """Modulus of the horizontal nudge added on the last :meth:`apply`."""
        with self._lock:
            return math.hypot(self._applied[0], self._applied[1])

    def snapshot(self) -> NudgeState:
        """Current state, for telemetry and tests."""
        with self._lock:
            return NudgeState(
                intent=self._intent,
                applied=self._applied,
                armed=self._armed,
                permitted=self._permitted,
                yaw_budget_left_deg=max(0.0, self._budget - abs(self._yaw_deg)),
            )

    def _permit_now(self) -> bool:
        try:
            return bool(self._permit())
        except Exception as exc:  # noqa: BLE001 - a failing predicate forbids, never raises
            logger.debug("Nudge permit predicate failed: %s", exc)
            return False

    def _scale_now(self) -> float:
        if self._scale_source is None:
            return 1.0
        try:
            scale = float(self._scale_source())
        except Exception as exc:  # noqa: BLE001
            logger.debug("Nudge scale source failed: %s", exc)
            return 1.0
        return max(0.0, min(1.0, scale)) if math.isfinite(scale) else 1.0

    def apply(self, vx: float, vy: float, vyaw: float) -> Triple:
        """Return the total command, autonomous plus nudge.

        Parameters
        ----------
        vx, vy, vyaw : float
            Autonomous normalized command after every upstream clamp.

        Returns
        -------
        Tuple[float, float, float]
            ``(vx, vy, vyaw)`` to transmit. Exactly the input while the nudge
            is zero.
        """
        if not self._cfg.enabled:
            return vx, vy, vyaw
        permitted = self._permit_now()
        with self._lock:
            now = self._clock()
            if self._last_apply is None:
                dt = 0.0
            else:
                dt = min(MAX_INTERVAL_SEC, max(0.0, now - self._last_apply))
            self._last_apply = now
            self._permitted = permitted
            fresh = self._stamp is not None and now - self._stamp <= self._deadman
            intent = self._intent if (self._armed and permitted and fresh) else _ZERO

            ix, iy, iyaw = intent
            if iyaw > 0.0 and self._yaw_deg >= self._budget:
                iyaw = 0.0
            elif iyaw < 0.0 and self._yaw_deg <= -self._budget:
                iyaw = 0.0
            nx, ny, nyaw = self._shaper.step((ix, iy, iyaw), dt)

            if (nyaw > 0.0 and self._yaw_deg >= self._budget) or (nyaw < 0.0 and self._yaw_deg <= -self._budget):
                self._shaper.zero_yaw()
                nyaw = 0.0
                direction = 1 if self._yaw_deg > 0.0 else -1
                if self._budget_logged != direction:
                    self._budget_logged = direction
                    logger.warning(
                        "Nudge yaw budget of %.0f deg exhausted to the %s; yaw in that direction is ignored.",
                        self._budget,
                        "left" if direction > 0 else "right",
                    )
            self._yaw_deg += math.degrees(nyaw * self._yaw_rate * dt)
            if abs(self._yaw_deg) < self._budget:
                self._budget_logged = 0

            scale = self._scale_now() if (nx or ny) else 1.0
            nx, ny = nx * scale, ny * scale
            self._applied = (nx, ny, nyaw)
            if nx == 0.0 and ny == 0.0 and nyaw == 0.0:
                return vx, vy, vyaw

            tx, ty = vx + nx, vy + ny
            magnitude = math.hypot(tx, ty)
            if magnitude > self._max_total:
                tx, ty = tx * self._max_total / magnitude, ty * self._max_total / magnitude
            yaw_limit = max(abs(vyaw), self._max_vyaw)
            tyaw = max(-yaw_limit, min(yaw_limit, vyaw + nyaw))

            if self._last_log is None or now - self._last_log >= LOG_PERIOD_SEC:
                self._last_log = now
                logger.info(
                    "[NUDGE intent=(%.0f,%.0f,%.0f) applied=(%.3f,%.3f,%.3f) budget=%.1f]",
                    intent[0],
                    intent[1],
                    intent[2],
                    nx,
                    ny,
                    nyaw,
                    max(0.0, self._budget - abs(self._yaw_deg)),
                )
            return tx, ty, tyaw
