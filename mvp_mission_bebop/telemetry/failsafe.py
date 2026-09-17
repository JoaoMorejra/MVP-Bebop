"""Continuous safety supervisor and kinematic invariant enforcement.

Monitors sensor telemetry heartbeat, enforces altitude safety ceiling,
validates kinematic constraints, and coordinates controlled emergency landings.
"""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final, Iterator, Optional, Tuple

from mvp_mission_bebop.exceptions import KinematicConstraintViolation
from mvp_mission_bebop.parameters import FlightKinematicsConfig, TimeoutsConfig
from mvp_mission_bebop.telemetry.odometry import OdometrySupervisor, TelemetryHealth

if TYPE_CHECKING:  # pragma: no cover - avoids a circular import at runtime
    from mvp_mission_bebop.actuators.proxy import BenchtopDroneProxy

logger = logging.getLogger("FailsafeSupervisor")

#: Numerical slack below which a command counts as exactly zero.
KINEMATIC_TOLERANCE: Final[float] = 1e-4


class FailsafeSupervisor:
    """Supervisory observer validating multi-sensor integrity and flight envelope invariants."""

    def __init__(
        self,
        drone_actuator: "BenchtopDroneProxy",
        odom_supervisor: OdometrySupervisor,
        timeouts_cfg: TimeoutsConfig,
        kinematics_cfg: Optional[FlightKinematicsConfig] = None,
    ) -> None:
        self.actuator = drone_actuator
        self.odom_supervisor = odom_supervisor
        self.timeouts_cfg = timeouts_cfg
        self.kinematics_cfg = kinematics_cfg or FlightKinematicsConfig()
        self.last_valid_frame_timestamp: float = time.monotonic()
        self.failsafe_active: bool = False

        # Climb authority is a window, not a flag a caller can simply assert.
        # Stage 1 opens it for the duration of its ascent and it closes itself;
        # outside that window a positive ``vz`` is unrepresentable no matter what
        # the caller asks for. See :meth:`climb_window`.
        self._climb_authorized: bool = False
        self._max_climb_speed: float = 0.0

        # Altitude-hold authority is a second, much smaller window, granted for
        # a different reason and on different terms. The climb window exists so
        # Stage 1 can fly a manoeuvre; this one exists so the altitude *loop*
        # can trim a sink it did not ask for. It is opened by every stage that
        # translates, it is bounded an order of magnitude tighter, and -- unlike
        # the climb window -- it needs no opt-in from the caller of
        # ``clamp_kinematics``, because its whole point is that the ordinary
        # command path can use it without any step having to know it exists.
        self._altitude_hold_ceiling: float = 0.0

    def notify_frame_received(self) -> None:
        """Register fresh video frame arrival timestamp.

        Monotonic, for the reason given in ``OdometrySupervisor.telemetry_health``:
        a wall-clock heartbeat against a short timeout turns a clock correction
        into a spurious emergency landing.

        Call this only with a frame in hand. Stage 1 used to stamp it twice with
        no frame at all -- unconditionally at the end of the stabilize loop and
        again on entry to ``_finalize`` -- which forged a heartbeat for a camera
        that may have been dead the whole time and carried the mission into
        Stage 2 blind.
        """
        self.last_valid_frame_timestamp = time.monotonic()

    @property
    def climb_authorized(self) -> bool:
        """True only while a Stage 1 ascent window is open."""
        return self._climb_authorized

    @contextmanager
    def climb_window(self, max_climb_speed: float) -> Iterator[None]:
        """Authorize bounded positive ``vz`` for the duration of the block.

        The mission's vertical invariant is that the drone never climbs. Stage 1
        needs one bounded exception to it, because the Bebop firmware ends its
        launch profile at its own hover height and the gap to the configured
        target can only be closed by commanding ascent.

        Making that exception a *window* rather than a parameter is deliberate.
        A parameter is a claim the caller makes about itself, and every step has
        equal access to the supervisor; a window is a fact the supervisor owns.
        Stage 2-5 code that passed ``allow_climb=True`` -- by mistake, or because
        it was copied from here -- still gets its climb suppressed, because no
        window is open. The block is exception-safe: authority is revoked in a
        ``finally``, so a fault mid-ascent cannot leave the invariant relaxed.

        Parameters
        ----------
        max_climb_speed : float
            Ceiling on the ascent rate authorized inside the block. Non-positive
            values open no authority at all.

        Raises
        ------
        RuntimeError
            If a window is already open. Nesting would make the revocation on
            exit ambiguous, and nothing in the mission needs it.
        """
        if self._climb_authorized:
            raise RuntimeError("A climb window is already open; nesting is not supported.")

        limit = max(0.0, max_climb_speed)
        self._climb_authorized = limit > 0.0
        self._max_climb_speed = limit
        if self._climb_authorized:
            logger.info("Climb authority granted, bounded at %.3f m/s.", limit)
        try:
            yield
        finally:
            self._climb_authorized = False
            self._max_climb_speed = 0.0
            logger.info("Climb authority revoked; vertical authority is descent-only again.")

    @property
    def altitude_hold_ceiling(self) -> float:
        """Ascent currently authorized for altitude hold, in normalized units."""
        return self._altitude_hold_ceiling

    @contextmanager
    def altitude_hold_window(self, max_climb_speed: float) -> Iterator[None]:
        """Authorize bounded corrective ascent for the duration of the block.

        Every phase that translates opens one of these. Inside it the altitude
        governor may ask for a small positive ``vz`` to cancel the lift loss that
        translation causes; outside it the vertical axis is descent-only exactly
        as it always was, and a zero or negative ceiling opens no authority at
        all -- which is what ``governor.hold_enabled = false`` produces, so the
        switch needs no second check anywhere else.

        Two things distinguish this from :meth:`climb_window` and both are
        deliberate. It nests, saving and restoring the previous ceiling, because
        the stages that open it are not a single linear scope -- a hold inside a
        navigating loop is an ordinary arrangement and making it an error would
        buy nothing. And it does not require ``allow_climb`` from the caller,
        because the authority is for the *governor's* command on the ordinary
        path rather than for a manoeuvre a step has decided to fly; requiring an
        opt-in would mean threading a flag through every call site and would
        make the flag, rather than the window, the thing that grants ascent.

        Parameters
        ----------
        max_climb_speed : float
            Ceiling on corrective ascent inside the block, normalized. Values at
            or below zero authorize nothing.
        """
        previous = self._altitude_hold_ceiling
        ceiling = max(0.0, max_climb_speed)
        self._altitude_hold_ceiling = ceiling
        if ceiling > 0.0 and previous <= 0.0:
            logger.info(
                "Altitude hold authority granted, bounded at %.3f normalized ascent.", ceiling
            )
        try:
            yield
        finally:
            self._altitude_hold_ceiling = previous
            if ceiling > 0.0 and previous <= 0.0:
                logger.info("Altitude hold authority revoked; vertical authority is descent-only.")

    def clamp_kinematics(
        self, vz: float, vyaw: float, *, allow_climb: bool = False
    ) -> Tuple[float, float]:
        """Saturate a command onto the kinematic invariants.

        This is the guard for the normal command path. Its counterpart
        :meth:`assert_kinematics` raises, which is correct for a contract
        violation but wrong as a flight-time guard: a single ``vz`` of 1e-3
        arriving from a rounding error used to propagate out of the step, through
        the pipeline's generic exception handler, and into an emergency landing.
        A safety supervisor that ends the mission over a sign error is a
        liability rather than a protection.

        Ascent comes from one of two windows and from nowhere else. The Stage 1
        climb window (:meth:`climb_window`) additionally requires the caller to
        ask, through ``allow_climb``, so that neither an unauthorized caller nor
        a stale window can relax the invariant alone. The altitude-hold window
        (:meth:`altitude_hold_window`) does not, because it exists for the
        governor's command on the ordinary path; it is bounded an order of
        magnitude tighter, and outside it -- which is the state of this
        supervisor unless a translating stage has opened one -- ``vz`` is
        saturated non-positive exactly as it always was.

        ``vyaw`` is forced to zero unconditionally in every phase: the yaw
        invariant protects optical-flow fidelity and has no exception.

        Parameters
        ----------
        vz : float
            Requested vertical velocity. Positive is ascent.
        vyaw : float
            Requested yaw rate. Always suppressed.
        allow_climb : bool
            Caller's opt-in to ascent. Default ``False``, so every existing call
            site keeps the strict descent-only behaviour without modification.

        Returns
        -------
        Tuple[float, float]
            ``(vz, vyaw)``. ``vyaw`` is always ``0.0``. ``vz`` is saturated to
            ``<= 0`` unless a window is open, in which case it is saturated to
            that window's ascent ceiling.
        """
        if allow_climb and self._climb_authorized:
            ceiling = self._max_climb_speed
            authority = "the Stage 1 climb window"
        else:
            ceiling = self._altitude_hold_ceiling
            authority = "altitude hold" if ceiling > 0.0 else ""

        safe_vz = min(vz, ceiling)
        if vz > ceiling + KINEMATIC_TOLERANCE:
            if authority:
                logger.warning(
                    "Climb command vz=%.4f exceeds the %.4f authorized by %s; saturating.",
                    vz,
                    ceiling,
                    authority,
                )
            else:
                logger.warning(
                    "Climb command vz=%.4f suppressed; vertical authority is descent-only.", vz
                )

        if abs(vyaw) > KINEMATIC_TOLERANCE:
            logger.warning(
                "Yaw command vyaw=%.4f suppressed; rotation is prohibited in flight.", vyaw
            )
        return safe_vz, 0.0

    def clamp_translation(self, vx: float, vy: float) -> Tuple[float, float]:
        """Saturate the horizontal command onto the flight envelope.

        The counterpart to :meth:`clamp_kinematics`, and it was missing. That
        method saturated ``vz`` and ``vyaw`` and returned only those two, so the
        horizontal pair travelled from the guidance law to
        ``BebopDrone.move_velocity`` with no bound of any kind between them. The
        only remaining guard was the driver's own ``max(-1.0, min(1.0, v))``,
        which is to say: full throttle. Every jerk limit, braking profile and
        speed cap upstream is advisory unless something enforces an envelope at
        the boundary, because all of them live inside the guidance law that a
        defect would be in.

        Non-finite components are rejected to zero rather than saturated. NaN
        compares False against everything, so ``min``/``max`` would silently
        resolve it to whichever bound happened to be the first operand -- the
        clamp would convert a corrupt sample into full authority rather than
        containing it.
        """
        limit = abs(self.kinematics_cfg.max_horizontal_speed)
        safe = []
        for axis, value in (("vx", vx), ("vy", vy)):
            if not math.isfinite(value):
                logger.warning("Non-finite %s=%r suppressed to zero.", axis, value)
                safe.append(0.0)
                continue
            if abs(value) > limit + KINEMATIC_TOLERANCE:
                logger.warning(
                    "Horizontal command %s=%.4f exceeds the %.4f envelope; saturating.",
                    axis,
                    value,
                    limit,
                )
            safe.append(max(-limit, min(limit, value)))
        return safe[0], safe[1]

    def assert_kinematics(self, vz: float, vyaw: float) -> None:
        """Assert the kinematic safety invariants, raising on violation.

        Reserved for checkpoints and tests. Use :meth:`clamp_kinematics` on the
        command path.

        1. Absolute zero yaw rate (vyaw == 0.0).
        2. Non-positive vertical velocity (vz <= 0.0).
        """
        if abs(vyaw) > KINEMATIC_TOLERANCE:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vyaw={vyaw:.4f}. "
                "Yaw rotation is strictly prohibited during flight."
            )
        if vz > KINEMATIC_TOLERANCE:
            raise KinematicConstraintViolation(
                f"Kinematic constraint violation: vz={vz:.4f} > 0. "
                "Positive vertical climbing commands are strictly prohibited."
            )

    def evaluate_system_health(self) -> Tuple[bool, str]:
        """Verify telemetry health, altitude ceiling, and optical stream continuity."""
        if self.failsafe_active:
            return False, "Failsafe already active."

        health = self.odom_supervisor.telemetry_health()
        if health is TelemetryHealth.NEVER_RECEIVED:
            return False, "No odometry has ever been received. Verify /bebop/odom is publishing."
        if health is TelemetryHealth.STALE:
            return False, "Odometry telemetry stream loss (heartbeat timeout)."

        if self.odom_supervisor.is_ceiling_breached():
            rel_alt = self.odom_supervisor.relative_altitude
            ceiling = self.odom_supervisor.altitude_ceiling
            return False, f"Altitude ceiling breached: {rel_alt:.2f} m > {ceiling:.2f} m."

        frame_age = time.monotonic() - self.last_valid_frame_timestamp
        timeout = self.timeouts_cfg.video_stream_timeout_sec
        if frame_age > timeout:
            return False, f"Camera stream loss: frame age {frame_age:.1f} s > {timeout:.1f} s."

        return True, "Nominal"

    def trigger_emergency_land(self, reason: str) -> None:
        """Execute immediate controlled safe landing. Never cuts motors abruptly."""
        self.failsafe_active = True
        logger.critical("FAILSAFE ENGAGED: %s. Halting actuators and commanding landing.", reason)

        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync
            announce_sync(
                "Falha de segurança",
                details={"erro": reason},
                priority="CRITICAL",
                wait=False,
            )
        except Exception as vocal_err:
            logger.debug("Acoustic alert dispatch failure: %s", vocal_err)

        try:
            self.actuator.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            self.actuator.land()
        except Exception as exc:
            logger.critical("Secondary exception during emergency landing execution: %s", exc)
