"""Base abstractions and status definitions for mission steps."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Any, Optional, Sequence

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.cross_axis_hold import CrossAxisHold, body_velocity
from mvp_mission_bebop.perception.classes import is_accident_class, is_victim_class


def _accepts_yaw(governor: Any) -> bool:
    """Whether ``governor.compute_vz`` takes ``yaw_commanded`` (both shipped governors do)."""
    compute = getattr(governor, "compute_vz", None)
    if compute is None:
        return False
    try:
        parameters = inspect.signature(compute).parameters
    except (TypeError, ValueError):
        return False
    return "yaw_commanded" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def governed_vz(
    ctx: MissionContext,
    altitude: float,
    dt: Optional[float] = None,
    vx_commanded: Optional[float] = None,
    yaw_commanded: Optional[float] = None,
) -> float:
    """The altitude governor's ``vz`` for one cycle, its integral held while the altitude only propagates.

    ``OdometrySupervisor.vertical_coasting`` is true when the terrain-aware
    filter has accepted no ``speedZ`` lately; integrating an error nobody
    measured would wind the governor up (section 3.8). Supervisors and
    governors without that notion are called exactly as before, with the
    arguments the call site gives and no others.

    The operator's keyboard nudge translates the airframe like any horizontal
    demand, so its magnitude (``ctx.nudge.horizontal_magnitude()``) is added to
    ``vx_commanded`` and the governor's translation feedforward sees the
    total. ``yaw_commanded`` is forwarded only when given and accepted by the
    governor, so doubles that predate the yaw term are called with the
    arguments they accept.
    """
    nudge = getattr(ctx, "nudge", None)
    magnitude = getattr(nudge, "horizontal_magnitude", None)
    if callable(magnitude):
        extra = float(magnitude())
        if extra > 0.0:
            vx_commanded = (abs(vx_commanded) if vx_commanded is not None else 0.0) + extra
    kwargs = {} if vx_commanded is None else {"vx_commanded": vx_commanded}
    if yaw_commanded is not None and _accepts_yaw(ctx.governor):
        kwargs["yaw_commanded"] = yaw_commanded
    coasting = getattr(ctx.odom_supervisor, "vertical_coasting", None)
    if callable(coasting) and coasting():
        kwargs["hold_integrator"] = True
    if dt is None:
        return ctx.governor.compute_vz(altitude, **kwargs)
    return ctx.governor.compute_vz(altitude, dt, **kwargs)


def select_target(ctx: MissionContext, targets: Sequence[Any]) -> Optional[Any]:
    """The target a stage reports and locates, through the persistent selector when wired.

    Advances the selector one frame, so it is called once per new frame,
    including frames without targets. When the selector withholds a target
    (no priority class, or its track momentarily missing) the most confident
    detection is returned, so a confirmation never lacks a target to name.
    Contexts without a selector (unit doubles) keep the per-frame maximum.
    Victim detections are never prioritized over accidents.

    Parameters
    ----------
    ctx : MissionContext
        Mission context; ``target_selector`` is optional.
    targets : Sequence[Detection]
        Detections of the frame, already filtered by ``vision.target_classes``.

    Returns
    -------
    Optional[Detection]
        The chosen detection, or ``None`` when ``targets`` is empty.
    """
    selector = getattr(ctx, "target_selector", None)
    if selector is not None:
        chosen = selector.select(targets)
        if chosen is not None:
            return chosen
    if not targets:
        return None
    non_victims = [t for t in targets if not is_victim_class(getattr(t, "class_name", ""))]
    candidates = non_victims if non_victims else targets
    accidents = [t for t in candidates if is_accident_class(getattr(t, "class_name", ""))]
    pool = accidents if accidents else candidates
    return max(pool, key=lambda detection: detection.confidence)


def cross_axis_correction(step: Any, ctx: MissionContext, main_command: float, dt: float) -> float:
    """Orthogonal ``vx`` correction for a lateral leg (``translation_hold``).

    The lateral steps used to transmit ``vx`` as a literal ``0.0``; with zero
    pitch commanded the firmware does not hold the forward axis while the
    lateral one translates. This closes it on the measured forward body
    velocity (:class:`~mvp_mission_bebop.controllers.cross_axis_hold.CrossAxisHold`),
    bounded by ``translation_hold.max_correction``. The law is kept on the
    step (``step._cross_hold``) and built on first use; a context without the
    section (unit doubles) or a disabled law returns ``0.0`` exactly, so the
    transmitted ``vx`` is bit for bit the historical literal.
    """
    cfg = getattr(getattr(ctx, "params", None), "translation_hold", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        return 0.0
    hold = getattr(step, "_cross_hold", None)
    if hold is None:
        try:
            from mvp_mission_bebop.controllers.cross_axis_hold import CrossAxisHold
            hold = CrossAxisHold(cfg)
        except (TypeError, ValueError, ImportError):
            return 0.0
        step._cross_hold = hold
    snapshot = ctx.odom_supervisor.snapshot()
    vx = getattr(snapshot, "vx", None)
    vy = getattr(snapshot, "vy", None)
    yaw = getattr(snapshot, "yaw", 0.0)
    if not isinstance(vx, (int, float)) or not isinstance(vy, (int, float)):
        return 0.0
    from mvp_mission_bebop.controllers.cross_axis_hold import body_velocity
    forward, _left = body_velocity(float(vx), float(vy), float(yaw) if isinstance(yaw, (int, float)) else 0.0)
    correction, _yaw = hold.update("vy", main_command, forward, None, dt)
    return correction


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
