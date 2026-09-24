"""Perception pipelines decoupling detector inference from the flight control loops.

The control loops run at ``control_loop_hz`` (15 Hz by default). YOLOv8n on the
ground station's CPU costs 150-250 ms per frame, and while the loops called the
detector inline that cost set the cadence of every guidance law, of every
velocity command reaching the Bebop, and of the annotated stream the GCS
displays. The modules here move inference behind a latest-result slot so that
each consumer decides only how old an observation it is willing to act on.
"""

from mvp_mission_bebop.perception.worker import (
    LatestResultSlot,
    PerceptionPipeline,
    PerceptionSample,
    PerceptionWorker,
    SynchronousPerception,
    detector_kwargs,
    normalize_imgsz,
)

__all__ = [
    "LatestResultSlot",
    "PerceptionPipeline",
    "PerceptionSample",
    "PerceptionWorker",
    "SynchronousPerception",
    "detector_kwargs",
    "normalize_imgsz",
]
