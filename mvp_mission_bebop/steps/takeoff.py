"""Stage 1: IMU calibration, ground reference, takeoff, and hover stabilization."""

from __future__ import annotations

import logging
from typing import Optional

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import (
    JerkLimitedProfile,
    ProfileLimits,
    braking_velocity,
)
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step1Takeoff")

#: Seconds before liftoff at which the takeoff clearance call is made.
_CLEARANCE_ANNOUNCE_SEC: float = 3.2

#: Settling time after flat trim, in seconds. The IMU needs a moment on a level
#: surface before the calibration it just performed is trustworthy.
_FLAT_TRIM_SETTLE_SEC: float = 2.0


class TakeoffStep(BaseStep):
    """Calibrates, launches, and establishes a drift-free airborne origin."""

    def __init__(self) -> None:
        super().__init__("STEP 1: Calibration, Takeoff & Stabilization")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

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

        status = self._launch(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._stabilize(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        status = self._ascend(ctx)
        if status is not StepStatus.SUCCESS:
            return status

        return self._finalize(ctx)

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
            return StepStatus.SUCCESS

        logger.info("Pre-flight countdown and sensor warmup (%.1f s)...", duration)
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        announced = False
        warmup_frames = 0

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            remaining = deadline.remaining_sec
            frame = ctx.grab_frame(timeout_sec=0.2)
            if frame is not None:
                ctx.failsafe.notify_frame_received()
                try:
                    result = ctx.detector.detect(frame)
                    warmup_frames += 1
                    ctx.publish_annotated_stream(
                        frame, result, f"CONTAGEM REGRESSIVA: {remaining:.1f}s | YOLO PRONTO"
                    )
                except Exception as exc:  # noqa: BLE001 - warmup is best-effort
                    logger.debug("Warmup inference notice: %s", exc)

            if remaining <= _CLEARANCE_ANNOUNCE_SEC and not announced:
                announced = True
                logger.info("Takeoff synchronization window reached. Announcing clearance.")
                self._announce("Decolagem autorizada", "decolagem autorizada, iniciando voo")

            rate.tick()

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
        """Hover until the liftoff transient decays.

        This settles the airframe at whatever height the firmware's launch
        profile chose. It deliberately does *not* freeze the return origin any
        more: on a mission configured above the firmware's hover height the
        drone has not yet reached its operating altitude here, and freezing the
        origin mid-ascent would anchor the mission to a transient. That now
        happens in :meth:`_finalize`, after the climb has converged.
        """
        duration = ctx.params.kinematics.takeoff_stabilize_duration_sec
        logger.info("Stabilizing in hover for %.1f s...", duration)

        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)

        while deadline.active:
            if ctx.emergency_event.is_set():
                return StepStatus.ABORTED

            if ctx.grab_frame(timeout_sec=0.2) is not None:
                ctx.failsafe.notify_frame_received()
            rate.tick()

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
                if ctx.emergency_event.is_set():
                    self._halt(ctx)
                    return StepStatus.ABORTED

                snapshot = ctx.odom_supervisor.snapshot()
                altitude = snapshot.relative_altitude

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
