"""Stage 3: closed-loop lateral servoing onto the confirmed target, ending in a freeze.

The step drives the perception loop and the actuator boundary; the control law
lives in :mod:`mvp_mission_bebop.controllers.visual_servoing`.

In the lateral flight pattern the drone does not advance on the target. It
nulls the horizontal pixel error with the body-frame lateral channel alone,
transmits ``vx = 0.0`` on every cycle, and leaves the gimbal at the search tilt
Stage 2 confirmed the target with: the range is constant, so the target stays in
frame vertically, and the gimbal belongs to Stage 4.

Target loss is handled in two stages. An alpha-beta filter carries the estimate
forward through short dropouts, so servoing continues against a prediction
rather than freezing on the last command; predicted frames steer but never earn
the credit that freezes the airframe. When the coast horizon expires the step
hands over to the controller's reacquisition law, which creeps laterally toward
the side the target left from, instead of ending the stage.

The stage ends on the cycle the controller latches its freeze: the target
measured inside the centring gate for ``vision.alignment_dwell_cycles`` frames
with the horizontal ground speed inside ``inspection.settle_max_speed_mps``. That
cycle transmits station keeping immediately and records the last measured pixel
and the tilt on the blackboard, which Stage 4 uses as its open-loop pointing
solution if the target is lost while it frames.

Altitude is held two-sided throughout. Lateral translation costs the Bebop lift
exactly as forward translation does -- the thrust vector tilts either way -- so
the stage opens an altitude-hold window for its whole duration and feeds the
magnitude of the lateral demand to the governor as its feedforward.

The Simple mission profile replaces the lateral law with a pixel-error law on
yaw: ``ctx.visual_controller`` is the ``YawServoingController`` that
``mission.py`` injects for that profile, and its ``vyaw`` is transmitted with
``vx == vy == 0.0`` inside a yaw authority window (``failsafe.yaw_window``)
that the stage opens for its whole duration. Every exit -- lock, deadline,
exhausted reacquisition, abort, fault -- leaves the Bebop, which latches the
last Twist, on ``vyaw == 0.0``. The other profiles keep ``vyaw == 0.0``
throughout.
"""

from __future__ import annotations

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, Dict, Final, Optional, Sequence, Tuple

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.visual_servoing import ServoCommand
from mvp_mission_bebop.engine.rate import MAX_INTERVAL_SEC, Deadline, LoopRate
from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker, TrackerGains
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.steps.base import BaseStep, StepStatus, governed_vz
from mvp_mission_bebop.telemetry.milestones import emit_milestone

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    from nectar.ai.detection.core.types import Detection

logger = logging.getLogger("Step3Tracking")

_STEP_NAMES: Final[Dict[MissionMode, str]] = {
    MissionMode.ADVANCED: "STEP 3: Visual Servoing & Coupled Guidance",
    MissionMode.SIMPLE: "STEP 3: Yaw Servoing & Coupled Guidance",
}


class _StageInterrupted(Exception):
    """Ends the servo loop with a status other than "it converged or it didn't".

    The loop returns a boolean because that is the only thing its caller needs
    in the ordinary case. An abort and a health failure are not ordinary cases
    and must not be encoded as "did not converge": the mission has to stop, not
    continue to Stage 4 with an unfrozen airframe. Raising is what carries that
    out through the ``with`` block, so the altitude-hold window closes on the way
    past rather than being left open by an early return.
    """

    def __init__(self, status: StepStatus) -> None:
        super().__init__(status.name)
        self.status = status


class VisualServoingStep(BaseStep):
    """Centres the confirmed target laterally and freezes over the track.

    Parameters
    ----------
    mode : MissionMode
        Mission profile, keyword-only. Selects the stage name; ``ADVANCED`` (the
        default) is the historical behaviour.
    """

    def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:
        self._mode = mode
        super().__init__(_STEP_NAMES[mode])

    @property
    def mode(self) -> MissionMode:
        """Mission profile this stage was built for."""
        return self._mode

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        if not ctx.blackboard.target_confirmed:
            logger.warning("No target was confirmed during search. Skipping visual servoing.")
            ctx.blackboard.approach_finished = False
            return StepStatus.SUCCESS

        vision_cfg = ctx.params.vision
        timeouts = ctx.params.timeouts
        controller = ctx.visual_controller
        controller.reset()

        tracker = ConstantVelocityTracker(
            TrackerGains(), max_coast_sec=timeouts.target_recovery_timeout_sec
        )

        logger.info(
            "Lateral servo engaged: tilt held at %.1f deg, gate %.0f px, dwell %d frames, "
            "stillness %.2f m/s, window %.1f s.",
            ctx.current_tilt_deg,
            max(
                vision_cfg.optical_center_tolerance_px,
                vision_cfg.optical_center_tolerance_ratio * ctx.frame_width,
            ),
            max(1, int(vision_cfg.alignment_dwell_cycles)),
            ctx.params.inspection.settle_max_speed_mps,
            timeouts.tracking_timeout_sec,
        )

        emit_milestone(
            "mission.approaching",
            {"window_sec": timeouts.tracking_timeout_sec, "ibvs": bool(vision_cfg.ibvs_enabled)},
        )
        deadline = Deadline(timeouts.tracking_timeout_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        approach_finished = False

        try:
            yaw_scope = (
                ctx.failsafe.yaw_window(ctx.params.yaw_pattern.max_vyaw)
                if self._mode is MissionMode.SIMPLE
                else contextlib.nullcontext()
            )
            with ctx.failsafe.altitude_hold_window(
                ctx.params.governor.climb_authority
            ), yaw_scope, ctx.perception.session(
                conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
            ):
                approach_finished = self._servo(ctx, deadline, rate, tracker)
        except _StageInterrupted as interrupt:
            return interrupt.status
        except BaseException:
            if self._mode is MissionMode.SIMPLE:
                self._release_yaw(ctx)
            raise
        finally:
            logger.info("Servo loop cadence: %s.", rate.cadence_report())

        self._hold(ctx)
        if self._mode is MissionMode.SIMPLE:
            # Pace the exit so the closing zero Twist reaches the consumer before Stage 4 starts.
            rate.tick()
        ctx.blackboard.approach_finished = approach_finished

        if not approach_finished:
            logger.warning(
                "Stage 3 ended without a lateral lock after %.1f s.",
                deadline.elapsed_sec,
            )

        return StepStatus.SUCCESS

    # ------------------------------------------------------------- servo loop

    def _servo(
        self,
        ctx: MissionContext,
        deadline: Deadline,
        rate: LoopRate,
        tracker: ConstantVelocityTracker,
    ) -> bool:
        """Run the perception and servoing loop. Returns whether the freeze latched.

        Split out of :meth:`execute` so the altitude-hold window is a scope
        rather than a flag: whatever ends this loop -- the lock, the deadline,
        an exhausted recovery, an abort, an exception -- the window closes
        behind it and the vertical axis is descent-only again before the next
        stage runs.

        The dwell that ends the stage lives in the controller
        (``vision.alignment_dwell_cycles``); the step supplies the two facts the
        controller cannot observe itself: whether the centroid was measured in
        this frame, and whether the airframe is at rest.
        """
        vision_cfg = ctx.params.vision
        controller = ctx.visual_controller
        settle_max_speed_mps = ctx.params.inspection.settle_max_speed_mps
        max_age = vision_cfg.perception_max_age_sec
        lost_cycles = 0
        last_generation = 0
        last_measured_px: Optional[Tuple[float, float]] = None
        # Time since the controller last ran. The servoing law, the tracker and
        # every frame counter inside the controller (centring credit,
        # reacquisition hits) are defined per observation, so they advance once
        # per new frame with the interval that actually separates two frames --
        # as they did when the loop ran at the inference rate. Between frames
        # the loop re-transmits the latched
        # command, which keeps the altitude hold, the failsafe clamps and the
        # abort check at the full control rate without counting any observation
        # twice.
        pending_dt = 0.0
        latched: Optional[ServoCommand] = None

        while deadline.active:
            if ctx.interrupted():
                self._hold(ctx)
                raise _StageInterrupted(StepStatus.ABORTED)

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    raise _StageInterrupted(StepStatus.FAILURE)

            dt = rate.tick()
            pending_dt += dt
            sample = ctx.perception.get_latest(max_age)

            if sample is None:
                latched = None
                self._hold(ctx)
                continue

            if sample.generation == last_generation:
                if latched is None:
                    self._hold(ctx, dt)
                else:
                    self._apply(ctx, latched, dt)
                continue

            last_generation = sample.generation
            frame_dt = min(pending_dt, MAX_INTERVAL_SEC)
            pending_dt = 0.0
            ctx.failsafe.notify_frame_received()
            targets = sample.result.filter_by_class(vision_cfg.target_classes)

            center, measured = self._resolve_target(
                targets, tracker, frame_dt, (ctx.frame_width, ctx.frame_height)
            )
            if center is None:
                lost_cycles += 1
                if lost_cycles < vision_cfg.lost_frames_tolerance:
                    # Still inside the tracker's coast horizon: hold and let the
                    # extrapolation do its job before escalating.
                    latched = None
                    self._hold(ctx, frame_dt)
                    ctx.perception.set_status(f"STEP 3: TARGET LOST ({lost_cycles} frames)")
                    continue

                # Coasting has run out. Recover rather than abandon.
                command = controller.note_target_lost(ctx.current_tilt_deg, frame_dt)
                latched = command
                self._apply(ctx, command, frame_dt)
                self._publish(ctx, command, deadline.elapsed_sec, False, float("nan"))
                if command.recovery_exhausted:
                    break
                continue

            if measured:
                lost_cycles = 0
                last_measured_px = self._best_detection(targets).center

            snapshot = ctx.odom_supervisor.snapshot()
            command = controller.compute(
                target_center=center,
                frame_dimensions=(ctx.frame_width, ctx.frame_height),
                current_tilt_deg=ctx.current_tilt_deg,
                relative_altitude_m=self._height_above_target(snapshot),
                dt=frame_dt,
                measured=measured,
                airframe_at_rest=self._airframe_at_rest(snapshot, settle_max_speed_mps),
            )
            err_x = float(center[0]) - ctx.frame_width / 2.0

            if command.locked:
                # The horizontal freeze is transmitted in the lock cycle itself:
                # every cycle spent re-sending a stale lateral command after the
                # decision is translation Stage 4's stillness check must wait out.
                self._hold(ctx, frame_dt)
                self._publish(ctx, command, deadline.elapsed_sec, measured, err_x)
                if last_measured_px is not None:
                    ctx.blackboard.confirmed_target_px = (
                        float(last_measured_px[0]),
                        float(last_measured_px[1]),
                    )
                ctx.blackboard.confirmed_tilt_deg = ctx.current_tilt_deg
                logger.info(
                    "Lateral lock after %.1f s (err %+.0f px, tilt %.1f deg). Stage 3 complete.",
                    deadline.elapsed_sec,
                    err_x,
                    ctx.current_tilt_deg,
                )
                return True

            latched = command
            self._apply(ctx, command, frame_dt)
            self._publish(ctx, command, deadline.elapsed_sec, measured, err_x)
            if command.recovery_exhausted:
                # Reacquisition can also run out on frames that carry an
                # unconfirmed observation; the window is the same either way.
                break

        return False

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _airframe_at_rest(snapshot: Any, settle_max_speed_mps: float) -> bool:
        """Is the horizontal ground speed inside the stillness threshold?

        Same threshold Stage 4's stillness verification applies, so a freeze
        latched here is one the inspection does not then have to wait out. Read
        through ``getattr`` like the rest of this step reads the snapshot: a
        snapshot without the field carries no velocity at all, and the freeze
        then falls back to the centring dwell alone.

        Parameters
        ----------
        snapshot : Any
            Odometry snapshot, normally an ``OdometrySnapshot``.
        settle_max_speed_mps : float
            ``inspection.settle_max_speed_mps``.

        Returns
        -------
        bool
            True when the speed is unknown or at most the threshold.
        """
        speed = getattr(snapshot, "horizontal_speed", None)
        if speed is None:
            return True
        return float(speed) <= settle_max_speed_mps

    @staticmethod
    def _height_above_target(snapshot: Any) -> float:
        """Height above the plane the target lies on, for the ground projection.

        ``relative_altitude`` is measured from the launch point, which on a
        pad is the pad top; the target lies on the floor. Read through
        ``getattr`` so a snapshot without the field falls back to the relative
        altitude, which is the same number when no pad is configured.
        """
        height = getattr(snapshot, "height_above_floor_m", None)
        return float(height) if height is not None else float(snapshot.relative_altitude)

    @staticmethod
    def _best_detection(targets: Sequence["Detection"]) -> "Detection":
        """The detection the servo follows: the most confident one in the frame."""
        return max(targets, key=lambda detection: detection.confidence)

    @staticmethod
    def _resolve_target(
        targets: Sequence["Detection"],
        tracker: ConstantVelocityTracker,
        dt: float,
        frame_dimensions: Tuple[int, int],
    ) -> Tuple[Optional[Tuple[float, float]], bool]:
        """Return the target centre and whether it came from a real detection.

        A coasted estimate is additionally required to still lie inside the
        frame. The tracker's own ``trustworthy`` flag is a statement about
        elapsed coast time and nothing else, so an extrapolation that has walked
        off the edge of the image still reported as usable -- and the servoing
        law would then dutifully chase a position no camera can see. Time alone
        is the wrong horizon; leaving the sensor's field of view is what actually
        ends the track's validity.
        """
        if targets:
            best = VisualServoingStep._best_detection(targets)
            estimate = tracker.update(best.center, dt)
            return estimate.center, True

        estimate = tracker.coast(dt)
        if estimate is None or not estimate.trustworthy:
            return None, False

        width, height = frame_dimensions
        if not (0.0 <= estimate.x <= width and 0.0 <= estimate.y <= height):
            logger.warning(
                "Coasted estimate left the frame at (%.0f, %.0f); treating the target as lost.",
                estimate.x,
                estimate.y,
            )
            return None, False
        return estimate.center, False

    def _apply(self, ctx: MissionContext, command: ServoCommand, dt: float) -> None:
        """Transmit the lateral command. The gimbal is not commanded in this stage.

        The along-track channel is transmitted as a literal ``0.0`` rather than
        read from the command, so no defect upstream can turn this stage into an
        approach. The lateral channel is bilateral -- the servo corrects to
        either side -- and only saturated onto the envelope. The governor's
        feedforward is the norm of the horizontal demand, which with ``vx``
        structurally zero is ``|vy|``.

        The lateral law emits ``vyaw == 0.0`` and the other profiles transmit
        a literal ``0.0`` on that channel. The yaw law of the Simple profile
        emits its turn here, limited by the yaw authority
        (``failsafe.yaw_window``); its lateral channel is the literal ``0.0``,
        so the profile never translates and the governor's feedforward is zero.
        ``vx`` stays the literal ``0.0`` in every profile.

        Parameters
        ----------
        ctx : MissionContext
            Mission context.
        command : ServoCommand
            Output of ``ctx.visual_controller``.
        dt : float
            Control period, seconds.
        """
        simple = self._mode is MissionMode.SIMPLE
        lateral = 0.0 if simple else command.vy
        yaw = command.vyaw if simple else 0.0
        vz = governed_vz(ctx, 
            ctx.odom_supervisor.snapshot().relative_altitude, dt, vx_commanded=abs(lateral)
        )
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, yaw)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(0.0, lateral)
        ctx.drone.move_velocity(
            vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw
        )

    @staticmethod
    def _release_yaw(ctx: MissionContext) -> None:
        """Leave the airframe on an all-zero Twist after a fault in the Simple profile.

        The Bebop latches the last Twist, so a fault raised between a yaw
        command and the stage's closing hold would leave the airframe turning.
        The command bypasses the governor and the failsafe clamps -- either may
        be what raised -- because an all-zero Twist already satisfies every
        invariant they enforce. A failure to transmit is logged and does not
        mask the original fault.

        Parameters
        ----------
        ctx : MissionContext
            Mission context.
        """
        try:
            ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
        except Exception as exc:  # noqa: BLE001 - the original fault must propagate
            logger.critical("Zero Twist after a Stage 3 fault was not transmitted: %s", exc)

    @staticmethod
    def _hold(ctx: MissionContext, dt: Optional[float] = None) -> None:
        """Command station keeping, keeping the anti-climb governor engaged."""
        vz = governed_vz(ctx, ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)

    @staticmethod
    def _publish(
        ctx: MissionContext,
        command: ServoCommand,
        elapsed_sec: float,
        measured: bool,
        err_x_px: float,
    ) -> None:
        """Caption the annotated stream with the servoing state.

        The ``STEP 3:`` prefix and the ``ERR:`` field are read by the ground
        station and are kept verbatim; ``ERR_X`` is the horizontal error the
        lateral loop actually nulls.
        """
        range_text = (
            f"{command.ground_range_m:.2f}m" if command.ground_range_m is not None else "n/a"
        )
        source = "" if measured else " [PREDICTED]"
        err_x_text = f"{err_x_px:+.0f}px" if math.isfinite(err_x_px) else "n/a"
        ctx.perception.set_status(
            f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 3: "
            f"{command.phase.value.upper()}{source} ({elapsed_sec:.1f}s) "
            f"| TILT: {command.tilt_deg:.1f}deg | RANGE: {range_text} "
            f"| ERR: {command.pixel_error:.0f}px | ERR_X: {err_x_text} "
            f"| ALIGN: {command.alignment:.2f}",
        )
