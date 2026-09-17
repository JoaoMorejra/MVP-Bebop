"""Stage 3: image-based visual servoing and coupled approach guidance.

The step drives the phase machine and the perception loop; the control law lives
in :mod:`mvp_mission_bebop.controllers.visual_servoing`.

Target loss is handled in two stages. An alpha-beta filter carries the estimate
forward through short dropouts, so servoing continues against a prediction
rather than freezing on the last command. When the coast horizon expires the
step hands over to the controller's reacquisition law instead of ending the
approach.

That second stage exists because losing the target near the inspection attitude
is the expected case, not an anomaly: the ground footprint shrinks, the object is
foreshortened into an aspect ratio the detector never trained on, and the
airframe's own shadow falls on it. The step used to break out of its loop at that
point and report an unfinished approach -- discarding a target the drone was
directly on top of, at the moment it had almost arrived. Now it holds position,
sweeps the gimbal back up, and resumes the phase it was in once the target is
back.

The stage ends when the gimbal reaches the inspection attitude and the airframe
has come to rest under it. Both halves are checked: a stage that declared itself
finished while the drone was still translating would hand Stage 4 an airframe it
then has to wait out before it can photograph anything, and the freeze is the
thing the inspection is framed around.

Altitude is held two-sided throughout, which is new. Translation costs the Bebop
lift, and this is the stage that translates hardest and longest; against the old
descent-only governor the resulting sink was the one error the vertical axis had
no authority to answer, so the drone arrived over the scene low. The stage opens
an altitude-hold window for its whole duration and throttles its own forward
demand whenever that loop is losing -- translating less hard is the cheapest
correction available, because translation is the disturbance.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Sequence, Tuple

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.visual_servoing import ServoCommand, TrackingPhase
from mvp_mission_bebop.engine.rate import Deadline, LoopRate
from mvp_mission_bebop.estimation.target_tracker import ConstantVelocityTracker, TrackerGains
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    import numpy as np
    from nectar.ai.detection.core.types import Detection, DetectionResult

logger = logging.getLogger("Step3Tracking")

class _StageInterrupted(Exception):
    """Ends the servo loop with a status other than "it converged or it didn't".

    The loop returns a boolean because that is the only thing its caller needs
    in the ordinary case. An abort and a health failure are not ordinary cases
    and must not be encoded as "did not converge": the mission has to stop, not
    continue to Stage 4 with an unaligned approach. Raising is what carries that
    out through the ``with`` block, so the altitude-hold window closes on the way
    past rather than being left open by an early return.
    """

    def __init__(self, status: StepStatus) -> None:
        super().__init__(status.name)
        self.status = status


class VisualServoingStep(BaseStep):
    """Centres, approaches, and settles over the confirmed target."""

    def __init__(self) -> None:
        super().__init__("STEP 3: Visual Servoing & Coupled Guidance")

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
            "Visual servoing engaged: %s geometry, tilt %.1f -> %.1f deg, "
            "corridor %.0f px, window %.1f s.",
            "pinhole" if vision_cfg.ibvs_enabled else "open-loop (IBVS disabled)",
            ctx.params.gimbal.search_tilt_deg,
            ctx.params.gimbal.nadir_tilt_deg,
            vision_cfg.optical_center_tolerance_px * vision_cfg.approach_corridor_ratio,
            timeouts.tracking_timeout_sec,
        )

        deadline = Deadline(timeouts.tracking_timeout_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        approach_finished = False

        try:
            with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
                approach_finished = self._servo(ctx, deadline, rate, tracker)
        except _StageInterrupted as interrupt:
            return interrupt.status

        self._hold(ctx)
        ctx.blackboard.approach_finished = approach_finished

        if not approach_finished:
            logger.warning(
                "Approach ended without confirmed nadir alignment after %.1f s.",
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
        """Run the perception and servoing loop. Returns whether it converged.

        Split out of :meth:`execute` so the altitude-hold window is a scope
        rather than a flag: whatever ends this loop -- convergence, the
        deadline, an abort, an exception -- the window closes behind it and the
        vertical axis is descent-only again before the next stage runs.
        """
        vision_cfg = ctx.params.vision
        controller = ctx.visual_controller
        # Read once per stage: the operator's dwell setting governs when the
        # approach is declared finished, and reading it from the config here is
        # what makes the parameter sheet authoritative over this decision.
        alignment_dwell_cycles = max(1, int(vision_cfg.alignment_dwell_cycles))
        approach_finished = False
        aligned_cycles = 0
        lost_cycles = 0

        while deadline.active:
            if ctx.emergency_event.is_set():
                self._hold(ctx)
                raise _StageInterrupted(StepStatus.ABORTED)

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    raise _StageInterrupted(StepStatus.FAILURE)

            frame = ctx.grab_frame(timeout_sec=1.0)
            dt = rate.tick()

            if frame is None:
                self._hold(ctx)
                continue

            ctx.failsafe.notify_frame_received()
            result = ctx.detector.detect(frame, conf=vision_cfg.confidence_threshold)
            targets = result.filter_by_class(vision_cfg.target_classes)

            center, measured = self._resolve_target(
                targets, tracker, dt, (ctx.frame_width, ctx.frame_height)
            )
            if center is None:
                lost_cycles += 1
                if lost_cycles < vision_cfg.lost_frames_tolerance:
                    # Still inside the tracker's coast horizon: hold and let the
                    # extrapolation do its job before escalating.
                    self._hold(ctx, dt)
                    ctx.publish_annotated_stream(
                        frame, result, f"STEP 3: TARGET LOST ({lost_cycles} frames)"
                    )
                    continue

                # Coasting has run out. Recover rather than abandon.
                command = controller.note_target_lost(ctx.current_tilt_deg, dt)
                self._apply(ctx, command, dt)
                self._publish(ctx, frame, result, command, deadline.elapsed_sec, False)
                if command.phase is TrackingPhase.NADIR and command.nadir_aligned:
                    aligned_cycles += 1
                    if aligned_cycles >= alignment_dwell_cycles:
                        logger.info(
                            "Nadir station keeping held over accident scene. Approach complete."
                        )
                        approach_finished = True
                        break
                else:
                    aligned_cycles = 0
                if command.recovery_exhausted:
                    break
                continue

            if measured:
                lost_cycles = 0

            snapshot = ctx.odom_supervisor.snapshot()
            command = controller.compute(
                target_center=center,
                frame_dimensions=(ctx.frame_width, ctx.frame_height),
                current_tilt_deg=ctx.current_tilt_deg,
                relative_altitude_m=snapshot.relative_altitude,
                dt=dt,
                speed_scale=self._horizontal_scale(ctx),
            )

            self._apply(ctx, command, dt)
            self._publish(ctx, frame, result, command, deadline.elapsed_sec, measured)

            if command.phase is TrackingPhase.NADIR:
                if self._nadir_dwell_earned(ctx, command):
                    aligned_cycles += 1
                    if aligned_cycles >= alignment_dwell_cycles:
                        logger.info(
                            "Inspection attitude held for %d cycles at %.1f deg "
                            "(%.1f px error, airframe %s). Approach complete.",
                            aligned_cycles,
                            command.tilt_deg,
                            command.pixel_error,
                            "frozen" if command.nadir_frozen else "aligned",
                        )
                        approach_finished = True
                        break
                else:
                    aligned_cycles = 0
            else:
                aligned_cycles = 0

        return approach_finished

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _nadir_dwell_earned(ctx: MissionContext, command: ServoCommand) -> bool:
        """Does this cycle count toward the dwell that ends the approach?

        Once the controller has committed to inspecting from here, the only
        thing that counts is that the airframe has actually stopped. Counting
        pixel alignment instead would end the stage while the drone was still
        shedding the approach velocity -- and Stage 4 opens by verifying
        motionlessness statistically, so every metre per second left over is
        time the capture spends waiting for a transient this stage should have
        absorbed.

        Before the commitment -- a nadir hold that is still tracking, which is
        what a controller driven straight into the phase produces -- the older
        evidence stands: the target is inside the nadir window, or the gimbal is
        at the attitude with the target near enough the centre.
        """
        if command.nadir_committed:
            return command.nadir_frozen

        gimbal_cfg = ctx.params.gimbal
        at_attitude = (
            ctx.current_tilt_deg
            <= gimbal_cfg.nadir_tilt_deg + gimbal_cfg.nadir_tilt_tolerance_deg
        )
        return command.nadir_aligned or (
            at_attitude
            and command.pixel_error
            <= ctx.params.vision.optical_center_tolerance_px
            * ctx.params.vision.nadir_arrival_tolerance_factor
        )

    @staticmethod
    def _horizontal_scale(ctx: MissionContext) -> float:
        """Fraction of the along-track demand the altitude loop permits.

        Reaching for the governor through ``getattr`` because the descent-only
        governor is still a supported configuration and predates the method;
        a missing one means no throttling, which is exactly right for a
        controller that cannot act on a sink anyway.
        """
        scale = getattr(ctx.governor, "horizontal_scale", None)
        return float(scale()) if callable(scale) else 1.0

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
            best = max(targets, key=lambda detection: detection.confidence)
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
        """Drive the gimbal and transmit the velocity command."""
        if abs(command.tilt_deg - ctx.current_tilt_deg) > 1e-3:
            ctx.drone.camera_control(tilt=command.tilt_deg, pan=0.0)
            ctx.current_tilt_deg = command.tilt_deg

        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        safe_vx, safe_vy = ctx.failsafe.clamp_translation(command.vx, command.vy)
        ctx.drone.move_velocity(
            vx=safe_vx, vy=safe_vy, vz=safe_vz, vyaw=safe_vyaw
        )

    @staticmethod
    def _hold(ctx: MissionContext, dt: Optional[float] = None) -> None:
        """Command station keeping, keeping the anti-climb governor engaged."""
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)

    @staticmethod
    def _publish(
        ctx: MissionContext,
        frame: "np.ndarray",
        result: "DetectionResult",
        command: ServoCommand,
        elapsed_sec: float,
        measured: bool,
    ) -> None:
        """Overlay servoing state onto the annotated stream."""
        range_text = (
            f"{command.ground_range_m:.2f}m" if command.ground_range_m is not None else "n/a"
        )
        source = "" if measured else " [PREDICTED]"
        ctx.publish_annotated_stream(
            frame,
            result,
            f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 3: "
            f"{command.phase.value.upper()}{source} ({elapsed_sec:.1f}s) "
            f"| TILT: {command.tilt_deg:.1f}deg | RANGE: {range_text} "
            f"| ERR: {command.pixel_error:.0f}px | ALIGN: {command.alignment:.2f}",
        )
