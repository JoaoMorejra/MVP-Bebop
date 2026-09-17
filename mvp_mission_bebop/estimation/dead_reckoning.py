"""Aggregated displacement from the mission's own ``move_velocity`` history.

The Bebop tells the mission almost nothing about where it is. ``/bebop/odom`` is
the only state feedback the airframe offers, it comes from an optical-flow
estimator, and this mission spends its whole profile looking at featureless
tarmac from 1.55 m at tilt angles the estimator was never characterised for.
When it drifts, it drifts silently: nothing on this platform cross-checks it,
and the return leg that consumes it has no way to notice it is flying home to
the wrong place.

There is, however, one thing the mission knows exactly -- what it commanded. The
sequence of ``move_velocity`` calls and the interval each was held for is owned
by this process, cannot drift, and is only ever as wrong as the speed
calibration behind it, which is a quantity that can be measured on the ground.
Integrating it produces a track from the launch origin that is independent of
the optical flow estimate and of whether ``/bebop/odom`` is publishing at all.

Three properties of the airframe shape the integration and none of them are
optional:

*The Bebop latches.* ``move_velocity`` publishes a Twist and the firmware holds
it until another arrives; there is no timeout and no implicit zero. So a command
displaces the drone over the interval until the *next* command, not over some
nominal control period, and that is what is integrated here.

*The driver quantizes.* Components are transmitted as ``int8(v * 100)``, so
anything under 0.01 normalized is an exact zero on the wire. Crediting the
airframe with travel under those commands would bias every estimate outward, and
the sigma-delta shaper emits sub-threshold demand deliberately and often. The
dead zone lives in :class:`~mvp_mission_bebop.estimation.calibration.SpeedCalibration`
and is applied on every segment.

*Normalized is not metres per second.* The conversion is the calibration's job,
and until it is measured in flight the absolute scale of this track is nominal
-- correctly *shaped*, but no more accurate than the gain it was built on.

The class is free of ROS, of the Nectar SDK, and of any wall-clock read it does
not take through an injectable clock, so a whole mission's worth of motion can be
replayed deterministically in a test.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from mvp_mission_bebop.estimation.calibration import SpeedCalibration
from mvp_mission_bebop.parameters import DeadReckoningConfig

logger = logging.getLogger("DeadReckoning")

Clock = Callable[[], float]


@dataclass(frozen=True)
class MotionSegment:
    """One held command and the displacement it is credited with.

    Retained so a flight can be explained after the fact: a return leg that
    ended in the wrong place is a question about which segment was wrong, and
    without the log there is nothing to answer it with.
    """

    #: The normalized command that was held.
    vx: float
    vy: float
    vz: float
    vyaw: float
    #: How long it was held, in seconds, after clamping.
    duration_sec: float
    #: World-frame displacement credited to it, in metres, relative to the
    #: heading the drone held at launch.
    dx_m: float
    dy_m: float
    #: Heading at the start of the segment, in radians.
    heading_rad: float

    @property
    def distance_m(self) -> float:
        """Planar length of this segment, in metres."""
        return math.hypot(self.dx_m, self.dy_m)


@dataclass(frozen=True)
class DeadReckonedState:
    """Immutable view of the aggregated track."""

    #: Displacement from the launch origin in the launch-heading frame: ``x``
    #: forward along the heading held at takeoff, ``y`` to its left.
    x_m: float
    y_m: float
    #: Heading relative to launch, radians. Zero for the whole of a mission that
    #: honours the ``vyaw == 0`` invariant.
    heading_rad: float
    #: Total time the tracker has been armed, in seconds.
    elapsed_sec: float
    #: Path length travelled, as opposed to displacement from the origin.
    path_length_m: float
    segment_count: int
    armed: bool

    @property
    def displacement(self) -> Tuple[float, float]:
        """``(x, y)`` from the launch origin, in metres."""
        return self.x_m, self.y_m

    @property
    def distance_m(self) -> float:
        """Straight-line range back to the launch origin, in metres.

        The hypotenuse of the aggregated vector: fly this far along its reverse
        bearing and the drone is back where it started.
        """
        return math.hypot(self.x_m, self.y_m)

    def body_frame_origin_error(self) -> Tuple[float, float, float]:
        """Vector from the estimated position to the origin, in body FLU.

        Deliberately the same shape and sign convention as
        :meth:`~mvp_mission_bebop.telemetry.odometry.OdometrySnapshot.body_frame_launch_error`
        -- ``ex`` positive when the origin lies ahead of the nose, ``ey``
        positive when it lies to the left -- so the return guidance law consumes
        either source without knowing which it was handed.
        """
        dx, dy = -self.x_m, -self.y_m
        cos_psi = math.cos(self.heading_rad)
        sin_psi = math.sin(self.heading_rad)
        ex_body = cos_psi * dx + sin_psi * dy
        ey_body = -sin_psi * dx + cos_psi * dy
        return ex_body, ey_body, math.hypot(dx, dy)


class DeadReckoningTracker:
    """Accumulates displacement from the sequence of commands actually sent.

    Wired in at the actuator boundary -- :class:`
    ~mvp_mission_bebop.actuators.proxy.BenchtopDroneProxy` hands it every
    command -- so no mission step has to remember to report its own motion.
    A step that forgets is exactly how a dead-reckoned track silently loses a
    leg, and the proxy is the one place every leg has to pass through.

    Thread safety matters and is not incidental: commands arrive on the mission
    thread while :meth:`advance` may be called from a telemetry timer, and both
    mutate the accumulator.
    """

    __slots__ = (
        "_calibration",
        "_config",
        "_clock",
        "_lock",
        "_x",
        "_y",
        "_heading",
        "_vx",
        "_vy",
        "_vz",
        "_vyaw",
        "_expiry",
        "_last_update",
        "_armed",
        "_elapsed",
        "_path_length",
        "_segments",
        "_segment_count",
    )

    def __init__(
        self,
        calibration: SpeedCalibration,
        config: Optional[DeadReckoningConfig] = None,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._calibration = calibration
        self._config = config or DeadReckoningConfig()
        self._clock = clock
        self._lock = threading.RLock()

        self._x: float = 0.0
        self._y: float = 0.0
        self._heading: float = 0.0
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._vz: float = 0.0
        self._vyaw: float = 0.0
        self._expiry: Optional[float] = None
        self._last_update: float = clock()
        self._armed: bool = False
        self._elapsed: float = 0.0
        self._path_length: float = 0.0
        self._segments: List[MotionSegment] = []
        self._segment_count: int = 0

    # ------------------------------------------------------------- properties

    @property
    def armed(self) -> bool:
        """True while commands are being aggregated."""
        return self._armed

    @property
    def calibration(self) -> SpeedCalibration:
        """The command-to-displacement map in force."""
        return self._calibration

    @property
    def segments(self) -> List[MotionSegment]:
        """Copy of the retained segment log, oldest first."""
        with self._lock:
            return list(self._segments)

    # ------------------------------------------------------------------ arming

    def arm(self) -> None:
        """Zero the accumulator and begin aggregating from here.

        Called once the airborne origin has been frozen, which is the only
        moment ``(0, 0)`` means anything: measured on the ground it would bake
        the lift-off transient into the coordinate the drone spends the rest of
        the mission trying to return to, and measured before the climb it would
        anchor to an altitude the mission has since left. The horizontal origin
        and this origin are therefore frozen together, by construction.
        """
        with self._lock:
            self._x = self._y = self._heading = 0.0
            self._vx = self._vy = self._vz = self._vyaw = 0.0
            self._expiry = None
            self._last_update = self._clock()
            self._elapsed = 0.0
            self._path_length = 0.0
            self._segments.clear()
            self._segment_count = 0
            self._armed = True
        logger.info(
            "Dead reckoning armed at the launch origin. Gain %.3f m/s per unit command, "
            "dead zone %.3f, efficiency %.2f.",
            self._calibration.normalized_to_mps,
            self._calibration.command_deadzone,
            self._calibration.translation_efficiency,
        )

    def disarm(self) -> None:
        """Stop aggregating, retaining the track accumulated so far."""
        self.advance()
        with self._lock:
            self._armed = False

    def reset(self) -> None:
        """Discard the whole track and disarm."""
        with self._lock:
            self._x = self._y = self._heading = 0.0
            self._vx = self._vy = self._vz = self._vyaw = 0.0
            self._expiry = None
            self._last_update = self._clock()
            self._elapsed = 0.0
            self._path_length = 0.0
            self._segments.clear()
            self._segment_count = 0
            self._armed = False

    # ------------------------------------------------------------ integration

    def command(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        vyaw: float = 0.0,
        duration: Optional[float] = None,
    ) -> None:
        """Close out the held command and latch a new one.

        The ordering is the substance of the method. The airframe was flying the
        *previous* command right up to the instant this one is transmitted, so
        that interval is credited first and only then is the new command
        latched. Reversing the two attributes each interval to the wrong
        velocity, which over a mission of several hundred commands is not a
        rounding error but a systematic lag of one control period in every leg.

        Parameters
        ----------
        duration : Optional[float]
            The SDK's optional hold time. When given, the command is treated as
            expiring after it: ``BebopDrone.move_velocity`` zeroes the Twist when
            the duration elapses, so integrating it forward indefinitely would
            credit travel the airframe did not make.
        """
        now = self._clock()
        self._integrate_to(now)
        with self._lock:
            self._vx, self._vy, self._vz, self._vyaw = vx, vy, vz, vyaw
            self._expiry = None if duration is None else now + max(0.0, duration)

    def advance(self, now: Optional[float] = None) -> None:
        """Credit the held command up to now without changing it.

        Call this before reading the track. Without it the interval since the
        last command -- which for a stage that transmits at 15 Hz is one control
        period, and for one blocked on a frame grab can be far more -- is missing
        from the estimate.
        """
        self._integrate_to(self._clock() if now is None else now)

    def _integrate_to(self, now: float) -> None:
        """Advance the accumulator to ``now``. Takes the lock."""
        with self._lock:
            if not self._armed:
                self._last_update = now
                return

            interval = now - self._last_update
            self._last_update = now
            if interval <= 0.0:
                return

            # A mission thread descheduled behind a blocking frame grab or a
            # model load must not credit the airframe with several seconds of
            # travel at the last commanded speed. The clamp loses real motion
            # when it bites, which is the right trade: under-reading the
            # displacement makes the return leg stop short, and over-reading it
            # makes the drone fly past the origin and keep going.
            step = min(interval, self._config.max_integration_step_sec)
            self._elapsed += step

            expiry = self._expiry
            if expiry is not None:
                remaining = expiry - (now - step)
                if remaining <= 0.0:
                    # The latched command lapsed before this interval began.
                    self._vx = self._vy = self._vz = self._vyaw = 0.0
                    self._expiry = None
                    return
                step = min(step, remaining)

            vx, vy, vyaw = self._vx, self._vy, self._vyaw
            heading = self._heading

            forward_m, left_m = self._calibration.displacement(vx, vy, step)
            if forward_m == 0.0 and left_m == 0.0 and vyaw == 0.0:
                return

            # Body FLU rotated into the launch-heading frame. Evaluated at the
            # heading held when the segment began, which for this mission is
            # always zero: ``vyaw`` is pinned by the failsafe and nothing here
            # relaxes that. The rotation is carried anyway so that a violation of
            # the yaw invariant shows up as a curved track rather than being
            # quietly discarded.
            cos_psi = math.cos(heading)
            sin_psi = math.sin(heading)
            dx = forward_m * cos_psi - left_m * sin_psi
            dy = forward_m * sin_psi + left_m * cos_psi

            self._x += dx
            self._y += dy
            self._path_length += math.hypot(dx, dy)
            self._heading += vyaw * self._config.yaw_rate_per_unit_rad_s * step
            self._segment_count += 1

            if self._config.record_segments:
                self._segments.append(
                    MotionSegment(
                        vx=vx,
                        vy=vy,
                        vz=self._vz,
                        vyaw=vyaw,
                        duration_sec=step,
                        dx_m=dx,
                        dy_m=dy,
                        heading_rad=heading,
                    )
                )
                overflow = len(self._segments) - self._config.max_segments
                if overflow > 0:
                    del self._segments[:overflow]

    # ----------------------------------------------------------------- reading

    def state(self, *, advance: bool = True) -> DeadReckonedState:
        """Current aggregated track.

        Parameters
        ----------
        advance : bool
            Credit the held command up to now before reading. The default is
            what any caller acting on the result wants; ``False`` exists for
            diagnostics that must not perturb the integration.
        """
        if advance:
            self.advance()
        with self._lock:
            return DeadReckonedState(
                x_m=self._x,
                y_m=self._y,
                heading_rad=self._heading,
                elapsed_sec=self._elapsed,
                path_length_m=self._path_length,
                segment_count=self._segment_count,
                armed=self._armed,
            )

    @property
    def displacement(self) -> Tuple[float, float]:
        """``(x, y)`` from the launch origin, in metres."""
        return self.state().displacement

    @property
    def distance_m(self) -> float:
        """Straight-line range back to the launch origin, in metres."""
        return self.state().distance_m

    def body_frame_origin_error(self) -> Tuple[float, float, float]:
        """``(ex, ey, distance)`` toward the origin, in body FLU."""
        return self.state().body_frame_origin_error()

    def summary(self) -> str:
        """One-line description of the aggregated track, for the flight log."""
        state = self.state()
        if not state.armed and state.segment_count == 0:
            return "dead reckoning: no motion aggregated"
        bearing = math.degrees(math.atan2(state.y_m, state.x_m))
        return (
            f"dead reckoning: displacement ({state.x_m:+.2f}, {state.y_m:+.2f}) m from launch, "
            f"range {state.distance_m:.2f} m on a {bearing:+.0f} deg bearing, "
            f"path length {state.path_length_m:.2f} m over {state.elapsed_sec:.1f} s "
            f"and {state.segment_count} commands"
        )
