"""Base abstractions and status definitions for mission steps."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto

from mvp_mission_bebop.context import MissionContext


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
