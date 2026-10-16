"""Where the airframe is relative to the edge of the pad it took off from.

The Bebop holds height with a downward sonar. While the airframe is over the
pad the sonar ranges the pad top; past the edge it ranges the floor, one pad
height further down. The firmware does not see a pad edge, it sees its altitude
reading jump, and it answers by moving the airframe (section 1.5 of
``docs/PROMPT_IMPLEMENTACAO_TOTEM_VOO_ALTITUDE.md``). Leaving the pad looks like
a sudden climb and the firmware sinks; coming back looks like a sudden loss of
height and it climbs.

Under a nearest-echo sonar model the pad stays inside the beam until the
along-track position

    ``s* = edge_ahead_m + h * tan(sonar_half_angle)``

where ``h`` is the height above the pad top, and the reading steps by the pad
height at ``s*`` in both directions. This module brackets ``s*`` with a guard
window,

    ``[s* - edge_uncertainty_m - guard_lead_m, s* + edge_uncertainty_m + guard_extent_m]``

and names four situations from the along-track position and its direction:
over the pad (``ON``), leaving it (``LEAVING``), off it (``OFF``) and coming
back (``ENTERING``). The edge guard acts in the two transit states.

The model is one-dimensional, along the outbound track, which is the body left
axis (:func:`outbound_track_m`). Drift along X is not seen by the monitor; it
can bring the real edge crossing earlier or later than the along-track position
predicts, and it is the risk measured in the field, not modelled here.

Pure: no ROS, no clock. The caller supplies the interval.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Final, Optional

from mvp_mission_bebop.parameters import PlatformConfig

logger = logging.getLogger("PlatformMonitor")

#: Height above the pad floor used for ``s*``, in metres. Below it the beam
#: footprint is centimetres wide and the edge is effectively at the pad edge.
MIN_BEAM_HEIGHT_M: Final[float] = 0.3

#: Separation between the threshold that enters a region and the one that
#: leaves it again, in metres. Larger than the odometric jitter at hover.
HYSTERESIS_M: Final[float] = 0.05

#: Displacement from the last direction decision below which motion is noise,
#: in metres.
DIRECTION_NOISE_M: Final[float] = 0.02

#: Consecutive samples beyond :data:`DIRECTION_NOISE_M`, all on the same side,
#: that confirm a direction.
DIRECTION_CONFIRM_SAMPLES: Final[int] = 3

#: Residual time-to-live below which an override counts as expired, in seconds.
_TTL_EPSILON_SEC: Final[float] = 1e-9


class PlatformState(str, Enum):
    """Position of the airframe relative to the pad edge."""

    #: No frozen origin, no ``edge_ahead_m``, or a non-finite input.
    UNKNOWN = "unknown"
    #: Over the pad; the sonar ranges the pad top.
    ON = "on"
    #: Outbound, inside the guard window.
    LEAVING = "leaving"
    #: Clear of the pad; the sonar ranges the floor.
    OFF = "off"
    #: Inbound, inside the guard window.
    ENTERING = "entering"


#: States in which the edge guard is active.
GUARDED_STATES: Final[frozenset] = frozenset({PlatformState.LEAVING, PlatformState.ENTERING})


@dataclass(frozen=True)
class PlatformReport:
    """Outcome of one :meth:`PlatformMonitor.update`."""

    state: PlatformState
    #: True in :data:`GUARDED_STATES`.
    guard_active: bool
    #: Along-track position used for this update, metres, positive outbound.
    along_track_m: Optional[float]
    #: ``s*``, the expected along-track position of the sonar step, metres.
    edge_step_m: Optional[float]
    #: ``+1`` moving away from the origin, ``-1`` returning, ``0`` unknown.
    direction: int
    #: Time since the last state change, seconds; ``None`` before the first.
    seconds_since_transition: Optional[float]


#: Report of a monitor that has seen nothing yet.
UNKNOWN_REPORT: Final[PlatformReport] = PlatformReport(
    state=PlatformState.UNKNOWN,
    guard_active=False,
    along_track_m=None,
    edge_step_m=None,
    direction=0,
    seconds_since_transition=None,
)


def _finite(value: Optional[float]) -> Optional[float]:
    """``value`` as a finite float, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def outbound_track_m(origin_dx: float, origin_dy: float, yaw_rad: float) -> float:
    """Displacement of the airframe to the left of the origin, projected on the body.

    The outbound track of the mission is the body left axis (FLU, +y left). With
    the displacement of the airframe from the origin equal to
    ``-(origin_dx, origin_dy)`` and the body left unit vector
    ``(-sin(yaw), cos(yaw))``, the projection is
    ``origin_dx * sin(yaw) - origin_dy * cos(yaw)``.

    Parameters
    ----------
    origin_dx, origin_dy : float
        Origin minus current position in the odometry frame, metres.
    yaw_rad : float
        Heading of the airframe in the odometry frame, radians.

    Returns
    -------
    float
        Metres to the left of the origin, positive to the left. Forward
        displacement does not contribute.

    Raises
    ------
    ValueError
        If any argument is not a finite number.
    """
    values = (origin_dx, origin_dy, yaw_rad)
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
        raise ValueError(f"outbound_track_m requires finite arguments, got {values!r}")
    return origin_dx * math.sin(yaw_rad) - origin_dy * math.cos(yaw_rad)


class PlatformMonitor:
    """Classifies the along-track position against the pad edge.

    Parameters
    ----------
    cfg : PlatformConfig
        Pad geometry and guard window. Read on every update, so a value
        changed after construction takes effect on the next sample.
    """

    __slots__ = (
        "_cfg",
        "_state",
        "_direction",
        "_anchor",
        "_candidate",
        "_candidate_count",
        "_since_transition",
        "_override",
        "_override_ttl",
        "_report",
    )

    def __init__(self, cfg: PlatformConfig) -> None:
        if not isinstance(cfg, PlatformConfig):
            raise TypeError(f"cfg must be a PlatformConfig, got {type(cfg).__name__}")
        self._cfg = cfg
        self._state = PlatformState.UNKNOWN
        self._direction = 0
        self._anchor: Optional[float] = None
        self._candidate = 0
        self._candidate_count = 0
        self._since_transition: Optional[float] = None
        self._override: Optional[float] = None
        self._override_ttl = 0.0
        self._report = UNKNOWN_REPORT

    @property
    def report(self) -> PlatformReport:
        """The report of the most recent update."""
        return self._report

    def reset(self) -> None:
        """Forget the state, the direction and any override."""
        self._state = PlatformState.UNKNOWN
        self._direction = 0
        self._anchor = None
        self._candidate = 0
        self._candidate_count = 0
        self._since_transition = None
        self._override = None
        self._override_ttl = 0.0
        self._report = UNKNOWN_REPORT

    def override_along_track(self, along_track_m: float, ttl_sec: float) -> None:
        """Use an externally observed along-track position for ``ttl_sec``.

        Stage 5 sees the pad marker, which sits at the takeoff point: the
        marker offset is an along-track position independent of the
        odometry. It stands in for the odometric value on every update until
        ``ttl_sec`` of supplied intervals have elapsed.

        Parameters
        ----------
        along_track_m : float
            Position along the outbound track, metres. Non-finite values are
            ignored.
        ttl_sec : float
            Validity, seconds. Non-positive or non-finite values are ignored.
        """
        position = _finite(along_track_m)
        ttl = _finite(ttl_sec)
        if position is None or ttl is None or ttl <= 0.0:
            return
        self._override = position
        self._override_ttl = ttl

    def update(
        self, along_track_m: Optional[float], height_above_plane_m: float, dt: float
    ) -> PlatformReport:
        """Advance the classification by one sample.

        Parameters
        ----------
        along_track_m : Optional[float]
            Position along the outbound track from the frozen hover origin,
            metres, positive outbound. ``None`` before the origin exists.
        height_above_plane_m : float
            Height above the pad top, metres (the relative altitude).
        dt : float
            Interval since the previous update, seconds. Non-finite or negative
            values count as zero.

        Returns
        -------
        PlatformReport
            ``UNKNOWN`` with the guard inactive whenever ``edge_ahead_m`` is
            ``None`` or an input is missing or non-finite. Never raises on bad
            input.
        """
        interval = _finite(dt)
        interval = interval if interval is not None and interval > 0.0 else 0.0

        position = _finite(along_track_m)
        if self._override is not None:
            if self._override_ttl > _TTL_EPSILON_SEC:
                position = self._override
                self._override_ttl -= interval
            else:
                self._override = None
                self._override_ttl = 0.0

        cfg = self._cfg
        edge = _finite(cfg.edge_ahead_m)
        height = _finite(height_above_plane_m)
        if edge is None or position is None or height is None:
            return self._settle(PlatformState.UNKNOWN, position, None, interval, height)

        beam_height = max(height, MIN_BEAM_HEIGHT_M)
        edge_step = edge + beam_height * math.tan(math.radians(cfg.sonar_half_angle_deg))
        uncertainty = max(0.0, cfg.edge_uncertainty_m)
        lower = edge_step - uncertainty - max(0.0, cfg.guard_lead_m)
        upper = edge_step + uncertainty + max(0.0, cfg.guard_extent_m)
        on_pad = edge - uncertainty

        self._track_direction(position)
        state = self._state
        direction = self._direction

        if state is PlatformState.UNKNOWN:
            state = PlatformState.ON if position < edge_step else PlatformState.OFF
        elif state is PlatformState.ON:
            if position >= lower and direction > 0:
                state = PlatformState.LEAVING
        elif state is PlatformState.LEAVING:
            if position > upper:
                state = PlatformState.OFF
            elif position < lower - HYSTERESIS_M:
                state = PlatformState.ON
        elif state is PlatformState.OFF:
            if position <= upper - HYSTERESIS_M and direction < 0:
                state = PlatformState.ENTERING
        elif state is PlatformState.ENTERING:
            if position <= on_pad:
                state = PlatformState.ON
            elif position > upper:
                state = PlatformState.OFF

        return self._settle(state, position, edge_step, interval, height)

    # ------------------------------------------------------------- internals

    def _track_direction(self, position: float) -> None:
        """Resolve the direction of travel against a decision anchor.

        A per-sample difference cannot separate slow motion from noise at a
        high sample rate: 0.05 m/s at 15 Hz is 3 mm per sample. The
        displacement is measured from the position where the direction was
        last decided instead, and a direction is adopted after
        :data:`DIRECTION_CONFIRM_SAMPLES` consecutive samples all beyond
        :data:`DIRECTION_NOISE_M` on the same side.
        """
        if self._anchor is None:
            self._anchor = position
            return

        offset = position - self._anchor
        if abs(offset) < DIRECTION_NOISE_M:
            self._candidate = 0
            self._candidate_count = 0
            return

        sign = 1 if offset > 0.0 else -1
        if sign == self._direction:
            # Still travelling the decided way: follow the leading edge so a
            # reversal is measured from where it began.
            self._anchor = position
            self._candidate = 0
            self._candidate_count = 0
            return

        if sign == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = sign
            self._candidate_count = 1
        if self._candidate_count >= DIRECTION_CONFIRM_SAMPLES:
            self._direction = sign
            self._anchor = position
            self._candidate = 0
            self._candidate_count = 0

    def _settle(
        self,
        state: PlatformState,
        position: Optional[float],
        edge_step: Optional[float],
        interval: float,
        height: Optional[float],
    ) -> PlatformReport:
        """Commit ``state``, log a transition, and build the report."""
        if state is PlatformState.UNKNOWN:
            self._direction = 0
            self._anchor = None
            self._candidate = 0
            self._candidate_count = 0

        if state is not self._state:
            self._state = state
            self._since_transition = 0.0
            logger.info(
                "[PLATFORM state=%s s=%s s_star=%s h=%s dir=%+d]",
                state.name,
                "na" if position is None else f"{position:.2f}",
                "na" if edge_step is None else f"{edge_step:.2f}",
                "na" if height is None else f"{height:.2f}",
                self._direction,
            )
        elif self._since_transition is not None:
            self._since_transition += interval

        self._report = PlatformReport(
            state=state,
            guard_active=state in GUARDED_STATES,
            along_track_m=position,
            edge_step_m=edge_step,
            direction=self._direction,
            seconds_since_transition=self._since_transition,
        )
        return self._report
