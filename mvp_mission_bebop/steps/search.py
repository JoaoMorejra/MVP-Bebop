"""Stage 2: lateral left search under strict kinematic locks.

The search translates only along the body Y axis (+y is LEFT, REP-103). The
forward channel is pinned at ``vx == 0.0`` by construction and the lateral one
at ``vy >= 0.0``, so the airframe never closes on the target and never drifts
back toward the launch pad it just left.

Yaw rate is pinned at zero for the whole cruise, because
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

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, Dict, Final, Optional, Sequence

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.geometry import CameraIntrinsics, project_to_ground
from mvp_mission_bebop.controllers.profiling import JerkLimitedProfile, ProfileLimits
from mvp_mission_bebop.controllers.quantization import QuantizedCommandShaper
from mvp_mission_bebop.controllers.yaw_pattern import YawRateLimiter
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.detection_filter import HysteresisConfirmer
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import (
    BaseStep,
    StepStatus,
    cross_axis_correction,
    governed_vz,
    select_target,
)
from mvp_mission_bebop.telemetry.milestones import emit_milestone
from mvp_mission_bebop.telemetry.odometry import OdometrySnapshot

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    from nectar.ai.detection.core.types import Detection

logger = logging.getLogger("Step2Search")

_STEP_NAMES: Final[Dict[MissionMode, str]] = {
    MissionMode.ADVANCED: "STEP 2: Lateral Left Search",
    MissionMode.SIMPLE: "STEP 2: Yaw Scan Search",
}


class LateralSearchStep(BaseStep):
    """Cruises to the left, running inference, until a target is confirmed.

    Parameters
    ----------
    mode : MissionMode
        Mission profile, keyword-only. Selects the stage name; ``ADVANCED`` (the
        default) is the historical behaviour. ``SIMPLE`` replaces the lateral
        cruise with a left yaw scan (``vx == vy == 0.0``, ``vyaw >= 0.0``) inside
        a yaw authority window, and ends on an explicit zero yaw command.
    """

    def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:
        self._mode = mode
        self._yaw_ramp: Optional[YawRateLimiter] = None
        super().__init__(_STEP_NAMES[mode])

    @property
    def mode(self) -> MissionMode:
        """Mission profile this stage was built for."""
        return self._mode

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        vision_cfg = ctx.params.vision
        kinematics_cfg = ctx.params.kinematics
        rtl_cfg = ctx.params.rtl
        timeout = ctx.params.timeouts.search_timeout_sec

        cruise_mps = ctx.speed_calibration.to_mps(
            kinematics_cfg.forward_cruise_velocity, axis="lateral"
        )
        profile = JerkLimitedProfile(
            ProfileLimits(
                max_velocity=max(cruise_mps, 1e-3),
                max_accel=kinematics_cfg.max_accel_mps2,
                max_jerk=kinematics_cfg.max_jerk_mps3,
            )
        )
        shaper = QuantizedCommandShaper(rtl_cfg.min_effective_speed, rtl_cfg.quantization_step)
        confirmer = HysteresisConfirmer(vision_cfg.confirmation_frames, vision_cfg.release_frames)
        selector = getattr(ctx, "target_selector", None)
        if selector is not None:
            selector.reset()

        simple = self._mode is MissionMode.SIMPLE
        if simple:
            yaw_cfg = ctx.params.yaw_pattern
            logger.info(
                "Yaw scan engaged: window %.1f s, rate %.3f normalized (about %.1f deg/s), "
                "slew %.2f /s. Translation is disabled.",
                timeout,
                yaw_cfg.scan_vyaw,
                math.degrees(yaw_cfg.scan_vyaw * ctx.params.dead_reckoning.yaw_rate_per_unit_rad_s),
                yaw_cfg.scan_ramp_per_sec,
            )
            logger.info(
                "Invariants: vx == 0.0, vy == 0.0, vyaw >= 0.0 (left) bounded at %.3f by the yaw "
                "window, vz bounded by altitude hold at %.2f m.",
                yaw_cfg.max_vyaw,
                ctx.params.relative_target_altitude_m,
            )
        else:
            logger.info(
                "Lateral left search engaged: window %.1f s, cruise %.3f m/s (%.3f normalized), "
                "acceleration %.2f m/s^2, jerk %.2f m/s^3.",
                timeout,
                cruise_mps,
                kinematics_cfg.forward_cruise_velocity,
                kinematics_cfg.max_accel_mps2,
                kinematics_cfg.max_jerk_mps3,
            )
            logger.info(
                "Invariants: vx == 0.0, vyaw == 0.0 (optical-flow fidelity), vy >= 0.0, vz bounded to "
                "[-%.3f, +%.3f] by altitude hold at %.2f m above the launch point.",
                ctx.params.governor.max_descent_speed,
                ctx.params.governor.climb_authority,
                ctx.params.relative_target_altitude_m,
            )

        self._assert_search_tilt(ctx)

        if simple:
            self._begin_yaw_scan(ctx)

        deadline = Deadline(timeout)
        rate = LoopRate(kinematics_cfg.control_loop_hz)
        ctx.failsafe.notify_frame_received()
        bb = getattr(ctx, "blackboard", None)
        if bb is None or getattr(bb, "takeoff_complete", False):
            emit_milestone(
                "mission.scan_start", {"window_sec": timeout, "cruise_mps": round(cruise_mps, 3)}
            )

        yaw_scope = (
            ctx.failsafe.yaw_window(ctx.params.yaw_pattern.max_vyaw)
            if simple
            else contextlib.nullcontext()
        )
        with ctx.failsafe.altitude_hold_window(
            ctx.params.governor.climb_authority
        ), yaw_scope, ctx.perception.session(
            conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
        ):
            try:
                # The yaw scan never translates, so neither the pad-edge guard
                # nor the departure settle applies to it.
                if not simple and getattr(ctx.params.platform, "edge_ahead_m", None) is not None:
                    halted = self._settle_before_departure(ctx, rate, profile, shaper)
                    if halted is not None:
                        return halted
                    # The search window is for searching; the settle has its
                    # own ceiling.
                    deadline = Deadline(timeout)
                return self._cruise_until_confirmed(
                    ctx, deadline, rate, profile, shaper, confirmer, cruise_mps, timeout
                )
            finally:
                logger.info("Search loop cadence: %s.", rate.cadence_report())

    @staticmethod
    def _assert_search_tilt(ctx: MissionContext) -> None:
        """Command the search gimbal angle on entry, absorbing any fault.

        The ground station can re-enter this stage from Stage 4 or 5, which
        leave the mount at the framing or return tilt. The sweep geometry and the
        view band assume ``gimbal.search_tilt_deg``, and nothing on this airframe
        acknowledges a gimbal command, so the angle is asserted once before any
        translation rather than inherited from the takeoff publish.

        A gimbal fault is logged at warning level and does not abort the sweep.
        """
        tilt = float(ctx.params.gimbal.search_tilt_deg)
        try:
            ctx.drone.camera_control(tilt=tilt, pan=0.0)
        except Exception as exc:  # noqa: BLE001 - a gimbal fault must not abort the sweep
            logger.warning("Gimbal command to %.1f deg failed: %s", tilt, exc)
        ctx.current_tilt_deg = tilt

    # ------------------------------------------------------- pad departure

    def _settle_before_departure(
        self,
        ctx: MissionContext,
        rate: LoopRate,
        profile: JerkLimitedProfile,
        shaper: QuantizedCommandShaper,
    ) -> Optional[StepStatus]:
        """Hold station on the pad until the altitude settles on the relative target.

        The firmware ends its launch profile about 1.0 m above the launch point
        and the operating height over a pad is lower, so Stage 1 hands over
        with the governor still descending. Crossing the pad edge with that
        error still being corrected mixes the governor's correction with the
        firmware's answer to the sonar step, which field test F-2 has to
        separate. The governor runs through the ordinary :meth:`_command` with
        zero translation until the error has stayed inside
        the larger of the governor deadbands and ``governor.horizontal_throttle_error_m`` for ``platform.departure_settle_window_sec``.
        Reaching ``platform.departure_settle_timeout_sec`` is not a fault: the
        search proceeds, and the governor's horizontal throttle still
        restrains the cruise while the error is large.

        Returns
        -------
        Optional[StepStatus]
            ``None`` to proceed with the search; ``ABORTED`` or ``FAILURE`` when
            the stage must end here.
        """
        platform = ctx.params.platform
        target = ctx.params.relative_target_altitude_m
        governor_cfg = ctx.params.governor
        # The departure waits only on an error the cruise itself would be
        # throttled for. Inside ``horizontal_throttle_error_m`` the governor
        # flies full speed, and the old band (``deadband_m``, 3 cm) could not be
        # reached from below: a 5 cm deficit asks for under one driver count of
        # climb, which the int8 quantization sends as zero, and the airframe
        # sat on the pad until the settle timeout.
        band = max(
            governor_cfg.deadband_m,
            governor_cfg.climb_deadband_m,
            governor_cfg.horizontal_throttle_error_m,
        )
        max_age = ctx.params.vision.perception_max_age_sec
        settle_deadline = Deadline(platform.departure_settle_timeout_sec)
        inside_sec = 0.0
        error = float("nan")
        last_generation = 0
        settled = False

        while settle_deadline.active:
            if ctx.interrupted():
                self._halt(ctx, profile, shaper, rate.period_sec, rate)
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            dt = rate.tick()
            # The video watchdog is fed by fresh frames, not by the loop.
            sample = ctx.perception.get_latest(max_age)
            if sample is not None and sample.generation != last_generation:
                last_generation = sample.generation
                ctx.failsafe.notify_frame_received()

            self._command(ctx, profile, shaper, dt, 0.0)
            error = ctx.odom_supervisor.snapshot().relative_altitude - target
            inside_sec = inside_sec + dt if abs(error) <= band else 0.0
            if inside_sec >= platform.departure_settle_window_sec:
                settled = True
                break

        logger.info(
            "[PLATFORM departure_settle err=%+.3f elapsed=%.2f]", error, settle_deadline.elapsed_sec
        )
        if not settled:
            logger.warning(
                "Departure settle window of %.1f s expired %.3f m off the %.2f m relative "
                "target; departing under the altitude-hold throttle.",
                platform.departure_settle_timeout_sec,
                error,
                target,
            )
        return None

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
            chosen = select_target(ctx, targets)

            snapshot = ctx.odom_supervisor.snapshot()
            ctx.perception.set_status(
                f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 2: SEARCH "
                f"({deadline.elapsed_sec:.1f}s/{timeout:.1f}s) "
                f"| TILT: {ctx.current_tilt_deg:.1f}deg | ALT: {snapshot.relative_altitude:.2f}m",
            )

            if report.just_confirmed:
                best = chosen
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
                # Hysteresis confirmation, not the first raw candidate, opens
                # the reveal: it latches for the rest of the flight, so a
                # detector blink that the confirmer would release must not be
                # what shows the operator the first box.
                ctx.detection_reveal_enabled = True
                emit_milestone("mission.target_found", self._locate(ctx, best, snapshot))
                detail = (
                    "alvo detectado, centralizando por rotação"
                    if self._mode is MissionMode.SIMPLE
                    else "alvo detectado na pista, iniciando aproximação"
                )
                self._announce("Acidente detectado", detail)
                self._halt(ctx, profile, shaper, rate.period_sec, rate)
                return StepStatus.SUCCESS

            if chosen is not None:
                best = chosen
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
        """Advance to the left along the search track under the jerk-limited profile.

        The demand is scaled by what the altitude loop will tolerate before it
        is profiled, not after. Scaling the shaped command instead would leave
        the profile believing it was still tracking the full cruise, so the jerk
        limit would be measured against a velocity the airframe was never given
        -- and a demand scaled below the driver's quantization floor would be
        truncated to a standstill rather than duty-cycled down to it.

        In the Simple profile the cruise is the yaw scan instead.
        """
        if self._mode is MissionMode.SIMPLE:
            self._yaw_command(ctx, dt, ctx.params.yaw_pattern.scan_vyaw)
            return
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

        In the Simple profile the rotation is ramped to zero instead.
        """
        if self._mode is MissionMode.SIMPLE:
            self._yaw_halt(ctx, rate, dt)
            return
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
        """Profile, convert, shape, and transmit one leftward velocity command.

        The forward channel is the literal ``0.0``: that is the structural
        invariant of the lateral pattern. ``max(0.0, vy)`` stops a sigma-delta
        pulse of the wrong polarity from stepping the drone back to the right.
        The altitude governor receives the magnitude of the lateral demand as
        its horizontal feedforward.

        In the Simple profile this is reached only while a candidate is held,
        so the yaw demand is zero.
        """
        if self._mode is MissionMode.SIMPLE:
            self._yaw_command(ctx, dt, 0.0)
            return
        profiled = profile.step(target_mps, dt)
        vy = shaper.shape(ctx.speed_calibration.to_normalized(profiled, axis="lateral"), dt)
        vz = governed_vz(ctx, 
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=abs(vy)
        )
        cross = cross_axis_correction(self, ctx, max(0.0, vy), dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(cross, max(0.0, vy))
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

    # -------------------------------------------------------------- yaw scan

    def _begin_yaw_scan(self, ctx: MissionContext) -> None:
        """Arm the scan ramp and record the heading Stage 4 will restore.

        Stage 1 records the heading at the end of the climb. A run that starts
        at this stage (``--stages 2,...``) has none, so it is taken here, before
        the first rotation.

        Parameters
        ----------
        ctx : MissionContext
            Mission context; ``ctx.blackboard`` is optional.
        """
        self._yaw_ramp = YawRateLimiter(ctx.params.yaw_pattern.scan_ramp_per_sec)
        blackboard = getattr(ctx, "blackboard", None)
        if blackboard is not None and getattr(blackboard, "initial_yaw_rad", None) is None:
            blackboard.initial_yaw_rad = float(ctx.odom_supervisor.snapshot().yaw)

    def _yaw_command(self, ctx: MissionContext, dt: float, target_vyaw: float) -> None:
        """Ramp toward ``target_vyaw`` and transmit it with every translation channel at zero.

        The governor still runs, so altitude hold is unchanged; its horizontal
        feedforward is zero because the airframe does not translate.

        Parameters
        ----------
        ctx : MissionContext
            Mission context.
        dt : float
            Control period, seconds.
        target_vyaw : float
            Yaw-rate demand, normalized; ``>= 0.0`` turns left.
        """
        ramp = self._yaw_ramp
        if ramp is None:
            ramp = self._yaw_ramp = YawRateLimiter(ctx.params.yaw_pattern.scan_ramp_per_sec)
        vyaw = ramp.step(target_vyaw, dt)
        vz = governed_vz(
            ctx,
            ctx.odom_supervisor.snapshot().relative_altitude,
            dt,
            vx_commanded=0.0,
            yaw_commanded=abs(vyaw),
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, vyaw)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, 0.0)
        ctx.drone.move_velocity(vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw)

    def _yaw_halt(self, ctx: MissionContext, rate: Optional[LoopRate], dt: float) -> None:
        """Decelerate the rotation to exactly zero, paced like the translational halt.

        The Bebop latches the last Twist, so the loop always transmits at least
        one command and ends on ``vyaw == 0.0``. The final zero is followed by
        one paced cycle before returning: the Twist crosses DDS asynchronously,
        and a stage that returned on the publish would let the next stage's
        first output overtake it at the consumer, so the last Twist observed
        at a stage boundary would be the penultimate ramp sample.

        Parameters
        ----------
        ctx : MissionContext
            Mission context.
        rate : Optional[LoopRate]
            Loop pacing between commands; ``None`` publishes back to back.
        dt : float
            Control period, seconds.
        """
        yaw_cfg = ctx.params.yaw_pattern
        per_cycle = max(yaw_cfg.scan_ramp_per_sec * max(dt, 1e-3), 1e-6)
        cycles = int(math.ceil(yaw_cfg.scan_vyaw / per_cycle)) + 2
        for _ in range(cycles):
            self._yaw_command(ctx, dt, 0.0)
            ramp = self._yaw_ramp
            if ramp is None or ramp.value == 0.0:
                break
            if abs(ramp.value) <= 1e-9:
                ramp.reset()
                self._yaw_command(ctx, dt, 0.0)
                break
            if rate is not None:
                rate.tick()
        else:
            ramp = self._yaw_ramp
            if ramp is not None:
                ramp.reset()
            self._yaw_command(ctx, dt, 0.0)
        if rate is not None:
            rate.tick()

    @staticmethod
    def _locate(
        ctx: MissionContext, target: "Detection", snapshot: OdometrySnapshot
    ) -> Dict[str, Any]:
        """Describe where the confirmed target lies, for the narration payload.

        Uses the same exact pinhole projection as the Stage 3 guidance law, so
        the spoken position agrees with the one the approach then flies. The
        geometry is best-effort: a projection that is unavailable (ray near
        the horizon, no altitude yet) or a configuration the intrinsics refuse
        leaves the angular and metric fields as ``None`` rather than dropping
        the milestone.

        Returns
        -------
        Dict[str, Any]
            ``class_name``, ``confidence``, ``center_px``, and, when the
            geometry resolves, ``bearing_deg`` (positive right of the nose),
            ``forward_m`` and ``lateral_m`` (body frame, FLU) and
            ``ground_range_m``.
        """
        center = (float(target.center[0]), float(target.center[1]))
        payload: Dict[str, Any] = {
            "class_name": target.class_name,
            "confidence": round(float(target.confidence), 3),
            "center_px": [round(center[0], 1), round(center[1], 1)],
            "bearing_deg": None,
            "forward_m": None,
            "lateral_m": None,
            "ground_range_m": None,
        }
        try:
            intrinsics = CameraIntrinsics(
                width=int(ctx.frame_width),
                height=int(ctx.frame_height),
                horizontal_fov_deg=ctx.params.vision.horizontal_fov_deg,
                vertical_fov_deg=ctx.params.vision.vertical_fov_deg,
            )
            theta_x, _ = intrinsics.bearing(center)
            payload["bearing_deg"] = round(math.degrees(theta_x), 1)
            # The target lies on the floor; on a pad the relative altitude is
            # measured from the pad top.
            height = getattr(snapshot, "height_above_floor_m", snapshot.relative_altitude)
            projection = project_to_ground(intrinsics, center, height, ctx.current_tilt_deg)
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug("Target geometry unavailable for the milestone: %s", exc)
            return payload
        if projection is not None:
            payload["forward_m"] = round(projection.forward_m, 2)
            payload["lateral_m"] = round(projection.lateral_m, 2)
            payload["ground_range_m"] = round(projection.ground_range_m, 2)
        return payload

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


ForwardSearchStep = LateralSearchStep
