"""Which ground-station stage jumps the running mission accepts.

The station publishes an ``Int32`` on ``mission/goto_stage``; the running step
unwinds and the runner continues at the requested stage. Before this module
the only refusal was Stage 1 above 0.25 m of relative altitude, so after a
touchdown, on the ground, ``goto 1`` was accepted and the aircraft launched
again. The decision now rests on the mission's own progress, with altitude as
an additional condition rather than the only one.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Final, Iterable, Optional, Tuple

logger = logging.getLogger("StageGate")

#: Relative altitude above which the airframe is taken to be flying, metres.
AIRBORNE_ALTITUDE_M: Final[float] = 0.25

#: Stages that presuppose a completed takeoff when the run includes Stage 1.
#: Stage 5 is deliberately absent: the battery return jumps to it from any
#: stage, the climb included, and refusing it would keep a dying pack aloft.
_FLIGHT_STAGES: Final[Tuple[int, ...]] = (2, 3, 4)


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
        requests go to ``ctx.request_stage``.
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
        try:
            return float(self._altitude()) > AIRBORNE_ALTITUDE_M
        except Exception:  # noqa: BLE001 - no reading is not a reason to allow it
            return True

    def __call__(self, requested: int) -> bool:
        """Gate ``requested``; return True when it was forwarded to the runner."""
        blackboard = self._ctx.blackboard
        reason = refuse_stage_request(
            int(requested),
            stage_numbers=self._stages,
            current_stage=self._ctx.current_stage,
            takeoff_committed=bool(getattr(blackboard, "takeoff_committed", False)),
            takeoff_complete=bool(getattr(blackboard, "takeoff_complete", False)),
            airborne=self._airborne() if int(requested) == 1 else False,
        )
        if reason is not None:
            logger.warning("Stage request %d refused: %s.", requested, reason)
            return False
        logger.info("Stage %d requested by the ground station.", requested)
        self._ctx.request_stage(int(requested))
        return True
