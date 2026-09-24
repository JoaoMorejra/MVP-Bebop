"""Stage 2: linear forward search under strict kinematic locks.

One invariant holds for the whole cruise: yaw rate is pinned at zero, because
the Bebop derives its odometry from optical flow and any rotation corrupts the
horizontal position estimate the return leg later depends on.

The vertical axis used to be a second one -- constrained non-positive by the
anti-climb governor -- and this is the stage where that turned out to be the
wrong constraint. Pitching into the cruise command tilts the thrust vector, the
firmware does not make up the vertical component, and the airframe sinks for as
long as the translation lasts. A descent-only governor has no authority over an
error of that sign, so it commanded ``vz = 0.0`` and the drone went on sinking:
the cruise began at the target altitude and did not end there. The stage now
runs inside an altitude-hold window, and scales its own cruise demand back
whenever that loop is losing -- the cruise is the disturbance, so flying it
slower is the most direct correction available.

Two defects in the previous implementation are fixed here. It issued no velocity
command at all on an iteration where a target was visible but not yet confirmed,
leaving the drone coasting on whatever it had last been told. And its
confirmation counter reset to zero on any single missed frame, so a detector
blink discarded accumulated evidence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Sequence

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.detection_filter import HysteresisConfirmer
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    from nectar.ai.detection.core.types import Detection

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
        logger.info(
            "Invariants: vyaw == 0.0 (optical-flow fidelity), vz bounded to "
            "[-%.3f, +%.3f] by altitude hold at %.2f m.",
            ctx.params.governor.max_descent_speed,
            ctx.params.governor.climb_authority,
            kinematics_cfg.target_altitude_m,
        )

        deadline = Deadline(timeout)
        rate = LoopRate(kinematics_cfg.control_loop_hz)
        ctx.failsafe.notify_frame_received()

        with ctx.failsafe.altitude_hold_window(
            ctx.params.governor.climb_authority
        ), ctx.perception.session(
            conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
        ):
            try:
                return self._cruise_until_confirmed(
                    ctx, deadline, rate, profile, shaper, confirmer, cruise_mps, timeout
                )
            finally:
                logger.info("Search loop cadence: %s.", rate.cadence_report())

    # ------------------------------------------------------------ cruise loop

    def _cruise_until_confirmed(
        self,
        ctx: MissionContext,
        deadline: Deadline,
        rate: LoopRate,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        confirmer: HysteresisConfirmer,
        cruise_mps: float,
        timeout: float,
    ) -> StepStatus:
        """Cruise the search track until a target confirms or the window closes.

        Separated from :meth:`execute` so that the altitude-hold window is a
        scope: every exit from this loop -- confirmation, timeout, abort, health
        failure -- passes back out through the ``with`` block, and the vertical
        axis is descent-only again before the next stage begins.
        """
        vision_cfg = ctx.params.vision
        max_age = vision_cfg.perception_max_age_sec
        last_generation = 0
        targets: Sequence["Detection"] = ()

        while deadline.active:
            if ctx.interrupted():
                self._halt(ctx, profile, shaper, rate.period_sec, rate)
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            dt = rate.tick()
            sample = ctx.perception.get_latest(max_age)

            if sample is None:
                # No usable perception this cycle, but the drone still needs a
                # command: the Bebop latches its last Twist indefinitely.
                targets = ()
                self._cruise(ctx, profile, shaper, dt, cruise_mps)
                continue

            if sample.generation == last_generation:
                # The worker has not produced a new frame since the last cycle.
                # Keep flying the decision that frame supported, but do not feed
                # it to the confirmer again: the hysteresis counts distinct
                # frames, and re-counting one would confirm on a single image.
                self._act(ctx, profile, shaper, dt, cruise_mps, targets)
                continue

            last_generation = sample.generation
            ctx.failsafe.notify_frame_received()
            targets = sample.result.filter_by_class(vision_cfg.target_classes)
            report = confirmer.update(bool(targets))

            snapshot = ctx.odom_supervisor.snapshot()
            ctx.perception.set_status(
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
                self._halt(ctx, profile, shaper, rate.period_sec, rate)
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
            self._act(ctx, profile, shaper, dt, cruise_mps, targets)

        logger.warning("Search window of %.1f s expired without confirmation.", timeout)
        ctx.blackboard.target_confirmed = False
        self._halt(ctx, profile, shaper, rate.period_sec, rate)
        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- motion

    def _act(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        cruise_mps: float,
        targets: Sequence["Detection"],
    ) -> None:
        """Hold station on a visible candidate, otherwise continue the cruise.

        Holding while the confirmation filter fills, rather than continuing to
        close on an unconfirmed target, keeps the candidate in frame for the
        frames that will confirm or release it.
        """
        if targets:
            self._command(ctx, profile, shaper, dt, 0.0)
        else:
            self._cruise(ctx, profile, shaper, dt, cruise_mps)


    def _cruise(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        cruise_mps: float,
    ) -> None:
        """Advance along the search track under the jerk-limited profile.

        The demand is scaled by what the altitude loop will tolerate before it
        is profiled, not after. Scaling the shaped command instead would leave
        the profile believing it was still tracking the full cruise, so the jerk
        limit would be measured against a velocity the airframe was never given
        -- and a demand scaled below the driver's quantization floor would be
        truncated to a standstill rather than duty-cycled down to it.
        """
        scale = getattr(ctx.governor, "horizontal_scale", None)
        permitted = cruise_mps * (float(scale()) if callable(scale) else 1.0)
        self._command(ctx, profile, shaper, dt, permitted)

    def _halt(
        self,
        ctx: MissionContext,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
        dt: float,
        rate: Optional[LoopRate] = None,
    ) -> None:
        """Decelerate to a stop without the nose-down transient of a step command.

        Ramping down matters here specifically: an abrupt stop pitches the
        airframe, which swings the camera at the exact moment the mission needs
        a stable view of the target it just confirmed.

        The ramp has to be *paced* for any of that to be true, and it was not.
        The loop stepped the profile by a nominal ``dt`` with nothing sleeping
        between iterations, so the whole 0.6 s deceleration was published in
        about 0.4 ms -- eleven Twist messages back to back onto a depth-1
        queue, of which the airframe observes essentially only the last. The
        drone therefore received the step command this method exists to avoid,
        and the profile was decorative. Pacing it against the same ``LoopRate``
        the caller is already running restores the intent.
        """
        for _ in range(self._cycles_to_rest(profile, dt)):
            self._command(ctx, profile, shaper, dt, 0.0)
            if abs(profile.velocity) <= 1e-6:
                break
            if rate is not None:
                rate.tick()

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
        vz = ctx.governor.compute_vz(
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=vx
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(max(0.0, vx), 0.0)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

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
