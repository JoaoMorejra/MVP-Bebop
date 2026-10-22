"""Stage 1: IMU calibration, ground reference, takeoff, and hover stabilization."""

from __future__ import annotations

import contextlib
import logging
import math
import time
from typing import Any, ContextManager, Dict, Final, FrozenSet, Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.engine.launch import CountdownTicker
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.launch_envelope import launches_from_ground
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import BaseStep, StepStatus, governed_vz
from mvp_mission_bebop.telemetry.milestones import emit_milestone

logger = logging.getLogger("Step1Takeoff")

#: Seconds before liftoff at which the takeoff clearance call is made.
_CLEARANCE_ANNOUNCE_SEC: float = 3.2

#: Settling time after flat trim, in seconds. The IMU needs a moment on a level
#: surface before the calibration it just performed is trustworthy. Not a
#: blocking wait any more: the ground reference and the detector warmup run
#: inside it, and the takeoff waits only for what is left of it at the zero.
_FLAT_TRIM_SETTLE_SEC: float = 2.0

#: Margin above ``takeoff_settle_min_altitude_m`` within which a target is
#: treated as low for the stabilization floor, metres.
_LOW_TARGET_MARGIN_M: Final[float] = 0.05

#: Poll period of the remaining IMU settle before the takeoff, seconds.
_SETTLE_POLL_SEC: Final[float] = 0.01

#: Time the altitude must stay inside ``governor.deadband_m`` of the target for
#: the post-takeoff descent to count as converged, in seconds. About five
#: odometry samples at the driver's 5 Hz.
_DESCENT_SETTLE_WINDOW_SEC: Final[float] = 1.0


class _NoPerception:
    """Stand-in for a context without a perception pipeline."""

    engaged = False

    def release_nowait(self) -> None:
        return None


#: ARSDK flying states that confirm the airframe has left the ground: takingoff
#: (1) and hovering (2). ``usertakeoff`` (6) waits on the ground.
_TAKEOFF_CONFIRMING_STATES: Final[FrozenSet[int]] = frozenset({1, 2})


def _ceiling_transition(ctx: MissionContext, name: str) -> None:
    """Call a takeoff-transient transition of the odometry supervisor, when it has one.

    Duck-typed: the stage-level test doubles of the supervisor predate the
    ceiling schedule and have nothing to transition.
    """
    transition = getattr(ctx.odom_supervisor, name, None)
    if callable(transition):
        transition()


def takeoff_confirmed(flying_state: Optional[int], relative_altitude: float, min_altitude: float) -> bool:
    """Whether the takeoff actually happened.

    ``BebopDrone.takeoff`` publishes an ``Empty`` and returns True after a fixed
    three-second sleep whatever the firmware did (``nectar/control/bebop/drone.py``),
    so its return value cannot be the evidence. Either the aircraft reports a
    flying state in :data:`_TAKEOFF_CONFIRMING_STATES`, or odometry places it
    above ``min_altitude`` (``takeoff_settle_min_altitude_m``); on the bench the
    simulator supplies that odometry.

    Parameters
    ----------
    flying_state : int or None
        Last ``/bebop/states/flying_state``, or None when none has arrived.
    relative_altitude : float
        Altitude above the calibrated ground reference, metres.
    min_altitude : float
        Threshold above which the airframe is off the ground, metres.
    """
    if flying_state in _TAKEOFF_CONFIRMING_STATES:
        return True
    return relative_altitude > min_altitude


class TakeoffStep(BaseStep):
    """Calibrates, launches, and establishes a drift-free airborne origin."""

    def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")
        self._mode = mode
        self._takeoff_called = False
        #: Monotonic instant the IMU settle after the flat trim ends; ``None``
        #: before a flat trim has been requested.
        self._imu_settled_at: Optional[float] = None
        #: Countdown deadline of a launch without a pinned one, anchored at the
        #: Stage 1 entry, and the ticker counting towards it.
        self._cold_deadline: Optional[float] = None
        self._cold_ticker: Optional[CountdownTicker] = None
        #: Monotonic instant of the last takeoff command.
        self._t_takeoff_cmd: float = 0.0

    @property
    def mode(self) -> MissionMode:
        return self._mode

    def _call_takeoff_once_confirmed(self, ctx: MissionContext, relative_altitude: float) -> None:
        """Raise ``mission.takeoff`` the first time the takeoff is confirmed.

        The altitude in the payload is the configured target (D2), not the
        measured height: only the moment of the call depends on the flight.
        """
        if self._takeoff_called:
            return
        kinematics = ctx.params.kinematics
        if not takeoff_confirmed(
            getattr(ctx, "flying_state", None), relative_altitude, kinematics.takeoff_settle_min_altitude_m
        ):
            return
        self._takeoff_called = True
        payload: Dict[str, Any] = {"altitude_m": round(kinematics.target_altitude_m, 2)}
        bb = getattr(ctx, "blackboard", None)
        if bb is not None:
            for attr in ("t_click", "t_takeoff_cmd", "t_takeoff_started", "t_takeoff_confirmed"):
                val = getattr(bb, attr, None)
                if val is not None:
                    payload[attr] = round(val, 3) if isinstance(val, float) else val
        payload["takeoff_surface"] = "ground" if launches_from_ground(ctx.params) else "platform"
        payload["target_altitude_m"] = round(float(kinematics.target_altitude_m), 2)
        emit_milestone("mission.takeoff", payload)

    def execute(self, ctx: MissionContext) -> StepStatus:
        """Run Stage 1: the ground sequence inside the countdown, then the flight.

        The countdown is the warmup window. The perception session opens at the
        go, so the detector warms up while the flat trim and the ground
        reference run; a launch without a pinned deadline counts from here, not
        after them; and at the zero the detector is released and the takeoff
        commanded at once, the session closing (bounded) only after it.
        """
        logger.info("--- [%s] ---", self.name)
        self._takeoff_called = False
        self._imu_settled_at = None
        started = time.monotonic()
        self._arm_cold_countdown(ctx, started)

        with self._perception_session(ctx) as perception:
            status = self._ground_sequence(ctx)
            if status is not StepStatus.SUCCESS:
                # The countdown ticks must not keep running towards a takeoff
                # that is not going to happen.
                self._stop_ticker(ctx)
                return status
            bb = getattr(ctx, "blackboard", None)
            if bb is not None:
                bb.takeoff_committed = True
            # The takeoff does not need the camera; the confirmation after it
            # does. Releasing first keeps a hung inference out of the gap
            # between the zero and the command.
            perception.release_nowait()
            status = self._command_takeoff(ctx)
        if status is not StepStatus.SUCCESS:
            return status
        return self._flight_sequence(ctx)

    def _stop_ticker(self, ctx: MissionContext) -> None:
        ticker = getattr(ctx, "countdown_ticker", None)
        if ticker is not None:
            ticker.stop()
        if self._cold_ticker is not None:
            self._cold_ticker.stop()

    def _arm_cold_countdown(self, ctx: MissionContext, started: float) -> None:
        """Anchor a countdown with no pinned deadline at the Stage 1 entry.

        A launch from standby carries ``launch_deadline`` and its own ticker.
        Without one the countdown used to start after the flat trim and the
        ground reference, so the operator waited for both and then for the
        whole countdown. It now runs from the go, in full, and the ground
        sequence happens inside it.
        """
        self._cold_deadline = None
        self._cold_ticker = None
        countdown = ctx.params.kinematics.countdown_sec
        if getattr(ctx, "launch_deadline", None) is not None or not countdown > 0.0:
            return
        self._cold_deadline = started + float(countdown)
        self._cold_ticker = CountdownTicker(
            self._cold_deadline,
            lambda whole: emit_milestone("mission.countdown", {"remaining_sec": whole}),
        )
        self._cold_ticker.start()

    @staticmethod
    def _perception_session(ctx: MissionContext) -> ContextManager[Any]:
        perception = getattr(ctx, "perception", None)
        if perception is None:
            return contextlib.nullcontext(_NoPerception())
        return perception.session(imgsz=ctx.params.vision.inference_imgsz)

    def _ground_sequence(self, ctx: MissionContext) -> StepStatus:
        """Pre-arm checks, gimbal, flat trim, ground reference and the countdown."""
        if not self._check_prearm_battery(ctx):
            return StepStatus.FAILURE

        ctx.current_tilt_deg = ctx.params.gimbal.search_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        started = time.monotonic()
        if not self._flat_trim(ctx):
            return StepStatus.FAILURE

        if not self._calibrate(ctx):
            return StepStatus.FAILURE
        logger.info("[TIMING] ground_sequence_sec=%.3f", time.monotonic() - started)

        return self._countdown(ctx)

    def _flight_sequence(self, ctx: MissionContext) -> StepStatus:
        """Takeoff confirmation, stabilization, ascent and the airborne origin."""
        bb = getattr(ctx, "blackboard", None)
        status = self._confirm_takeoff(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._stabilize(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._acquire_altitude(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._finalize(ctx)
        if status is StepStatus.SUCCESS:
            if bb is not None:
                bb.takeoff_complete = True
        return status

    # --------------------------------------------------------------- flat trim

    def _flat_trim(self, ctx: MissionContext) -> bool:
        """Request the IMU flat trim and require the aircraft to confirm it.

        The count on ``states/flat_trim`` is read before the request and must
        advance within ``timeouts.flat_trim_ack_timeout_sec``. Without that, a
        real flight is refused: the trim is the attitude reference the whole
        flight is levelled on, and the SDK's request is fire-and-forget. The
        bench proceeds, since ``/bebop/flattrim`` does not cross the relay.

        Returns
        -------
        bool
            False only when a real flight must not take off.
        """
        tracker = getattr(ctx, "flat_trim_ack", None)
        mark = tracker.sequence if tracker is not None else None
        logger.info("Executing IMU flat trim on a level surface...")
        ctx.drone.flat_trim()
        timeout = ctx.params.timeouts.flat_trim_ack_timeout_sec
        acknowledged = tracker is not None and tracker.wait_after(mark, timeout)
        emit_milestone("mission.flat_trim", {"acknowledged": bool(acknowledged)})
        if acknowledged:
            logger.info("Flat trim acknowledged by the aircraft.")
        elif ctx.drone.no_fly:
            logger.warning("[NO-FLY] Flat trim not acknowledged within %.1f s; bench continues.", timeout)
        else:
            logger.critical(
                "Flat trim not acknowledged within %.1f s (FlatTrimChanged). Refusing to take off.", timeout
            )
            self._announce(
                "Nivelamento sem confirmação",
                "a aeronave não confirmou o nivelamento, decolagem cancelada",
                priority="CRITICAL",
            )
            return False
        # The settle is the airframe's IMU; on the bench there is none, and the
        # proxy's ``delay`` that used to spend it was already a no-op there.
        settle = 0.0 if ctx.drone.no_fly else _FLAT_TRIM_SETTLE_SEC
        self._imu_settled_at = time.monotonic() + settle
        return True

    # ---------------------------------------------------------------- pre-arm

    def _check_prearm_battery(self, ctx: MissionContext) -> bool:
        """Refuse to launch on a charge already at the in-flight land threshold.

        The same ``battery.land_pct`` the failsafe lands on, not a second
        limit: a takeoff at or below it would be answered by a landing on the
        first health check after liftoff. Without a fresh reading the launch
        proceeds, as the in-flight net does -- a charge the aircraft has not
        reported is not a reading to act on.
        """
        battery = getattr(ctx.failsafe, "battery_supervisor", None)
        if battery is None:
            return True
        percentage = battery.current_percentage()
        if percentage is None or percentage > battery.land_pct:
            return True

        if ctx.drone.no_fly:
            logger.warning(
                "Battery at %.0f%% is at or below the %.0f%% land threshold, but --no-fly "
                "is active, so the mission continues on the bench.",
                percentage,
                battery.land_pct,
            )
            return True

        logger.critical(
            "Battery at %.0f%% is at or below the %.0f%% land threshold. Refusing to launch: "
            "the in-flight battery net would land the aircraft immediately after liftoff.",
            percentage,
            battery.land_pct,
        )
        self._announce(
            "Falha na decolagem",
            f"bateria em {percentage:.0f} por cento, no limiar de pouso de "
            f"{battery.land_pct:.0f} por cento, decolagem cancelada",
            priority="CRITICAL",
        )
        return False

    # ------------------------------------------------------------ calibration

    def _calibrate(self, ctx: MissionContext) -> bool:
        """Establish the ground altitude reference, refusing an untrustworthy one."""
        if ctx.odom_supervisor.calibrate_ground_reference():
            z0 = ctx.odom_supervisor.snapshot().ground_reference
            if z0 is not None:
                getattr(ctx, "publish_ground_reference", lambda _z0: None)(z0)
            return True

        if ctx.drone.no_fly:
            logger.warning(
                "Ground calibration was refused, but --no-fly is active, so the mission "
                "continues on the bench with an uncalibrated altitude reference."
            )
            return True

        logger.critical(
            "Ground reference calibration failed. Refusing to launch: without a trustworthy "
            "z0 the altitude ceiling and the anti-climb governor are both meaningless."
        )
        self._announce(
            "Falha na calibração",
            "falha na calibração de referência de solo, decolagem cancelada",
            priority="CRITICAL",
        )
        return False

    # -------------------------------------------------------------- countdown

    def _countdown(self, ctx: MissionContext) -> StepStatus:
        """Run the pre-flight countdown while warming up the perception pipeline.

        The inference warmup is the point: the first YOLO call allocates and
        compiles, and paying that cost here rather than in the first search
        iteration keeps the search loop's cadence honest from its first cycle.
        """
        floor = ctx.params.kinematics.countdown_sec
        duration = floor
        # A launch from standby counts towards the click plus the countdown,
        # the instant the station's overlay counts to as well; the flat trim
        # and the ground reference ran inside that window. A launch without
        # one counts from the Stage 1 entry (:meth:`_arm_cold_countdown`).
        launch_deadline = getattr(ctx, "launch_deadline", None)
        ticker = getattr(ctx, "countdown_ticker", None)
        if launch_deadline is None and self._cold_deadline is not None:
            launch_deadline = self._cold_deadline
            ticker = self._cold_ticker
        if launch_deadline is not None:
            duration = max(0.0, launch_deadline - time.monotonic())
            # A passed deadline authorizes the takeoff only if its window was
            # counted where the operator sees it. Otherwise the time went to a
            # start-up, and the countdown is never shortened by one.
            if duration <= 0.0 and floor > 0.0 and not getattr(ticker, "shown", False):
                logger.warning(
                    "Launch deadline passed with no countdown shown: counting the full %.1f s.", floor
                )
                self._stop_ticker(ctx)
                ticker = None
                duration = floor
        if duration <= 0.0:
            self._stop_ticker(ctx)
            emit_milestone("mission.countdown_3", {"remaining_sec": 0})
            emit_milestone("mission.countdown", {"remaining_sec": 0})
            return self._await_ground_interlocks(ctx, launch_deadline)

        logger.info("Pre-flight countdown and sensor warmup (%.1f s)...", duration)
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        announced = False
        warmup_frames = 0
        seen = None
        # The station's countdown overlay and its clearance call follow these
        # milestones rather than a timer of their own: timed from the spawn,
        # they ran some 14 s ahead of this loop (imports, YOLO load, flat trim
        # and ground calibration all come first).
        last_whole: Optional[int] = None

        # Warmup runs on the perception worker, at the flight input size, and
        # publishes a detection summary rather than an annotated frame: drawn
        # on this thread it held the countdown loop to the inference rate and
        # gated the cockpit video on the annotated frame (5 FPS in Stage 1).
        # The session is the one :meth:`execute` opened at the go.
        while deadline.active:
            if ctx.interrupted():
                return StepStatus.ABORTED

            remaining = deadline.remaining_sec
            whole = int(math.ceil(max(0.0, remaining)))
            if ticker is None and whole != last_whole and whole > 0:
                last_whole = whole
                emit_milestone("mission.countdown", {"remaining_sec": whole})
            ctx.perception.set_status(f"CONTAGEM REGRESSIVA: {remaining:.1f}s | YOLO PRONTO")

            sample = ctx.perception.get_latest(max_age_sec=1.0)
            if sample is not None and sample is not seen:
                seen = sample
                warmup_frames += 1
                ctx.failsafe.notify_frame_received()

            if remaining <= _CLEARANCE_ANNOUNCE_SEC and not announced:
                announced = True
                logger.info("Takeoff synchronization window reached. Announcing clearance.")
                emit_milestone("mission.countdown_3", {"remaining_sec": round(remaining, 1)})
                self._announce("Decolagem autorizada", "decolagem autorizada, iniciando voo")

            rate.tick()

        self._stop_ticker(ctx)
        if not announced:
            emit_milestone("mission.countdown_3", {"remaining_sec": 0})
        emit_milestone("mission.countdown", {"remaining_sec": 0})
        logger.info("Perception pipeline warmed up over %d frames.", warmup_frames)
        return self._await_ground_interlocks(ctx, None)

    def _await_ground_interlocks(self, ctx: MissionContext, deadline: Optional[float]) -> StepStatus:
        """Stamp the countdown's zero and wait out what is left of the IMU settle.

        The settle after the flat trim is an interlock, not a delay to remove:
        a countdown shorter than the ground sequence waits for it, and the
        excess over the zero is logged rather than skipped.

        Parameters
        ----------
        deadline : Optional[float]
            Monotonic instant the countdown reached zero when that lies in the
            past (a passed deadline); ``None`` when the zero is now.
        """
        now = time.monotonic()
        zero = now if deadline is None else min(now, deadline)
        bb = getattr(ctx, "blackboard", None)
        if bb is not None:
            bb.t_countdown_zero = zero
        logger.info("[TIMING] t_countdown_zero mono=%.3f", zero)

        settled_at = self._imu_settled_at
        if settled_at is not None:
            while time.monotonic() < settled_at:
                if ctx.interrupted():
                    return StepStatus.ABORTED
                time.sleep(min(_SETTLE_POLL_SEC, max(0.0, settled_at - time.monotonic())))

        overrun = time.monotonic() - zero
        if overrun > _SETTLE_POLL_SEC:
            logger.warning(
                "[TIMING] ground_sequence_overrun_sec=%.3f: the countdown was exceeded by a ground "
                "interlock (flat trim settle); the takeoff waited for it.",
                overrun,
            )
        return StepStatus.SUCCESS

    # ----------------------------------------------------------------- launch

    def _launch(self, ctx: MissionContext) -> StepStatus:
        """Command the takeoff and wait for the airframe to confirm it."""
        status = self._command_takeoff(ctx)
        if status is not StepStatus.SUCCESS:
            return status
        return self._confirm_takeoff(ctx)

    def _command_takeoff(self, ctx: MissionContext) -> StepStatus:
        """Command takeoff.

        The altitude argument is accepted by the SDK and ignored by the Bebop
        firmware, which runs its own launch profile. It is passed through for
        API fidelity and used here only for logging and the ceiling.
        ``t_takeoff_cmd`` is stamped before the call: ``BebopDrone.takeoff``
        sleeps 3 s after publishing.
        """
        target_altitude = ctx.params.relative_target_altitude_m
        _ceiling_transition(ctx, "arm_ceiling_transient")
        logger.info(
            "Issuing takeoff (nominal altitude %.2f m above the launch point, ceiling %.2f m during "
            "the native profile). Note: the Bebop firmware selects its own launch altitude.",
            target_altitude,
            ctx.odom_supervisor.altitude_ceiling,
        )

        t_cmd_mono = time.monotonic()
        t_cmd_wall = time.time()
        bb = getattr(ctx, "blackboard", None)
        if bb is not None:
            bb.t_takeoff_cmd = t_cmd_mono
            bb.t_takeoff_cmd_wall = t_cmd_wall
        logger.info("[TIMING] t_takeoff_cmd mono=%.3f wall=%.3f", t_cmd_mono, t_cmd_wall)

        if not ctx.drone.takeoff(altitude=target_altitude):
            logger.critical("Autonomous takeoff rejected by the flight controller.")
            ctx.drone.move_velocity(0.0, 0.0, 0.0, 0.0)
            if bb is not None:
                bb.landing_started = True
            for _ in range(5):
                ctx.drone.land()
                time.sleep(0.05)
            self._announce(
                "Falha na decolagem",
                "decolagem rejeitada pela controladora de voo",
                priority="CRITICAL",
                details={"em_voo": False},
                wait=True,
            )
            if bb is not None:
                bb.takeoff_committed = False
            return StepStatus.FAILURE
        self._t_takeoff_cmd = t_cmd_mono
        return StepStatus.SUCCESS

    def _confirm_takeoff(self, ctx: MissionContext) -> StepStatus:
        """Require the airframe to confirm the takeoff on a real flight (R1)."""
        bb = getattr(ctx, "blackboard", None)
        no_fly = getattr(ctx.drone, "no_fly", getattr(ctx.params, "no_fly", True))
        if not no_fly:
            confirmed = self._wait_for_takeoff_confirmation(ctx, self._t_takeoff_cmd)
            if not confirmed:
                logger.critical("Takeoff confirmation timed out (R1). Landing aircraft.")
                ctx.drone.move_velocity(0.0, 0.0, 0.0, 0.0)
                if bb is not None:
                    bb.landing_started = True
                for _ in range(5):
                    ctx.drone.land()
                    time.sleep(0.05)
                self._announce(
                    "Falha na decolagem",
                    "decolagem não confirmada pela aeronave",
                    priority="CRITICAL",
                    details={"em_voo": False},
                    wait=True,
                )
                if bb is not None:
                    bb.takeoff_committed = False
                return StepStatus.FAILURE

        return StepStatus.SUCCESS

    def _wait_for_takeoff_confirmation(self, ctx: MissionContext, t_cmd_mono: float) -> bool:
        """Wait for fresh telemetry confirming the airframe left the ground (R1).

        Parameters
        ----------
        ctx : MissionContext
            Runtime context with telemetry and blackboard.
        t_cmd_mono : float
            Monotonic timestamp when the takeoff command was transmitted. Only
            samples with timestamp >= t_cmd_mono count towards confirmation.

        Returns
        -------
        bool
            True if confirmed within ``timeouts.takeoff_confirm_timeout_sec``;
            False on timeout or abort.
        """
        timeout_sec = ctx.params.timeouts.takeoff_confirm_timeout_sec
        deadline = Deadline(timeout_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        min_altitude = ctx.params.kinematics.takeoff_settle_min_altitude_m

        while deadline.active:
            if ctx.interrupted():
                return False

            if ctx.grab_frame(timeout_sec=0.1) is not None:
                ctx.failsafe.notify_frame_received()

            rate.tick()

            fs_mono = getattr(ctx, "flying_state_timestamp", 0.0)
            fresh_state = ctx.flying_state if fs_mono >= t_cmd_mono else None
            snapshot = ctx.odom_supervisor.snapshot()

            if takeoff_confirmed(fresh_state, snapshot.relative_altitude, min_altitude):
                t_confirmed = time.monotonic()
                bb = getattr(ctx, "blackboard", None)
                if bb is not None:
                    bb.t_takeoff_confirmed = t_confirmed
                logger.info(
                    "[TIMING] t_takeoff_confirmed mono=%.3f state=%s alt=%.3f",
                    t_confirmed,
                    fresh_state,
                    snapshot.relative_altitude,
                )
                self._call_takeoff_once_confirmed(ctx, snapshot.relative_altitude)
                return True

        return False

    def _stabilize(self, ctx: MissionContext) -> StepStatus:
        """Hover until the liftoff transient decays, bounded by a safety ceiling.

        This settles the airframe at whatever height the firmware's launch
        profile chose. It deliberately does *not* freeze the return origin any
        more: on a mission configured above the firmware's hover height the
        drone has not yet reached its operating altitude here, and freezing the
        origin mid-ascent would anchor the mission to a transient. That now
        happens in :meth:`_finalize`, after the climb has converged.

        The phase used to be an unconditional ``takeoff_stabilize_duration_sec``
        wait, spent in full whether or not the airframe was still moving. It now
        ends as soon as a :class:`SettlementDetector` over the vertical axis
        reports convergence, and that duration is only the ceiling. The detector
        is planar, so altitude goes in the x slot with y held at zero, exactly
        as in :meth:`_acquire_altitude`: its isotropic sigma then reduces to the standard
        deviation of the altitude.

        Two kinds of sample are kept out of the window because they look like
        convergence without being it: repeats of an odometry sample already
        seen, which a stalled stream produces with zero variance forever, and
        samples below ``takeoff_settle_min_altitude_m``, which is an airframe
        still on the ground after ``takeoff`` returned.
        """
        kinematics = ctx.params.kinematics
        ceiling_sec = kinematics.takeoff_stabilize_duration_sec
        min_altitude = self._settle_floor(
            kinematics.takeoff_settle_min_altitude_m, ctx.params.relative_target_altitude_m
        )
        settlement = SettlementDetector(
            SettlementCriteria(
                window_sec=kinematics.takeoff_settle_window_sec,
                min_samples=kinematics.takeoff_settle_min_samples,
                max_speed=kinematics.takeoff_settle_max_vz_mps,
                max_position_sigma=kinematics.takeoff_settle_max_altitude_sigma_m,
            )
        )

        logger.info(
            "Stabilizing in hover: mean |vz| <= %.3f m/s and altitude sigma <= %.3f m "
            "sustained for %.1f s above settle floor %.2f m (ceiling %.1f s)...",
            kinematics.takeoff_settle_max_vz_mps,
            kinematics.takeoff_settle_max_altitude_sigma_m,
            kinematics.takeoff_settle_window_sec,
            min_altitude,
            ceiling_sec,
        )

        deadline = Deadline(ceiling_sec)
        rate = LoopRate(kinematics.control_loop_hz)
        last_sample: Optional[int] = None
        report = None
        venue_streak = 0
        venue_limit = max(1, int(getattr(kinematics, "ceiling_breach_streak", 3)))

        while deadline.active:
            if ctx.interrupted():
                return StepStatus.ABORTED

            if ctx.grab_frame(timeout_sec=0.2) is not None:
                ctx.failsafe.notify_frame_received()
            rate.tick()

            snapshot = ctx.odom_supervisor.snapshot()
            self._call_takeoff_once_confirmed(ctx, snapshot.relative_altitude)
            if snapshot.sample_count == last_sample:
                continue
            last_sample = snapshot.sample_count

            # The firmware controls the altitude here; only the venue ceiling
            # justifies cutting its launch profile short (section 3.5).
            venue_check = getattr(ctx.odom_supervisor, "venue_ceiling_breached", None)
            venue_streak = venue_streak + 1 if venue_check is not None and venue_check(snapshot.relative_altitude) else 0
            if venue_streak >= venue_limit:
                self._halt(ctx)
                ctx.failsafe.trigger_emergency_land(
                    f"Venue ceiling breached during the launch profile at {snapshot.relative_altitude:.2f} m."
                )
                return StepStatus.FAILURE

            if snapshot.relative_altitude < min_altitude:
                settlement.reset()
                continue

            report = settlement.update(
                x=snapshot.relative_altitude,
                y=0.0,
                speed=abs(snapshot.vz),
                timestamp=snapshot.timestamp,
            )
            if report.settled:
                logger.info(
                    "Liftoff transient decayed after %.2f s of the %.1f s ceiling at %.2f m: %s",
                    deadline.elapsed_sec,
                    ceiling_sec,
                    snapshot.relative_altitude,
                    report,
                )
                _ceiling_transition(ctx, "end_native_profile")
                return StepStatus.SUCCESS

        # Not a fault: this is the full wait the phase always used to make.
        logger.warning(
            "Liftoff transient did not settle within the %.1f s ceiling (%s). Continuing.",
            ceiling_sec,
            report if report is not None else "no airborne odometry samples",
        )
        _ceiling_transition(ctx, "end_native_profile")
        return StepStatus.SUCCESS

    @staticmethod
    def _settle_floor(configured: float, target: float) -> float:
        """Altitude below which a hover sample is not counted as settled.

        A sample under the floor only proves the airframe is still on the
        ground. With the target within ``_LOW_TARGET_MARGIN_M`` of the
        configured floor, a hover at the target sits under it and the gate
        could only time out; the floor then follows half the target, still
        well clear of the ground and of the sonar's resting reading.
        """
        if target <= configured + _LOW_TARGET_MARGIN_M:
            return min(configured, 0.5 * target)
        return configured

    # ------------------------------------------------------------------ ascent

    def _acquire_altitude(self, ctx: MissionContext) -> StepStatus:
        """Close the gap between the firmware's hover height and the target, either way.

        Both profiles fly it. Above the target band (``governor.deadband_m``)
        the airframe descends under the altitude governor
        (:meth:`_descend_to_target`); below ``climb_deadband_m`` it climbs as
        described next. On the pad top the target is at or below the native
        height (D1), so only a flight from the floor can climb.

        The Bebop ends its launch profile at its own factory height, around one
        metre, and ignores the altitude the SDK was given. Every mission
        configured above that used to fly its entire profile a metre off target,
        because nothing in the pipeline ever commanded ascent -- and could not
        have, since the vertical invariant makes a positive ``vz``
        unrepresentable everywhere else.

        This is the one authorized exception, and it is narrow: a jerk- and
        acceleration-limited profile, bounded by a braking envelope that keeps
        the drone able to stop inside the arrival window, wrapped in a supervisor
        climb window that closes itself on the way out. The ceiling is checked
        every cycle rather than once at the end, so an overshoot is caught while
        there is still altitude left to give back.

        A mission whose target is at or below the achieved hover height skips
        the manoeuvre entirely, which is the default configuration.
        """
        kinematics = ctx.params.kinematics
        target = ctx.params.relative_target_altitude_m
        deadband = kinematics.climb_deadband_m
        ceiling = ctx.odom_supervisor.altitude_ceiling

        altitude = ctx.odom_supervisor.snapshot().relative_altitude
        if altitude > target + ctx.params.governor.deadband_m:
            return self._descend_to_target(ctx, target)
        if altitude >= target - deadband:
            logger.info(
                "No ascent required: holding %.2f m against a %.2f m target.", altitude, target
            )
            return StepStatus.SUCCESS

        logger.info(
            "Closing a %.2f m altitude deficit: %.2f m -> %.2f m at up to %.2f m/s (ceiling %.2f m).",
            target - altitude,
            altitude,
            target,
            kinematics.max_climb_speed_mps,
            ceiling,
        )
        self._announce("Subindo", "ajustando altitude de operação")

        profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=kinematics.max_climb_speed_mps,
                max_accel=kinematics.max_accel_mps2,
                max_jerk=kinematics.max_jerk_mps3,
            )
        )
        # Vertical settlement. The detector is planar, so the altitude goes in
        # the x slot and y is held at zero: its isotropic sigma,
        # ``sqrt(var(x) + var(y))``, then reduces exactly to the standard
        # deviation of the altitude, which is the quantity of interest.
        settlement = SettlementDetector(
            SettlementCriteria(
                window_sec=kinematics.climb_settle_window_sec,
                min_samples=kinematics.climb_settle_min_samples,
                max_speed=kinematics.climb_settle_max_speed_mps,
                max_position_sigma=kinematics.climb_settle_max_position_sigma_m,
                # Twice the deadband, because the deadband is also where the
                # feedforward *aims*. ``braking_velocity`` is given
                # ``arrival_tolerance=deadband``, so the profile plans to stop at
                # ``target - deadband`` -- exactly the lower edge of the
                # acceptance band. Requiring every sample inside one deadband
                # then made convergence turn on exact equality, and a centimetre
                # of downward sonar noise was enough to fail the whole window.
                # The climb would fly the profile correctly, never declare
                # convergence, and exit on the 15 s timeout every single flight,
                # logging a "did not converge" warning that meant nothing --
                # which is worse than no warning, because it trains the operator
                # to ignore the one place a real convergence failure would show.
                max_distance=2.0 * deadband,
            )
        )

        rate = LoopRate(kinematics.control_loop_hz)
        deadline = Deadline(kinematics.climb_timeout_sec)
        converged = False

        # Progress telemetry so the operator can see the climb is alive.
        _LOG_INTERVAL_SEC: float = 2.0
        _last_log_time: float = 0.0

        # Stall detection: if the altitude fails to advance by more than the
        # deadband over this window the firmware is likely rejecting the
        # commanded vz, and waiting for the full timeout is pointless.
        _STALL_WINDOW_SEC: float = 10.0
        _stall_ref_time: float = 0.0
        _stall_ref_alt: float = altitude

        with ctx.failsafe.climb_window(kinematics.max_climb_speed_mps):
            while deadline.active:
                if ctx.interrupted():
                    self._halt(ctx)
                    return StepStatus.ABORTED

                snapshot = ctx.odom_supervisor.snapshot()
                altitude = snapshot.relative_altitude
                self._call_takeoff_once_confirmed(ctx, altitude)

                # Checked on every cycle and in every mode, benchtop included.
                # This is the only phase of the mission that commands ascent, so
                # it is the only one that can drive itself through the ceiling.
                if ctx.odom_supervisor.is_ceiling_breached():
                    self._halt(ctx)
                    ctx.failsafe.trigger_emergency_land(
                        f"Altitude ceiling breached during ascent: "
                        f"{altitude:.2f} m > {ceiling:.2f} m."
                    )
                    return StepStatus.FAILURE

                if not ctx.drone.no_fly:
                    healthy, reason = ctx.failsafe.evaluate_system_health()
                    if not healthy:
                        self._halt(ctx)
                        ctx.failsafe.trigger_emergency_land(reason)
                        return StepStatus.FAILURE

                if ctx.grab_frame(timeout_sec=0.2) is not None:
                    ctx.failsafe.notify_frame_received()

                dt = rate.tick()
                remaining = target - altitude

                # Feedforward: the fastest ascent that can still be arrested
                # within the remaining distance, saturated at the authorized
                # climb rate. For the operational climb (<= 0.30 m/s), size the
                # deceleration at 0.12 m/s^2 so easing begins ~0.25 m out and
                # stops inside the 0.25 m ceiling margin rather than overshooting.
                climb_decel = (
                    min(kinematics.max_accel_mps2, 0.12)
                    if kinematics.max_climb_speed_mps <= 0.30
                    else kinematics.max_accel_mps2
                )
                if remaining > 0.0:
                    desired = braking_velocity(
                        remaining,
                        climb_decel,
                        cruise_velocity=kinematics.max_climb_speed_mps,
                        arrival_tolerance=deadband,
                    )
                    profiled = max(0.0, profile.step(desired, dt))
                else:
                    desired = 0.0
                    profiled = max(0.0, profile.step(0.0, dt))
                    if profiled < 0.01:
                        profile.reset(0.0, 0.0)
                        profiled = 0.0

                safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(
                    profiled, 0.0, allow_climb=True
                )
                ctx.drone.move_velocity(
                    vx=0.0,
                    vy=0.0,
                    vz=ctx.speed_calibration.to_normalized(safe_vz, axis="vertical"),
                    vyaw=safe_vyaw,
                )

                report = settlement.update(
                    x=altitude, y=0.0, speed=abs(snapshot.vz), distance=abs(remaining)
                )
                if report.settled:
                    converged = True
                    break

                # -- periodic progress telemetry ----------------------------
                elapsed = deadline.elapsed_sec
                if elapsed - _last_log_time >= _LOG_INTERVAL_SEC:
                    _last_log_time = elapsed
                    logger.info(
                        "Ascent progress: alt=%.2f m, remaining=%.2f m, "
                        "cmd_vz=%.3f, odom_vz=%.3f, settle=[%s] (%.1f/%.1f s)",
                        altitude,
                        remaining,
                        safe_vz,
                        snapshot.vz,
                        report.reason,
                        elapsed,
                        kinematics.climb_timeout_sec,
                    )

                # -- altitude stall detection --------------------------------
                # If the drone has not closed at least one deadband of altitude
                # over a rolling window, the firmware is likely rejecting the
                # commanded vz and waiting the full timeout is pointless.
                if elapsed - _stall_ref_time >= _STALL_WINDOW_SEC:
                    gained = altitude - _stall_ref_alt
                    if gained < deadband and remaining > deadband:
                        logger.warning(
                            "Altitude stall detected: gained only %.3f m in %.1f s "
                            "(need %.2f m more). Breaking out early.",
                            gained,
                            _STALL_WINDOW_SEC,
                            remaining,
                        )
                        break
                    _stall_ref_time = elapsed
                    _stall_ref_alt = altitude

            self._halt(ctx)

        altitude = ctx.odom_supervisor.snapshot().relative_altitude
        if converged:
            logger.info("Ascent converged at %.2f m (target %.2f m).", altitude, target)
        else:
            # Not a fault. The drone is airborne, healthy, and below its ceiling;
            # ending the mission over a slow climb would be a worse outcome than
            # flying the profile slightly low. The operator is told plainly.
            logger.warning(
                "Ascent did not converge within %.1f s: holding %.2f m against a %.2f m "
                "target. The mission continues at the altitude actually achieved.",
                kinematics.climb_timeout_sec,
                altitude,
                target,
            )
        return StepStatus.SUCCESS

    def _descend_to_target(self, ctx: MissionContext, target: float) -> StepStatus:
        """Descend from the firmware's hover height onto a lower target.

        Translation is held at zero and ``vz`` comes from the altitude
        governor, as in the pad-departure settle of Stage 2: the governor is
        the law that holds the altitude for the rest of the flight, so the
        hand-over to Stage 2 carries no step in the vertical command. The
        ceiling and, outside ``no_fly``, the system health are checked every
        cycle. Convergence is the altitude inside ``governor.deadband_m`` of
        the target for :data:`_DESCENT_SETTLE_WINDOW_SEC` (a
        :class:`SettlementDetector` over fresh odometry samples only).
        ``kinematics.climb_timeout_sec`` bounds the loop; reaching it is not a
        fault and the mission continues under the governor.
        """
        kinematics = ctx.params.kinematics
        band = ctx.params.governor.deadband_m
        ceiling = ctx.odom_supervisor.altitude_ceiling
        altitude = ctx.odom_supervisor.snapshot().relative_altitude
        logger.info(
            "Closing a %.2f m altitude surplus: %.2f m -> %.2f m under the altitude governor "
            "(band %.2f m, ceiling %.2f m).",
            altitude - target,
            altitude,
            target,
            band,
            ceiling,
        )
        self._announce("Descendo", "ajustando altitude de operação")
        settlement = SettlementDetector(
            SettlementCriteria(
                window_sec=_DESCENT_SETTLE_WINDOW_SEC,
                min_samples=kinematics.takeoff_settle_min_samples,
                max_speed=kinematics.takeoff_settle_max_vz_mps,
                max_position_sigma=kinematics.takeoff_settle_max_altitude_sigma_m,
                max_distance=band,
            )
        )
        rate = LoopRate(kinematics.control_loop_hz)
        deadline = Deadline(kinematics.climb_timeout_sec)
        last_sample: Optional[int] = None
        converged = False

        while deadline.active:
            if ctx.interrupted():
                self._halt(ctx)
                return StepStatus.ABORTED

            snapshot = ctx.odom_supervisor.snapshot()
            altitude = snapshot.relative_altitude
            self._call_takeoff_once_confirmed(ctx, altitude)

            if ctx.odom_supervisor.is_ceiling_breached():
                self._halt(ctx)
                ctx.failsafe.trigger_emergency_land(
                    f"Altitude ceiling breached during the post-takeoff descent: "
                    f"{altitude:.2f} m > {ceiling:.2f} m."
                )
                return StepStatus.FAILURE

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    self._halt(ctx)
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            if ctx.grab_frame(timeout_sec=0.2) is not None:
                ctx.failsafe.notify_frame_received()

            dt = rate.tick()
            vz = governed_vz(ctx, altitude, dt, vx_commanded=0.0)
            safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
            ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)

            if snapshot.sample_count == last_sample:
                continue
            last_sample = snapshot.sample_count
            report = settlement.update(
                x=altitude,
                y=0.0,
                speed=abs(snapshot.vz),
                distance=abs(altitude - target),
                timestamp=snapshot.timestamp,
            )
            if report.settled:
                converged = True
                break

        self._halt(ctx)
        altitude = ctx.odom_supervisor.snapshot().relative_altitude
        if converged:
            logger.info("Descent converged at %.2f m (target %.2f m).", altitude, target)
        else:
            logger.warning(
                "Descent did not converge within %.1f s: holding %.2f m against a %.2f m "
                "target. The mission continues under the altitude governor.",
                kinematics.climb_timeout_sec,
                altitude,
                target,
            )
        return StepStatus.SUCCESS

    # -------------------------------------------------------------- completion

    def _finalize(self, ctx: MissionContext) -> StepStatus:
        """Freeze the airborne origin at the operating altitude and hand over."""
        # Vet the telemetry *before* overwriting the origin with it. The freeze
        # is unguarded -- it takes whatever the last sample said -- so running it
        # first meant that odometry which had gone stale during the climb would
        # clobber the trustworthy ground-measured origin with a drifted one, and
        # the RTL that consumes it would then fly home to the wrong place. The
        # ground reference is a usable fallback; a stale airborne sample is not.
        snapshot = ctx.odom_supervisor.snapshot()
        self._call_takeoff_once_confirmed(ctx, snapshot.relative_altitude)
        if not ctx.drone.no_fly:
            healthy, reason = ctx.failsafe.evaluate_system_health()
            if not healthy:
                ctx.failsafe.trigger_emergency_land(reason)
                return StepStatus.FAILURE

            if not snapshot.is_calibrated:
                # Not a warning. Without a trustworthy z0 the altitude ceiling
                # and the anti-climb governor both compare against an arbitrary
                # datum, which is the same as having neither -- and this stage
                # already refuses to launch in that state, so reaching here means
                # the reference was lost in flight.
                logger.error(
                    "Airborne without a calibrated ground reference; the altitude ceiling and "
                    "the anti-climb governor have no datum to enforce."
                )
                ctx.failsafe.trigger_emergency_land("Ground reference lost during ascent.")
                return StepStatus.FAILURE

        # The altitude acquisition is over: the ratchet hands over to the
        # nominal ceiling before the origin is frozen.
        _ceiling_transition(ctx, "release_ceiling_transient")

        # Freeze the horizontal origin only now. Measuring it on the ground
        # would bake the ground-effect transient into the coordinate the drone
        # spends the rest of the mission trying to return to, and measuring it
        # before the climb would anchor it to an altitude the mission has since
        # left.
        ctx.odom_supervisor.freeze_hover_takeoff_origin()
        self._arm_dead_reckoning(ctx)
        snapshot = ctx.odom_supervisor.snapshot()
        bb = getattr(ctx, "blackboard", None)
        if bb is not None:
            bb.initial_yaw_rad = float(snapshot.yaw)
        t_hover = time.monotonic()
        if bb is not None:
            bb.t_hover = t_hover
        logger.info("[TIMING] t_hover mono=%.3f", t_hover)

        logger.info(
            "Stage 1 complete: relative altitude %.2f m, residual speed %.3f m/s.",
            snapshot.relative_altitude,
            snapshot.horizontal_speed,
        )
        self._announce("Decolagem concluída", "decolagem concluída, iniciando varredura")
        return StepStatus.SUCCESS

    @staticmethod
    def _arm_dead_reckoning(ctx: MissionContext) -> None:
        """Start aggregating displacement from the airborne origin.

        Armed in the same breath as the horizontal origin is frozen, and that
        pairing is the whole reason it happens here rather than at takeoff. The
        aggregate is a displacement *from* a point, so the two have to agree on
        which point: arming on the ground would credit the aggregate with the
        lift-off transient and the climb's own horizontal wander, and the return
        leg would then fly home to a spot the drone was never at.
        """
        tracker = getattr(ctx.drone, "motion_tracker", None)
        if tracker is None:
            return
        tracker.arm()

    @staticmethod
    def _halt(ctx: MissionContext) -> None:
        """Zero the vertical command. The Bebop latches its last Twist forever."""
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _announce(
        action: str,
        detail: str,
        *,
        priority: Optional[str] = None,
        wait: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            payload: Dict[str, Any] = {"etapa": detail}
            if details:
                payload.update(details)
            kwargs = {"details": payload, "wait": wait}
            if priority is not None:
                kwargs["priority"] = priority
            announce_sync(action, **kwargs)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
