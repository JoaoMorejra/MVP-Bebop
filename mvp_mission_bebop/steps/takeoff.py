"""Stage 1: IMU calibration, ground reference, takeoff, and hover stabilization."""

from __future__ import annotations

import logging
import math
from typing import Final, FrozenSet, Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.steps.base import BaseStep, StepStatus
from mvp_mission_bebop.telemetry.milestones import emit_milestone

logger = logging.getLogger("Step1Takeoff")

#: Seconds before liftoff at which the takeoff clearance call is made.
_CLEARANCE_ANNOUNCE_SEC: float = 3.2

#: Settling time after flat trim, in seconds. The IMU needs a moment on a level
#: surface before the calibration it just performed is trustworthy.
_FLAT_TRIM_SETTLE_SEC: float = 2.0


#: ARSDK flying states that confirm the airframe has left the ground: takingoff
#: (1) and hovering (2). ``usertakeoff`` (6) waits on the ground.
_TAKEOFF_CONFIRMING_STATES: Final[FrozenSet[int]] = frozenset({1, 2})


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

    def __init__(self) -> None:
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")
        self._takeoff_called = False

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
        emit_milestone("mission.takeoff", {"altitude_m": round(kinematics.target_altitude_m, 2)})

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)
        self._takeoff_called = False

        if not self._check_prearm_battery(ctx):
            return StepStatus.FAILURE

        ctx.current_tilt_deg = ctx.params.gimbal.search_tilt_deg
        ctx.drone.camera_control(tilt=ctx.current_tilt_deg, pan=0.0)

        logger.info("Executing IMU flat trim on a level surface...")
        ctx.drone.flat_trim()
        ctx.drone.delay(_FLAT_TRIM_SETTLE_SEC)

        if not self._calibrate(ctx):
            return StepStatus.FAILURE

        status = self._countdown(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        ctx.blackboard.takeoff_committed = True
        status = self._launch(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._stabilize(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._ascend(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._finalize(ctx)
        if status is StepStatus.SUCCESS:
            ctx.blackboard.takeoff_complete = True
        return status

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
        duration = ctx.params.kinematics.countdown_sec
        if duration <= 0.0:
            emit_milestone("mission.countdown_3", {"remaining_sec": 0})
            emit_milestone("mission.countdown", {"remaining_sec": 0})
            return StepStatus.SUCCESS

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
        with ctx.perception.session(imgsz=ctx.params.vision.inference_imgsz):
            while deadline.active:
                if ctx.interrupted():
                    return StepStatus.ABORTED

                remaining = deadline.remaining_sec
                whole = int(math.ceil(max(0.0, remaining)))
                if whole != last_whole and whole > 0:
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

        if not announced:
            emit_milestone("mission.countdown_3", {"remaining_sec": 0})
        emit_milestone("mission.countdown", {"remaining_sec": 0})
        logger.info("Perception pipeline warmed up over %d frames.", warmup_frames)
        return StepStatus.SUCCESS

    # ----------------------------------------------------------------- launch

    def _launch(self, ctx: MissionContext) -> StepStatus:
        """Command takeoff.

        The altitude argument is accepted by the SDK and ignored by the Bebop
        firmware, which runs its own launch profile. It is passed through for
        API fidelity and used here only for logging and the ceiling.
        """
        target_altitude = ctx.params.kinematics.target_altitude_m
        logger.info(
            "Issuing takeoff (nominal altitude %.2f m, ceiling %.2f m). "
            "Note: the Bebop firmware selects its own launch altitude.",
            target_altitude,
            ctx.odom_supervisor.altitude_ceiling,
        )

        if not ctx.drone.takeoff(altitude=target_altitude):
            logger.critical("Autonomous takeoff rejected by the flight controller.")
            self._announce(
                "Falha na decolagem",
                "decolagem rejeitada pela controladora de voo",
                priority="CRITICAL",
                wait=True,
            )
            return StepStatus.FAILURE

        return StepStatus.SUCCESS

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
        as in :meth:`_ascend`: its isotropic sigma then reduces to the standard
        deviation of the altitude.

        Two kinds of sample are kept out of the window because they look like
        convergence without being it: repeats of an odometry sample already
        seen, which a stalled stream produces with zero variance forever, and
        samples below ``takeoff_settle_min_altitude_m``, which is an airframe
        still on the ground after ``takeoff`` returned.
        """
        kinematics = ctx.params.kinematics
        ceiling_sec = kinematics.takeoff_stabilize_duration_sec
        min_altitude = kinematics.takeoff_settle_min_altitude_m
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
            "sustained for %.1f s above %.2f m (ceiling %.1f s)...",
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
                return StepStatus.SUCCESS

        # Not a fault: this is the full wait the phase always used to make.
        logger.warning(
            "Liftoff transient did not settle within the %.1f s ceiling (%s). Continuing.",
            ceiling_sec,
            report if report is not None else "no airborne odometry samples",
        )
        return StepStatus.SUCCESS

    # ------------------------------------------------------------------ ascent

    def _ascend(self, ctx: MissionContext) -> StepStatus:
        """Close the gap between the firmware's hover height and the target.

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
        target = kinematics.target_altitude_m
        deadband = kinematics.climb_deadband_m
        ceiling = ctx.odom_supervisor.altitude_ceiling

        altitude = ctx.odom_supervisor.snapshot().relative_altitude
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
                # climb rate. Aiming at the edge of the arrival window rather
                # than at the point target keeps the profile from asymptoting.
                desired = braking_velocity(
                    remaining,
                    kinematics.max_accel_mps2,
                    cruise_velocity=kinematics.max_climb_speed_mps,
                    arrival_tolerance=deadband,
                )
                profiled = profile.step(desired, dt)

                safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(
                    profiled, 0.0, allow_climb=True
                )
                ctx.drone.move_velocity(
                    vx=0.0,
                    vy=0.0,
                    vz=ctx.speed_calibration.to_normalized(safe_vz),
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

        # Freeze the horizontal origin only now. Measuring it on the ground
        # would bake the ground-effect transient into the coordinate the drone
        # spends the rest of the mission trying to return to, and measuring it
        # before the climb would anchor it to an altitude the mission has since
        # left.
        ctx.odom_supervisor.freeze_hover_takeoff_origin()
        self._arm_dead_reckoning(ctx)
        snapshot = ctx.odom_supervisor.snapshot()

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
        action: str, detail: str, *, priority: Optional[str] = None, wait: bool = False
    ) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            kwargs = {"details": {"etapa": detail}, "wait": wait}
            if priority is not None:
                kwargs["priority"] = priority
            announce_sync(action, **kwargs)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
