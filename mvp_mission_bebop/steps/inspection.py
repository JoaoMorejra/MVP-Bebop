"""Stage 4: absolute hover, gimbal-only framing, and forensic evidence capture.

The airframe holds an absolute hover for the whole stage: every Twist it
transmits carries ``vx = vy = vyaw = 0.0`` and only the altitude governor's
``vz``. The gimbal is the only actuator that moves. In the lateral flight pattern
the drone never advances on the target, so no fixed nadir attitude frames it;
the stage first points the optical axis at the target with the closed-loop
framing law of :mod:`mvp_mission_bebop.controllers.gimbal_framing`, bounded to
``[gimbal.nadir_tilt_deg, gimbal.search_tilt_deg]``, and only then verifies
stillness and captures.

The framing starts from the open-loop pointing solution of the last pixel Stage
3 measured at its lock. If no detection arrives while the gimbal frames, that
solution is kept -- never the floor -- and the capture proceeds flagged as
unsettled.

The capture is only worth as much as the hover beneath it. A 14-megapixel frame
taken while the airframe is still translating is motion-blurred at exactly the
scale that matters for reading a plate or a marking, so this stage verifies
stillness statistically before triggering, rather than taking a single velocity
sample at face value.

Verification is bounded: if the drone never settles within the window the
capture happens anyway, because an imperfect photograph of the scene is worth
more than none at all. The metadata records which of the two paths was taken.

The whole stage runs inside an altitude-hold window. That is not a translation
phase, so it might look unnecessary -- but the airframe arrives here having just
decelerated out of the approach, and the sink that deceleration leaves behind
does not stop at the moment the velocity command does. Without the window the
hover would settle wherever the transient left it and photograph the scene from
there, which is the same failure the translation phases had, arriving a stage
later.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Final, Optional, Tuple

from mvp_mission_bebop.blackboard import EvidenceRecord
from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.controllers.gimbal_framing import FramingCommand, GimbalFramingController
from mvp_mission_bebop.engine.rate import MAX_INTERVAL_SEC, Deadline, LoopRate
from mvp_mission_bebop.estimation.convergence import SettlementCriteria, SettlementDetector
from mvp_mission_bebop.mission_mode import MissionMode
from mvp_mission_bebop.perception.inspection_worker import InspectionJob
from mvp_mission_bebop.perception.worker import detector_kwargs
from mvp_mission_bebop.steps.base import BaseStep, StepStatus, governed_vz, select_target
from mvp_mission_bebop.telemetry.milestones import emit_milestone

logger = logging.getLogger("Step4Inspection")

_STEP_NAMES: Final[Dict[MissionMode, str]] = {
    MissionMode.ADVANCED: "STEP 4: Motionless Nadir Hover & Evidence Capture",
    MissionMode.SIMPLE: "STEP 4: Motionless Nadir Hover & Evidence Capture",
}

#: Gimbal tilt held into a landing in place (``rtl.enabled`` false), in
#: degrees. The marker fields, ``rtl.camera_tilt_deg`` included, are hidden from
#: the operator without the return, so a stored value must not decide where the
#: camera points; near-nadir keeps the touchdown spot in view.
STOW_GIMBAL_TILT_DEG: Final[float] = -80.0

#: Smallest tilt change worth transmitting, in degrees. Below it the gimbal
#: command is not re-sent, so a converged law does not flood the gimbal topic.
_TILT_DEADBAND_DEG: Final[float] = 1e-3


#: Bound on the wait for a suspended nudge to ramp to zero before the capture,
#: seconds: the overlay releases in ``nudge.release_sec`` (0.15 s by default).
_NUDGE_RELEASE_WAIT_SEC: Final[float] = 1.0


def nudge_active(ctx: Any) -> bool:
    """Whether the operator's nudge added anything to the last transmitted command.

    Parameters
    ----------
    ctx : MissionContext
        Mission context; ``nudge`` is optional.

    Returns
    -------
    bool
        ``True`` while the overlay's applied nudge is non-zero.
    """
    nudge = getattr(ctx, "nudge", None)
    snapshot = getattr(nudge, "snapshot", None)
    if not callable(snapshot):
        return False
    try:
        applied = snapshot().applied
    except Exception:  # noqa: BLE001 - an unreadable overlay counts as inactive
        return False
    return any(value != 0.0 for value in applied)


class _StageInterrupted(Exception):
    """Ends the framing phase with an abort or a health failure.

    Raised rather than returned so the altitude-hold window and the perception
    session both close on the way out, as in Stage 3.
    """

    def __init__(self, status: StepStatus) -> None:
        super().__init__(status.name)
        self.status = status


class NadirInspectionStep(BaseStep):
    """Holds an absolute hover, frames the target with the gimbal, records evidence.

    Parameters
    ----------
    mode : MissionMode
        Mission profile, keyword-only. Selects the stage name; ``ADVANCED`` (the
        default) is the historical behaviour.
    """

    def __init__(self, *, mode: MissionMode = MissionMode.ADVANCED) -> None:
        #: Native photo still awaiting the aircraft's PictureEventChanged.
        self._pending_ack: Optional[Dict[str, Any]] = None
        self._mode = mode
        super().__init__(_STEP_NAMES[mode])

    @property
    def mode(self) -> MissionMode:
        """Mission profile this stage was built for."""
        return self._mode

    def execute(self, ctx: MissionContext) -> StepStatus:
        logger.info("--- [%s] ---", self.name)

        # A degraded approach is the case where evidence is most valuable, not
        # least. The stage used to return immediately when the approach had not
        # confirmed its lock -- which is precisely the outcome Stage 3 reports
        # on any timeout or lost target -- so the common failure produced no
        # imagery at all and still logged success. Capture anyway, from wherever
        # the airframe got to, and record in the evidence metadata that the
        # alignment was unconfirmed so nothing downstream mistakes it for a
        # centred inspection.
        approach_finished = bool(ctx.blackboard.approach_finished)
        if not approach_finished:
            logger.warning(
                "Approach never confirmed the lateral lock. Inspecting in degraded mode: "
                "evidence will be captured and flagged as unaligned."
            )

        self._announce(
            "Inspecionando acidente", "drone pairado, apontando a câmera para o acidente"
        )

        try:
            with ctx.failsafe.altitude_hold_window(ctx.params.governor.climb_authority):
                return self._inspect(ctx, approach_finished)
        except _StageInterrupted as interrupt:
            return interrupt.status

    # ------------------------------------------------------------- inspection

    def _inspect(self, ctx: MissionContext, approach_finished: bool) -> StepStatus:
        """Frame, settle, capture, and hold. Called inside the altitude-hold window."""
        if not approach_finished:
            logger.warning(
                "Stage 3 centering timed out after %.1f s. Capturing photo immediately "
                "at present position and continuing mission.",
                15.0,
            )
            self._hold(ctx)
            self._capture(ctx, settled=False, forced=True)
            self._start_background_native_ack(ctx)
            evidence = getattr(ctx.blackboard, "evidence", None)
            if evidence is not None and getattr(evidence, "captured", False):
                self._announce("Registro concluído", "registro fotográfico do acidente concluído")
                logger.info(
                    "Stage 4 complete (forced capture on centering timeout). Evidence: raw=%s annotated=%s metadata=%s",
                    evidence.raw_path,
                    evidence.annotated_path,
                    evidence.metadata_path,
                )
            else:
                logger.error("Stage 4 finished without recording any evidence.")

            self._lower_gimbal_for_stage_five(ctx)
            return StepStatus.SUCCESS

        framed = self._frame_with_gimbal(ctx)
        degraded = not framed

        settled = self._await_stillness(ctx)
        if ctx.interrupted():
            # ``_await_stillness`` returns False both on timeout and on an
            # emergency, and the caller could not tell them apart -- so an abort
            # raised during the settle wait was followed by a full blocking
            # capture before anything acted on it.
            logger.warning("Emergency raised during the settle wait; abandoning evidence capture.")
            self._hold(ctx)
            return StepStatus.ABORTED

        settled = settled and not degraded
        captured = self._capture(ctx, settled)

        evidence = getattr(ctx.blackboard, "evidence", None)
        evidence_confirmed = bool(
            captured and evidence is not None and getattr(evidence, "captured", False)
        )

        if not evidence_confirmed:
            logger.warning("Primary capture did not confirm evidence; engaging hover watch window.")
            status = self._hover(ctx, already_captured=False, exit_on_capture=True)
            if status is not StepStatus.SUCCESS:
                return status

            evidence = getattr(ctx.blackboard, "evidence", None)
            evidence_confirmed = bool(evidence is not None and getattr(evidence, "captured", False))
            if not evidence_confirmed:
                logger.warning(
                    "No evidence was recorded during the hover window; attempting a final capture."
                )
                self._capture(ctx, settled, forced=True)

        self._start_background_native_ack(ctx)

        if ctx.blackboard.evidence.captured:
            self._announce("Registro concluído", "registro fotográfico do acidente concluído")
            logger.info(
                "Stage 4 complete. Evidence: raw=%s annotated=%s metadata=%s",
                ctx.blackboard.evidence.raw_path,
                ctx.blackboard.evidence.annotated_path,
                ctx.blackboard.evidence.metadata_path,
            )
        else:
            logger.error("Stage 4 finished without recording any evidence.")

        self._lower_gimbal_for_stage_five(ctx)
        return StepStatus.SUCCESS

    # --------------------------------------------------------------- framing

    def _frame_with_gimbal(self, ctx: MissionContext) -> bool:
        """Point the gimbal at the target while the airframe holds still.

        Every cycle transmits station keeping (``vx = vy = vyaw = 0.0``), checks
        for an abort and, outside ``no_fly``, for system health, as Stage 3 does.
        The gimbal is first carried toward the open-loop pointing solution of
        the last pixel Stage 3 measured. Each new frame that carries the target
        runs the closed-loop law and replaces that setpoint with the open-loop
        solution of the new pixel; every other cycle, including frames without
        the target, keeps slewing toward the newest setpoint. Each cycle moves
        the tilt by at most ``max_slew_limit_deg * dt``.

        The phase ends when the law reports the target framed, or when the
        window ``max(inspection.settle_timeout_sec, 2 * (search_tilt_deg -
        nadir_tilt_deg) / max_slew_limit_deg)`` expires: twice the time to slew
        across the whole band, and never shorter than the stillness wait. On
        timeout the gimbal rests on the open-loop solution of the newest pixel
        (the Stage 3 one if no detection arrived), never blindly on the floor.

        The perception session is closed before returning: ``ROSCam`` serves new
        frames to one consumer only, and the capture that follows grabs its own.

        Parameters
        ----------
        ctx : MissionContext
            Mission context; ``ctx.current_tilt_deg`` is updated with every tilt
            transmitted.

        Returns
        -------
        bool
            True when the target was framed inside the window.

        Raises
        ------
        _StageInterrupted
            On an abort (``ABORTED``) or a failed health check (``FAILURE``).
        """
        gimbal_cfg = ctx.params.gimbal
        vision_cfg = ctx.params.vision
        frame_dims = (int(ctx.frame_width), int(ctx.frame_height))
        try:
            controller = GimbalFramingController(gimbal_cfg, vision_cfg)
        except (TypeError, ValueError) as exc:
            logger.error(
                "Gimbal framing unavailable (%s); capturing at the current tilt %.1f deg.",
                exc,
                ctx.current_tilt_deg,
            )
            return False
        controller.reset()

        tilt_span = abs(float(gimbal_cfg.search_tilt_deg) - float(gimbal_cfg.nadir_tilt_deg))
        if tilt_span < 1e-3:
            logger.info(
                "Fixed tilt configured (%.1f deg): gimbal will remain stationary during inspection.",
                float(gimbal_cfg.search_tilt_deg),
            )
            return True

        goal = self._open_loop_goal(ctx, controller, frame_dims)
        window_sec = max(
            float(ctx.params.inspection.settle_timeout_sec),
            2.0 * tilt_span / float(gimbal_cfg.max_slew_limit_deg),
        )
        deadline = Deadline(window_sec)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        max_age = vision_cfg.perception_max_age_sec
        last_generation = 0
        target_seen = False

        logger.info(
            "Framing with the gimbal alone: open-loop setpoint %.1f deg, band [%.1f, %.1f] deg, "
            "window %.1f s.",
            goal,
            controller.min_tilt_deg,
            controller.max_tilt_deg,
            window_sec,
        )

        try:
            with ctx.perception.session(
                conf=vision_cfg.confidence_threshold, imgsz=vision_cfg.inference_imgsz
            ):
                while deadline.active:
                    self._hold(ctx)
                    if ctx.interrupted():
                        raise _StageInterrupted(StepStatus.ABORTED)
                    if not ctx.drone.no_fly:
                        healthy, reason = ctx.failsafe.evaluate_system_health()
                        if not healthy:
                            ctx.failsafe.trigger_emergency_land(reason)
                            raise _StageInterrupted(StepStatus.FAILURE)

                    # One slew budget per cycle: exactly one of the two tilt
                    # updates below runs, each limited to max_slew_limit_deg * dt.
                    dt = min(rate.tick(), MAX_INTERVAL_SEC)
                    sample = ctx.perception.get_latest(max_age)
                    if sample is None or sample.generation == last_generation:
                        self._point_gimbal(
                            ctx, controller.slew_toward(ctx.current_tilt_deg, goal, dt)
                        )
                        continue
                    last_generation = sample.generation
                    ctx.failsafe.notify_frame_received()

                    targets = sample.result.filter_by_class(vision_cfg.target_classes)
                    center: Optional[Tuple[float, float]] = None
                    best = select_target(ctx, targets)
                    if best is not None:
                        center = (float(best.center[0]), float(best.center[1]))
                        target_seen = True

                    command = controller.compute(center, frame_dims, ctx.current_tilt_deg, dt)
                    if center is not None:
                        # The airframe is frozen and the target static, so the
                        # pointing solution of the newest pixel stays valid after
                        # the target is lost; it replaces the Stage 3 setpoint.
                        goal = controller.open_loop_tilt(center, ctx.current_tilt_deg, frame_dims)
                        self._point_gimbal(ctx, command.tilt_deg)
                    else:
                        self._point_gimbal(
                            ctx, controller.slew_toward(ctx.current_tilt_deg, goal, dt)
                        )
                    self._publish_framing(ctx, command, deadline)
                    if command.framed:
                        logger.info(
                            "Target framed after %.1f s at tilt %.1f deg (%s).",
                            deadline.elapsed_sec,
                            ctx.current_tilt_deg,
                            command.note,
                        )
                        return True
                    if (
                        not getattr(ctx.blackboard, "approach_finished", True)
                        and deadline.elapsed_sec >= 1.0
                        and abs(ctx.current_tilt_deg - goal) <= 1.0
                    ):
                        logger.info(
                            "Degraded framing: gimbal reached setpoint %.1f deg after %.1f s. "
                            "Proceeding to capture.",
                            ctx.current_tilt_deg,
                            deadline.elapsed_sec,
                        )
                        return False
        finally:
            logger.info("Framing loop cadence: %s.", rate.cadence_report())

        logger.warning(
            "Target not framed within %.1f s; keeping tilt %.1f deg (%s). The capture is "
            "flagged as unsettled.",
            window_sec,
            ctx.current_tilt_deg,
            "open-loop solution of the last detection"
            if target_seen
            else "open-loop solution of the Stage 3 pixel",
        )
        return False

    @staticmethod
    def _open_loop_goal(
        ctx: MissionContext, controller: GimbalFramingController, frame_dims: Tuple[int, int]
    ) -> float:
        """Starting setpoint: open-loop pointing solution, falling back to nadir_tilt_deg.

        Falls back to nadir_tilt_deg when Stage 3 recorded no pixel or a
        corrupt one, driving the gimbal toward the configured capture tilt.
        """
        pixel = ctx.blackboard.confirmed_target_px
        tilt = ctx.blackboard.confirmed_tilt_deg
        if pixel is None or tilt is None:
            logger.info(
                "No confirmed target pixel from Stage 3; framing starts toward nadir tilt %.1f deg.",
                float(ctx.params.gimbal.nadir_tilt_deg),
            )
            return float(ctx.params.gimbal.nadir_tilt_deg)
        try:
            return controller.open_loop_tilt(pixel, tilt, frame_dims)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Confirmed target pixel %r unusable (%s); framing starts toward nadir tilt %.1f deg.",
                pixel,
                exc,
                float(ctx.params.gimbal.nadir_tilt_deg),
            )
            return float(ctx.params.gimbal.nadir_tilt_deg)

    @staticmethod
    def _point_gimbal(ctx: MissionContext, tilt_deg: float) -> None:
        """Transmit a tilt when it differs from the one last commanded."""
        if abs(tilt_deg - ctx.current_tilt_deg) > _TILT_DEADBAND_DEG:
            ctx.drone.camera_control(tilt=tilt_deg, pan=0.0)
            ctx.current_tilt_deg = tilt_deg

    @staticmethod
    def _publish_framing(ctx: MissionContext, command: FramingCommand, deadline: Deadline) -> None:
        """Caption the annotated stream with the framing state."""
        err_text = (
            f"{command.vertical_error_px:+.0f}px"
            if math.isfinite(command.vertical_error_px)
            else "n/a"
        )
        ctx.perception.set_status(
            f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: GIMBAL FRAMING "
            f"({deadline.elapsed_sec:.1f}s) | TILT: {command.tilt_deg:.1f}deg "
            f"| ERR_Y: {err_text}{' | FLOOR' if command.at_floor else ''}",
        )

    # -------------------------------------------------------------- stillness

    def _await_stillness(self, ctx: MissionContext) -> bool:
        """Wait until motion is statistically negligible, or the window expires."""
        cfg = ctx.params.inspection
        detector = SettlementDetector(
            SettlementCriteria(
                window_sec=cfg.settle_window_sec,
                min_samples=cfg.settle_min_samples,
                max_speed=cfg.settle_max_speed_mps,
                max_position_sigma=cfg.settle_max_position_sigma_m,
                max_vertical_speed=cfg.settle_max_vertical_speed,
            )
        )
        settle_timeout = (
            min(cfg.settle_timeout_sec, 2.0)
            if not getattr(ctx.blackboard, "approach_finished", True)
            else cfg.settle_timeout_sec
        )
        deadline = Deadline(settle_timeout)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        elapsed = 0.0
        report = None

        logger.info(
            "Verifying motionlessness: speed <= %.3f m/s, position sigma <= %.3f m, "
            "commanded vz <= %.3f, sustained for %.1f s.",
            cfg.settle_max_speed_mps,
            cfg.settle_max_position_sigma_m,
            cfg.settle_max_vertical_speed,
            cfg.settle_window_sec,
        )

        while deadline.active:
            if ctx.interrupted():
                return False

            commanded_vz = self._hold(ctx)
            snapshot = ctx.odom_supervisor.snapshot()
            dt = rate.tick()
            elapsed += dt

            if nudge_active(ctx):
                # The operator is moving the airframe: stillness measured now
                # would be measured against motion the operator is commanding.
                # The statistics restart once the keys are released.
                detector = SettlementDetector(detector.criteria)
                continue

            report = detector.update(
                x=snapshot.x,
                y=snapshot.y,
                speed=snapshot.speed,
                vz=commanded_vz,
                timestamp=elapsed,
            )
            if report.settled:
                logger.info("Hover confirmed motionless after %.1f s: %s", elapsed, report)
                return True

        logger.warning(
            "Hover did not settle within %.1f s (%s). Capturing anyway.",
            cfg.settle_timeout_sec,
            report,
        )
        return False

    # ---------------------------------------------------------------- capture

    def _capture(self, ctx: MissionContext, settled: bool, *, forced: bool = False) -> bool:
        """Trigger the onboard snapshot and persist local evidence.

        Station keeping is re-commanded first. The capture that follows is
        synchronous and takes appreciable time -- a blocking frame grab, a YOLO
        inference, an uncompressed PNG write measured at ~155 ms for an 856x480
        frame, a quality-100 JPEG and a JSON sidecar -- and the Bebop latches the
        last Twist it received until another arrives. Whatever the hold loop last
        sent would otherwise stay open-loop for that whole window, and the anti-
        climb governor's descent command is the thing most likely to be latched.
        """
        blackboard = getattr(ctx, "blackboard", None)
        if blackboard is not None:
            blackboard.nudge_suspended = True
        try:
            self._await_nudge_release(ctx)
            return self._capture_unnudged(ctx, settled, forced=forced)
        finally:
            if blackboard is not None:
                blackboard.nudge_suspended = False

    def _await_nudge_release(self, ctx: MissionContext) -> None:
        """Hold station until the suspended nudge has ramped to zero (bounded).

        The permit is already false (``blackboard.nudge_suspended``), so the
        overlay is releasing on its own ramp; this only waits for it, at the
        control rate, never longer than ``_NUDGE_RELEASE_WAIT_SEC``.
        """
        if not nudge_active(ctx):
            return
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        deadline = Deadline(_NUDGE_RELEASE_WAIT_SEC)
        while deadline.active and nudge_active(ctx):
            self._hold(ctx)
            rate.tick()
        self._hold(ctx)

    def _capture_unnudged(self, ctx: MissionContext, settled: bool, *, forced: bool = False) -> bool:
        """The capture proper, with the nudge suspended (see :meth:`_capture`)."""
        self._hold(ctx)
        frame = ctx.grab_frame(timeout_sec=1.5)
        if frame is None:
            logger.error("No frame available for evidence capture.")
            return False

        ctx.failsafe.notify_frame_received()

        # The 14 MP photo is requested before the local inference so the
        # aircraft's capture and its PictureEventChanged overlap the detector
        # instead of following it; the frame above and this photo are then
        # the same instant to within the request latency.
        tracker = ctx.picture_ack
        mark = tracker.sequence if tracker is not None else 0
        requested_at = time.monotonic()
        native_photo: Dict[str, Any] = {
            "requested_at_utc": datetime.now(timezone.utc).isoformat(),
            "acknowledged": False,
            "event": None,
            "error": None,
            "remote_path": None,
            "local_path": None,
        }
        ctx.drone.snapshot()

        # Still synchronous: this inference draws the annotated evidence JPEG
        # and decides target_confirmed for capture_done. The victim assessment
        # of the same frame runs later, off this thread (_submit_assessment).
        nadir_conf = ctx.params.inspection.nadir_confidence_threshold
        result = ctx.detector.detect(
            frame, **detector_kwargs(nadir_conf, ctx.params.vision.inference_imgsz)
        )
        targets = result.filter_by_class(ctx.params.vision.target_classes)

        annotated = ctx.publish_annotated_stream(
            frame,
            result,
            f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: EVIDENCE CAPTURE "
            f"| {'SETTLED' if settled else 'UNSETTLED'} | TARGETS: {len(targets)}",
        )

        # Not waited for here: measured on the aircraft, the TAKEN arrives 2.5 s
        # after the request, and blocking for it would leave the last Twist
        # latched on the Bebop. An acknowledgement already in is recorded now;
        # a later one is resolved by the hover loop (_resolve_native_ack).
        if tracker is not None:
            ack = tracker.wait_after(mark, 0.0)
            if ack is not None:
                native_photo.update(acknowledged=ack["acknowledged"], event=ack["event"], error=ack["error"])

        record = ctx.record_photographic_evidence(
            raw_frame=frame,
            annotated_frame=annotated if annotated is not None else frame,
            detections=list(targets),
            snapshot=ctx.odom_supervisor.snapshot(),
            native_photo=native_photo,
        )
        ctx.blackboard.evidence = record
        self._pending_ack = None
        if tracker is not None and native_photo["event"] is None and record.metadata_path:
            self._pending_ack = {
                "tracker": tracker,
                "mark": mark,
                "requested_at": requested_at,
                "native_photo": native_photo,
                "metadata_path": record.metadata_path,
            }
        elif native_photo["event"] is not None and not native_photo["acknowledged"]:
            logger.warning("Native photo refused by the aircraft (error=%s).", native_photo["error"])
        if record.captured:
            emit_milestone(
                "mission.capture_done",
                {
                    "raw_image": os.path.basename(record.raw_path) if record.raw_path else None,
                    "metadata": (
                        os.path.basename(record.metadata_path) if record.metadata_path else None
                    ),
                    "native_photo": bool(native_photo["acknowledged"]),
                    "settled": bool(settled),
                    "target_confirmed": bool(ctx.blackboard.target_confirmed),
                },
            )
        # Submitted after capture_done, so the station always sees
        # mission.inspection_ready follow it.
        if record.captured and record.raw_path:
            self._submit_assessment(ctx, frame, record)
        return record.captured

    @staticmethod
    def _submit_assessment(ctx: MissionContext, frame: Any, record: EvidenceRecord) -> None:
        """Hand the captured frame to the background victim inference.

        A frame copy and a non-blocking enqueue: the capture never waits for
        the result, which is settled during the remaining hover, the return
        and the landing (``perception/inspection_worker.py``). A failure here
        loses the assessment, never the capture; the report then states
        nothing about the victim.
        """
        worker = getattr(ctx, "inspection_worker", None)
        if worker is None:
            return
        if not record.timestamp:
            logger.warning("Capture without timestamp; victim assessment skipped.")
            return
        try:
            worker.submit(InspectionJob.create(record.timestamp, frame, record.metadata_path))
        except Exception as exc:  # noqa: BLE001 - the assessment is never flight-critical
            logger.error("Victim assessment not submitted: %s", exc)

    def _hover(
        self,
        ctx: MissionContext,
        already_captured: bool,
        *,
        exit_on_capture: bool = False,
    ) -> StepStatus:
        """Hold the nadir hover for the configured duration.

        Perception runs only while evidence is still missing. The detector used
        to be called on every cycle of the hover, including the whole remainder
        of the window after the capture had already been recorded -- a CPU
        inference per cycle producing results nothing consumed. Once
        ``captured`` holds, the pipeline is disengaged and the loop reduces to
        station keeping: ``_hold`` and ``rate.tick``, nothing else.

        The frame heartbeat stops being refreshed with it. That is safe because
        this loop never evaluates system health, and Stage 5 re-arms the
        heartbeat on entry before its first health check.

        A capture triggered from inside the hover needs the camera and detector
        to itself -- ``ROSCam`` serves new frames to one consumer only -- so the
        pipeline is disengaged before it, and re-engaged only if it failed.
        """
        duration = ctx.params.kinematics.hover_duration_sec
        nadir_conf = ctx.params.inspection.nadir_confidence_threshold
        imgsz = ctx.params.vision.inference_imgsz
        max_age = ctx.params.vision.perception_max_age_sec
        deadline = Deadline(duration)
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        captured = already_captured
        last_generation = 0

        if captured:
            logger.info(
                "Holding nadir hover for %.1f s; evidence already recorded, vision suspended.",
                duration,
            )
        else:
            logger.info("Holding nadir hover for %.1f s, watching for a capture...", duration)
            ctx.perception.engage(conf=nadir_conf, imgsz=imgsz)

        try:
            while deadline.active:
                if ctx.interrupted():
                    return StepStatus.ABORTED

                self._hold(ctx)
                rate.tick()
                self._resolve_native_ack(ctx)

                if captured:
                    continue

                sample = ctx.perception.get_latest(max_age)
                if sample is None or sample.generation == last_generation:
                    continue
                last_generation = sample.generation
                ctx.failsafe.notify_frame_received()

                targets = sample.result.filter_by_class(ctx.params.vision.target_classes)
                ctx.perception.set_status(
                    f"{'[NO-FLY] ' if ctx.drone.no_fly else ''}STEP 4: NADIR HOVER "
                    f"({deadline.elapsed_sec:.1f}s/{duration:.1f}s) | TARGETS: {len(targets)}",
                )

                if targets:
                    ctx.perception.disengage()
                    captured = self._capture(ctx, settled=True)
                    if captured:
                        logger.info(
                            "Evidence recorded %.1f s into the hover; vision suspended for "
                            "the remaining %.1f s.",
                            deadline.elapsed_sec,
                            deadline.remaining_sec,
                        )
                        evidence = getattr(ctx.blackboard, "evidence", None)
                        if exit_on_capture and bool(evidence is not None and getattr(evidence, "captured", False)):
                            logger.info("Evidence confirmed captured; exiting hover immediately.")
                            break
                    else:
                        ctx.perception.engage(conf=nadir_conf, imgsz=imgsz)
        finally:
            ctx.perception.disengage()
            logger.info("Hover loop cadence: %s.", rate.cadence_report())

        return StepStatus.SUCCESS

    def _lower_gimbal_for_stage_five(self, ctx: MissionContext) -> None:
        """Point the gimbal for Stage 5 once the evidence is recorded (Simple profile).

        The airframe keeps the heading it reached when the target was locked:
        Stage 5 lands in place, or centres on the pad marker from that heading,
        so no yaw is commanded here and the stage hands over immediately. The
        tilt is ``rtl.camera_tilt_deg`` with the return and
        :data:`STOW_GIMBAL_TILT_DEG` without it. A gimbal band of zero width
        (``search_tilt_deg == nadir_tilt_deg``) is a fixed camera and is left
        untouched.

        Parameters
        ----------
        ctx : MissionContext
            Mission context; ``ctx.current_tilt_deg`` is updated with the tilt
            transmitted.
        """
        if self._mode is not MissionMode.SIMPLE:
            return
        gimbal = ctx.params.gimbal
        if abs(float(gimbal.search_tilt_deg) - float(gimbal.nadir_tilt_deg)) < 1e-3:
            return
        tilt = float(ctx.params.rtl.camera_tilt_deg) if ctx.params.rtl.enabled else STOW_GIMBAL_TILT_DEG
        logger.info("Directing gimbal to %.1f deg for Stage 5; no heading restore.", tilt)
        self._point_gimbal(ctx, tilt)

    # ----------------------------------------------------------- native ACK

    def _resolve_native_ack(self, ctx: MissionContext, *, final: bool = False) -> bool:
        """Record the native photo's acknowledgement once it is in or overdue.

        Non-blocking. The sidecar written by the capture says ``acknowledged:
        false``; it is rewritten when PictureEventChanged arrives, or when
        ``inspection.native_photo_ack_timeout_sec`` has passed since the
        request (or ``final``) without one. Returns True while one is pending.
        """
        pending = self._pending_ack
        if pending is None:
            return False
        native = pending["native_photo"]
        ack = pending["tracker"].wait_after(pending["mark"], 0.0)
        overdue = time.monotonic() - pending["requested_at"] >= ctx.params.inspection.native_photo_ack_timeout_sec
        if ack is None and not overdue and not final:
            return True
        if ack is not None:
            native.update(acknowledged=ack["acknowledged"], event=ack["event"], error=ack["error"])
        native["ack_ms"] = (
            int((time.monotonic() - pending["requested_at"]) * 1000.0) if ack is not None else None
        )
        self._pending_ack = None
        if native["acknowledged"]:
            logger.info("Native photo acknowledged by the aircraft after %d ms.", native["ack_ms"])
        else:
            logger.warning(
                "Native photo not acknowledged by the aircraft (event=%s, error=%s).",
                native["event"],
                native["error"],
            )
        try:
            ctx.update_evidence_metadata(pending["metadata_path"], {"native_photo": dict(native)})
        except Exception as exc:  # noqa: BLE001 - evidence bookkeeping never ends the flight
            logger.error("Could not update the evidence sidecar with the native photo ACK: %s", exc)
        return False

    def _start_background_native_ack(self, ctx: MissionContext) -> None:
        """Resolve native photo ACK in background so aircraft does not idle in hover."""
        if self._pending_ack is None:
            return
        if not self._resolve_native_ack(ctx):
            return

        import threading

        def _watch_ack() -> None:
            timeout = ctx.params.inspection.native_photo_ack_timeout_sec
            rate = LoopRate(10.0)
            deadline = Deadline(timeout)
            while deadline.active:
                rate.tick()
                if self._pending_ack is None or not self._resolve_native_ack(ctx):
                    return
            self._resolve_native_ack(ctx, final=True)

        thread = threading.Thread(target=_watch_ack, name="NativePhotoAckWatcher", daemon=True)
        thread.start()

    def _settle_native_ack(self, ctx: MissionContext) -> None:
        """Wait out a pending acknowledgement, still station keeping.

        Bounded by the acknowledgement window counted from the request; an
        emergency ends the wait at once and records what is known.
        """
        if self._pending_ack is None:
            return
        rate = LoopRate(ctx.params.kinematics.control_loop_hz)
        while self._resolve_native_ack(ctx):
            if ctx.interrupted():
                self._resolve_native_ack(ctx, final=True)
                return
            self._hold(ctx)
            rate.tick()

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _hold(ctx: MissionContext, dt: Optional[float] = None) -> float:
        """Command a stationary hover with the altitude governor engaged.

        Returns the commanded vertical speed (post-clamp), in normalized
        units, so callers that need to know whether the vertical axis is
        still actively correcting -- see :meth:`_await_stillness` -- do not
        have to recompute it.
        """
        vz = governed_vz(ctx, ctx.odom_supervisor.snapshot().relative_altitude, dt)
        safe_vz, safe_vyaw = ctx.failsafe.clamp_kinematics(vz, 0.0)
        ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=safe_vz, vyaw=safe_vyaw)
        return safe_vz

    @staticmethod
    def _announce(action: str, detail: str) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
