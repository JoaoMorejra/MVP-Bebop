"""Step factories by mission profile."""

from __future__ import annotations

import functools
from typing import Callable, Dict

from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import BaseStep
from mvp_mission_bebop.steps.inspection import NadirInspectionStep
from mvp_mission_bebop.steps.rtl import ClosedLoopRTLStep
from mvp_mission_bebop.steps.search import LateralSearchStep
from mvp_mission_bebop.steps.takeoff import TakeoffStep
from mvp_mission_bebop.steps.tracking import VisualServoingStep


def build_steps(mode: MissionMode) -> Dict[int, Callable[[], BaseStep]]:
    """Factories for the five stages of ``mode``, keyed by stage number.

    Stage 1 is identical in every profile. Stages 2 to 5 receive the profile at
    construction; it is never read from the parameter document by the steps.

    Parameters
    ----------
    mode : MissionMode
        Resolved mission profile (``apply_mission_mode`` has already run).

    Returns
    -------
    Dict[int, Callable[[], BaseStep]]
        Zero-argument factories for stages 1 to 5, in flight order.
    """
    return {
        1: functools.partial(TakeoffStep, mode=mode),
        2: functools.partial(LateralSearchStep, mode=mode),
        3: functools.partial(VisualServoingStep, mode=mode),
        4: functools.partial(NadirInspectionStep, mode=mode),
        5: functools.partial(ClosedLoopRTLStep, mode=mode),
    }
