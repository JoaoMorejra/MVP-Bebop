"""Base abstractions and status definitions for mission steps."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Optional

from mvp_mission_bebop.context import MissionContext


def governed_vz(
    ctx: MissionContext, altitude: float, dt: Optional[float] = None, vx_commanded: Optional[float] = None
) -> float:
    """The altitude governor's ``vz`` for one cycle, its integral held while the altitude only propagates.

    ``OdometrySupervisor.vertical_coasting`` is true when the terrain-aware
    filter has accepted no ``speedZ`` lately; integrating an error nobody
    measured would wind the governor up (section 3.8). Supervisors and
    governors without that notion are called exactly as before, with the
    arguments the call site gives and no others.
    """
    kwargs = {} if vx_commanded is None else {"vx_commanded": vx_commanded}
    coasting = getattr(ctx.odom_supervisor, "vertical_coasting", None)
    if callable(coasting) and coasting():
        kwargs["hold_integrator"] = True
    if dt is None:
        return ctx.governor.compute_vz(altitude, **kwargs)
    return ctx.governor.compute_vz(altitude, dt, **kwargs)


class StepStatus(Enum):
    """Execution status returned by a mission step."""

    SUCCESS = auto()
    FAILURE = auto()
    ABORTED = auto()


class BaseStep(ABC):
    """Abstract base class for all deterministic mission steps."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def execute(self, ctx: MissionContext) -> StepStatus:
        """Execute step logic within the provided mission context.

        Parameters
        ----------
        ctx : MissionContext
            Shared flight context and interfaces.

        Returns
        -------
        StepStatus
            Outcome of the step execution.
        """
