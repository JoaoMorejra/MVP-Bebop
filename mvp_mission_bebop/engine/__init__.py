"""Execution engine: loop pacing, interrupt handling, and the step sequencer.

``MissionRunner`` is reachable only through ``mvp_mission_bebop.engine.runner``.
Re-exporting it here would make importing the package's timing primitives pull
in the mission context, and through it every controller -- which is a cycle,
since the controllers themselves depend on the timing primitives.
"""

from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.engine.signals import EmergencyHandler

__all__ = ["Deadline", "EmergencyHandler", "LoopRate"]
