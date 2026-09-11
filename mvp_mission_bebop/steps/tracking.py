"""Stage 3: image-based visual servoing and coupled approach guidance.

The step drives the phase machine and the perception loop; the control law lives
in :mod:`mvp_mission_bebop.controllers.visual_servoing`.

Target loss is handled by extrapolation rather than by a blind hold. The
previous implementation froze its last command and waited for the target to
reappear on its own, which gives up exactly the information the tracker has:
where the target was going. An alpha-beta filter carries the estimate forward
through short dropouts, so servoing continues against a prediction until the
coast horizon expires.
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

#: Consecutive aligned cycles required before the approach is declared finished.
_ALIGNMENT_DWELL_CYCLES: int = 8


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
        aligned_cycles = 0
        lost_cycles = 0
        approach_finished = False

        while deadline.active:
            if ctx.emergency_event.is_set():
                self._hold(ctx)
                return StepStatus.ABORTED

            if not ctx.drone.no_fly:
                healthy, reason = ctx.failsafe.evaluate_system_health()
                if not healthy:
                    ctx.failsafe.trigger_emergency_land(reason)
                    return StepStatus.FAILURE

            frame = ctx.handler.take_photo(timeout_sec=1.0)
            dt = rate.tick()

            if frame is None:
                self._hold(ctx)
                continue

            ctx.failsafe.notify_frame_received()
            result = ctx.detector.detect(frame, conf=vision_cfg.confidence_threshold)
            targets = result.filter_by_class(vision_cfg.target_classes)

            center, measured = self._resolve_target(targets, tracker, dt)
            if center is None:
                lost_cycles += 1
                self._hold(ctx)
                ctx.publish_annotated_stream(
                    frame, result, f"STEP 3: TARGET LOST ({lost_cycles} frames)"
                )
                if lost_cycles >= vision_cfg.lost_frames_tolerance:
                    logger.warning(
                        "Target unrecoverable after %.1f s of extrapolation. Ending approach.",
                        timeouts.target_recovery_timeout_sec,
                    )
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
            )

            self._apply(ctx, command)
            self._publish(ctx, frame, result, command, deadline.elapsed_sec, measured)

            if command.phase is TrackingPhase.NADIR and command.nadir_aligned:
                aligned_cycles += 1
                if aligned_cycles >= _ALIGNMENT_DWELL_CYCLES:
                    logger.info(
                        "Nadir alignment held for %d cycles (%.1f px error). Approach complete.",
                        aligned_cycles,
                        command.pixel_error,
                    )
                    approach_finished = True
                    break
            else:
                aligned_cycles = 0

        self._hold(ctx)
        ctx.blackboard.approach_finished = approach_finished

        if not approach_finished:
            logger.warning(
                "Approach ended without confirmed nadir alignment after %.1f s.",
                deadline.elapsed_sec,
            )

        return StepStatus.SUCCESS

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _resolve_target(
        targets: Sequence["Detection"],
        tracker: ConstantVelocityTracker,
        dt: float,
    ) -> Tuple[Optional[Tuple[float, float]], bool]:
        """Return the target centre and whether it came from a real detection."""
        if targets:
            best = max(targets, key=lambda detection: detection.confidence)
            estimate = tracker.update(best.center, dt)
            return estimate.center, True

        estimate = tracker.coast(dt)
        if estimate is None or not estimate.trustworthy:
            return None, False
        return estimate.center, False

    def _apply(self, ctx: MissionContext, command: ServoCommand) -> None:
        """Drive the gimbal and transmit the velocity command."""
        if abs(command.tilt_deg - ctx.current_tilt_deg) > 1e-3:
            ctx.drone.camera_control(tilt=command.tilt_deg, pan=0.0)
            ctx.current_tilt_deg = command.tilt_deg

        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(
            vx=command.vx, vy=command.vy, vz=safe_vz, vyaw=safe_vyaw
        )

    @staticmethod
    def _hold(ctx: MissionContext) -> None:
        """Command station keeping, keeping the anti-climb governor engaged."""
        vz = ctx.governor.compute_vz(ctx.odom_supervisor.snapshot().relative_altitude)
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
            f"| ERR: {command.pixel_error:.0f}px",
        )
