"""Which ground-station stage jumps the running mission accepts.

The station publishes an ``Int32`` on ``mission/goto_stage``; the running step
unwinds and the runner continues at the requested stage. Before this module
the only refusal was Stage 1 above 0.25 m of relative altitude, so after a
touchdown, on the ground, ``goto 1`` was accepted and the aircraft launched
again. The decision now rests on the mission's own progress, with altitude as
an additional condition rather than the only one.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, ContextManager, Final, Iterable, Optional, Tuple

logger = logging.getLogger("StageGate")

#: Relative altitude above which the airframe is taken to be flying, metres.
AIRBORNE_ALTITUDE_M: Final[float] = 0.25

#: Stages that presuppose a completed takeoff when the run includes Stage 1.
#: Stage 5 is deliberately absent: the battery return jumps to it from any
#: stage, the climb included, and refusing it would keep a dying pack aloft.
_FLIGHT_STAGES: Final[Tuple[int, ...]] = (2, 3, 4)

#: The return stage, ``ClosedLoopRTLStep``.
_RETURN_STAGE: Final[int] = 5


def stage_lock(ctx: Any) -> ContextManager[Any]:
    """The context's stage-transition lock, or a no-op for a context without one.

    ``MissionContext.stage_lock`` serializes the stage gate and the jump
    request it leads to (executor threads) against the runner naming the
    stage it is about to run (mission thread). Held only across the decision
    and the bookkeeping, never across logging, milestones or a step.

    Parameters
    ----------
    ctx : Any
        The mission context, or a test double without the lock.

    Returns
    -------
    ContextManager[Any]
        The lock itself, usable in a ``with`` block.
    """
    lock = getattr(ctx, "stage_lock", None)
    return lock if lock is not None else contextlib.nullcontext()


def is_airborne(relative_altitude: Callable[[], float]) -> bool:
    """Whether the airframe counts as flying, from its relative altitude.

    The single predicate behind the stage gate and the critical-charge return
    hook (``mission.py``), so both judge the same airframe the same way.

    Parameters
    ----------
    relative_altitude : Callable[[], float]
        Returns the relative altitude in metres.

    Returns
    -------
    bool
        ``True`` above :data:`AIRBORNE_ALTITUDE_M`, and when the reading
        raises: no reading is not a reason to treat the airframe as landed.
        ``False`` at or below the threshold, and for a NaN reading.

    Raises
    ------
    TypeError
        If ``relative_altitude`` is not callable.
    """
    if not callable(relative_altitude):
        raise TypeError(f"relative_altitude must be callable, got {type(relative_altitude).__name__}")
    try:
        return float(relative_altitude()) > AIRBORNE_ALTITUDE_M
    except Exception:  # noqa: BLE001 - no reading counts as airborne
        return True


def refuse_stage_request(
    requested: int,
    *,
    stage_numbers: Iterable[int],
    current_stage: Optional[int],
    takeoff_committed: bool,
    takeoff_complete: bool,
    airborne: bool,
) -> Optional[str]:
    """Return why a stage jump is refused, or ``None`` when it is accepted.

    Parameters
    ----------
    requested : int
        Stage the station asked for.
    stage_numbers : iterable of int
        Stages this run holds.
    current_stage : int or None
        Stage being executed; ``None`` before the first step starts.
    takeoff_committed : bool
        The Stage 1 countdown has ended and takeoff has been, or is being,
        commanded. From here on Stage 1 can never run again.
    takeoff_complete : bool
        Stage 1 returned success: the aircraft climbed and stabilised.
    airborne : bool
        Odometry places the airframe above :data:`AIRBORNE_ALTITUDE_M`, or no
        reading is available.

    Returns
    -------
    str or None
        Reason for the refusal, for the log; ``None`` to accept.
    """
    stages = tuple(stage_numbers)
    if requested not in stages:
        return f"stage {requested} is not in this run {list(stages)}"

    if requested == 1:
        if current_stage is not None and current_stage > 1:
            return f"the mission is already past Stage 1 (in Stage {current_stage})"
        if takeoff_committed:
            return "the countdown has ended; Stage 1 cannot restart"
        if airborne:
            return "the aircraft is airborne"
        return None

    if requested == 5 and current_stage == 5:
        return "Stage 5 is already running"

    if requested in _FLIGHT_STAGES and 1 in stages and not takeoff_complete:
        return f"Stage 1 has not completed the takeoff; Stage {requested} presupposes flight"

    return None


class StageRequestHandler:
    """Callback for ``mission/goto_stage``: gate each request, forward the accepted ones.

    Parameters
    ----------
    ctx : MissionContext
        Read for ``current_stage`` and the blackboard takeoff flags; accepted
        requests go to ``ctx.request_stage``, except Stage 5 on a fresh critical
        charge, which goes to ``ctx.request_battery_return`` (forced return).
    stage_numbers : iterable of int
        Stages this run holds.
    relative_altitude : callable
        Returns the current relative altitude in metres. Any exception is read
        as airborne: no reading is not a reason to allow a launch.
    """

    def __init__(
        self,
        ctx: Any,
        stage_numbers: Iterable[int],
        relative_altitude: Callable[[], float],
    ) -> None:
        self._ctx = ctx
        self._stages: Tuple[int, ...] = tuple(stage_numbers)
        self._altitude = relative_altitude

    def _airborne(self) -> bool:
        return is_airborne(self._altitude)

    def __call__(self, requested: int) -> bool:
        """Gate ``requested``; return True when it was forwarded to the runner."""
        stage = int(requested)
        airborne = self._airborne()
        critical = self._critical_charge(airborne) if stage == _RETURN_STAGE else None
        if critical is not None:
            logger.info("Stage %d requested by the ground station on a critical charge.", stage)
            return bool(self._ctx.request_battery_return(critical, source="station"))

        with stage_lock(self._ctx):
            blackboard = self._ctx.blackboard
            reason = refuse_stage_request(
                stage,
                stage_numbers=self._stages,
                current_stage=self._ctx.current_stage,
                takeoff_committed=bool(getattr(blackboard, "takeoff_committed", False)),
                takeoff_complete=bool(getattr(blackboard, "takeoff_complete", False)),
                airborne=airborne if stage == 1 else False,
            )
            if reason is None:
                self._ctx.request_stage(stage)
        if reason is not None:
            logger.warning("Stage request %d refused: %s.", stage, reason)
            return False
        logger.info("Stage %d requested by the ground station.", stage)
        return True

    def _critical_charge(self, airborne: bool) -> Optional[str]:
        """``BatterySupervisor.critical_reason`` of the context, or ``None``.

        A ``goto_stage 5`` that arrives airborne, on a real flight, with a
        fresh reading at or below the return threshold is the station's
        battery trigger, possibly ahead of the mission's own hook. It is marked
        as the forced return, so Stage 5 flies the return of the profile even
        with ``rtl.enabled = false``. On the ground, on the bench
        (``params.no_fly``), without a battery supervisor or without a fresh
        reading, the request is an operator-commanded Stage 5 and stays
        unforced, as the hook itself would.

        Parameters
        ----------
        airborne : bool
            :func:`is_airborne` for this request.
        """
        if not airborne or bool(getattr(getattr(self._ctx, "params", None), "no_fly", False)):
            return None
        battery = getattr(getattr(self._ctx, "failsafe", None), "battery_supervisor", None)
        critical_reason = getattr(battery, "critical_reason", None)
        if not callable(critical_reason) or not callable(getattr(self._ctx, "request_battery_return", None)):
            return None
        try:
            return critical_reason()
        except Exception as exc:  # noqa: BLE001 - an unreadable charge is not a reason to act
            logger.warning("Battery state unavailable for the stage request (%s).", exc)
            return None
