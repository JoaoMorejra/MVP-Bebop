"""Stage 2: linear forward search under strict kinematic locks.

Two invariants hold for the whole cruise. Yaw rate is pinned at zero, because
the Bebop derives its odometry from optical flow and any rotation corrupts the
horizontal position estimate the return leg later depends on. Vertical velocity
is constrained non-positive by the anti-climb governor.

Two defects in the previous implementation are fixed here. It issued no velocity
command at all on an iteration where a target was visible but not yet confirmed,
leaving the drone coasting on whatever it had last been told. And its
confirmation counter reset to zero on any single missed frame, so a detector
blink discarded accumulated evidence.
"""

from __future__ import annotations

import logging
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.detection_filter import HysteresisConfirmer
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("Step2Search")


class ForwardSearchStep(BaseStep):
    """Cruises forward, running inference, until a target is confirmed."""

    def __init__(self) -> None:
        super().__init__("STEP 2: Linear Forward Search")

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        vision_cfg = ctx.params.vision
        kinematics_cfg = ctx.params.kinematics
        rtl_cfg = ctx.params.rtl
        timeout = ctx.params.timeouts.search_timeout_sec

        cruise_mps = ctx.speed_calibration.to_mps(kinematics_cfg.forward_cruise_velocity)
        profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(cruise_mps, 1e-3),
                max_accel=kinematics_cfg.max_accel_mps2,
                max_jerk=kinematics_cfg.max_jerk_mps3,
            )
        )
        shaper = QuantizedCommandShaper(rtl_cfg.min_effective_speed, rtl_cfg.quantization_step)
        confirmer = HysteresisConfirmer(vision_cfg.confirmation_frames, vision_cfg.release_frames)

        logger.info(
            "Linear search engaged: window %.1f s, cruise %.3f m/s (%.3f normalized), "
            "acceleration %.2f m/s^2, jerk %.2f m/s^3.",
            timeout,
            cruise_mps,
            kinematics_cfg.forward_cruise_velocity,
            kinematics_cfg.max_accel_mps2,
            kinematics_cfg.max_jerk_mps3,
        )
        logger.info("Invariants: vyaw == 0.0 (optical-flow fidelity), vz <= 0.0 (anti-climb).")

        deadline = Deadline(timeout)
        rate = LoopRate(kinematics_cfg.control_loop_hz)
        ctx.failsafe.notify_frame_received()

        while deadline.active:
            if ctx.emergency_event.is_set():
                self._halt(ctx, profile, shaper, rate.period_sec)
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            frame = ctx.handler.take_photo(timeout_sec=1.0)
            dt = rate.tick()

            if frame is None:
                # No perception this cycle, but the drone still needs a command:
                # the Bebop latches its last Twist indefinitely.
                self._cruise(ctx, profile, shaper, dt, cruise_mps)
                continue

            ctx.failsafe.notify_frame_received()
            result = ctx.detector.detect(frame, conf=vision_cfg.confidence_threshold)
            targets = result.filter_by_class(vision_cfg.target_classes)
            report = confirmer.update(bool(targets))

            snapshot = ctx.odom_supervisor.snapshot()
            ctx.publish_annotated_stream(
                frame,
                result,
                f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 2: SEARCH "
                f"({deadline.elapsed_sec:.1f}s/{timeout:.1f}s) "
                f"| TILT: {ctx.current_tilt_deg:.1f}deg | ALT: {snapshot.relative_altitude:.2f}m",
            )

            if report.just_confirmed:
                best = max(targets, key=lambda detection: detection.confidence)
                logger.info(
                    "Target '%s' confirmed at %.2f confidence after %d frames. "
                    "Decelerating to station keeping.",
                    best.class_name,
                    best.confidence,
                    vision_cfg.confirmation_frames,
                )
                ctx.blackboard.target_confirmed = True
                ctx.blackboard.confirmed_tilt_deg = ctx.current_tilt_deg
                ctx.blackboard.confirmed_target_px = best.center
                self._announce(
                    "Acidente detectado", "alvo detectado na pista, iniciando aproximação"
                )
                self._halt(ctx, profile, shaper, rate.period_sec)
                return StepStatus.SUCCESS

            if targets:
                best = max(targets, key=lambda detection: detection.confidence)
                logger.info(
                    "Candidate target (%d/%d): class='%s', conf=%.2f, alt=%.2f m",
                    report.hits,
                    vision_cfg.confirmation_frames,
                    best.class_name,
                    best.confidence,
                    snapshot.relative_altitude,
                )
                # Hold station while the confirmation filter fills, rather than
                # continuing to close on an unconfirmed target.
                self._command(ctx, profile, shaper, dt, 0.0)
            else:
                self._cruise(ctx, profile, shaper, dt, cruise_mps)

        logger.warning("Search window of %.1f s expired without confirmation.", timeout)
        ctx.blackboard.target_confirmed = False
        self._halt(ctx, profile, shaper, rate.period_sec)
        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- motion

    def _cruise(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        cruise_mps: float,
    ) -> None:
        """Advance along the search track under the jerk-limited profile."""
        self._command(ctx, profile, shaper, dt, cruise_mps)

    def _halt(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
    ) -> None:
        """Decelerate to a stop without the nose-down transient of a step command.

        Ramping down matters here specifically: an abrupt stop pitches the
        airframe, which swings the camera at the exact moment the mission needs
        a stable view of the target it just confirmed.
        """
        for _ in range(self._cycles_to_rest(profile, dt)):
            self._command(ctx, profile, shaper, dt, 0.0)
            if abs(profile.velocity) <= 1e-6:
                break

    def _command(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        target_mps: float,
    ) -> None:
        """Profile, convert, shape, and transmit one forward velocity command."""
        profiled = profile.step(target_mps, dt)
        vx = shaper.shape(ctx.speed_calibration.to_normalized(profiled), dt)
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=max(0.0, vx), vy=0.0, vz=safe_vz, vyaw=safe_vyaw)

    @staticmethod
    def _cycles_to_rest(profile: JerkLimitedProfile, dt: float) -> int:
        """Upper bound on the cycles a full deceleration can take."""
        limits = profile.limits
        seconds = limits.max_velocity / limits.max_accel + limits.max_accel / limits.max_jerk
        return max(1, int(seconds / max(dt, 1e-3)) + 2)

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
