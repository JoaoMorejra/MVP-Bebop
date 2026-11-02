"""Deterministic mission pipeline sequencer."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Callable, Dict, Final, List, Optional

import nectar

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.exit_codes import EXIT_ABORTED_LANDED, mission_exit_code
from mvp_mission_bebop.engine.signals import EmergencyHandler
from mvp_mission_bebop.engine.stage_gate import stage_lock
from mvp_mission_bebop.mission_mode import spoken_stage_five_name
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("MissionRunner")

#: Number of stop-and-land commands transmitted during an emergency. The Bebop
#: subscribes with a queue depth of 1 over best-effort QoS, so a single publish
#: can be dropped; a short burst makes delivery overwhelmingly likely without
#: consuming the time budget.
_EMERGENCY_BURST_COUNT: int = 5

#: Spacing between commands in that burst.
_EMERGENCY_BURST_INTERVAL_SEC: float = 0.04

#: Stage names as the station speaks them in a failure alert. Stage 5 depends
#: on the return plan (``mission_mode.spoken_stage_five_name``).
_SPOKEN_STAGE_NAMES: Final[Dict[int, str]] = {
    1: "Decolagem",
    2: "Varredura",
    3: "Aproximação",
    4: "Inspeção",
}

#: Suffix of the jump marker line, by ``MissionContext.requested_stage_source``.
#: The ``--- [STEP N: name] ---`` part the station matches is unchanged.
_JUMP_SOURCE_LABELS: Final[Dict[str, str]] = {
    "station": "(salto comandado pela estação)",
    "battery": "(retorno forçado por bateria crítica)",
}

#: First stage that presupposes a confirmed target. A run entering it without
#: having confirmed one -- a partial bench run (``--stages 3,4,5``) or a stage
#: jump from the ground station -- opens the detection reveal on entry, or the
#: approach would fly blind on the feed and the Stage 4 annotated evidence
#: would be recorded without its boxes.
_DETECTION_REVEAL_STAGE: Final[int] = 3

#: Longest wait for the victim assessment after a landed mission, in seconds.
#: It normally settled during the return; this covers a slow device only.
_INSPECTION_DRAIN_SEC: Final[float] = 1.0

#: Stages flown inside one continuous altitude-hold window (see
#: :class:`_HoldWindowScope`).
_CONTINUOUS_HOLD_STAGES: Final[frozenset] = frozenset({2, 3, 4})


class _HoldWindowScope:
    """One altitude-hold window held open across Stages 2 to 4.

    Each stage opens its own window around its loops, which left the vertical
    axis descent-only in the gaps between them (stage transitions, station
    keeping between two loops), and the logs showed the authority revoked and
    granted again at every boundary. This scope opens a window bounded by the
    governor's own ``climb_authority`` when the first of Stages 2 to 4 starts
    and closes it when a stage outside them starts or the run ends: Stage 1
    keeps its own climb window, and Stage 5 is excluded so the touchdown
    sequence stays descent-only. The stages' own windows nest inside it
    (``altitude_hold_window`` saves and restores the previous ceiling).
    """

    def __init__(self, ctx: MissionContext) -> None:
        self._ctx = ctx
        self._stack = contextlib.ExitStack()
        self._open = False

    def enter_stage(self, stage: int) -> None:
        """Open or close the window for the stage about to run."""
        failsafe = getattr(self._ctx, "failsafe", None)
        window = getattr(failsafe, "altitude_hold_window", None)
        if not callable(window):
            return
        inside = stage in _CONTINUOUS_HOLD_STAGES
        if inside and not self._open:
            governor = getattr(self._ctx, "governor", None)
            authority = float(getattr(governor, "climb_authority", 0.0) or 0.0)
            self._stack.enter_context(window(authority))
            self._open = True
        elif not inside and self._open:
            self.close()

    def close(self) -> None:
        """Close the window if open; idempotent."""
        self._stack.close()
        self._open = False

#: Longest wait for the inspection worker thread to exit, in seconds.
_INSPECTION_STOP_SEC: Final[float] = 0.5


class MissionRunner:
    """Executes mission steps in order and finalizes resources exactly once."""

    def __init__(
        self,
        context: MissionContext,
        steps: Optional[List[BaseStep]] = None,
        *,
        on_finalize: Optional[Callable[[], None]] = None,
        stage_numbers: Optional[List[int]] = None,
    ) -> None:
        self.ctx = context
        self.steps = steps or []
        self._on_finalize = on_finalize
        #: Stage number of each configured step, as the ground station numbers
        #: them. Needed because a partial run (``--stages 4``) holds one step
        #: whose number is not its index.
        self.stage_numbers = stage_numbers or list(range(1, len(self.steps) + 1))

        # Signal handling is constructed here but installed explicitly, so a
        # runner can be built off the main thread -- in a test, say -- without
        # tripping "signal only works in main thread".
        self._emergency = EmergencyHandler(
            emergency_event=context.emergency_event,
            land_sequence=self._transmit_emergency_landing,
            finalizer=self._finalize,
            exit_code=EXIT_ABORTED_LANDED,
        )

    @property
    def emergency(self) -> EmergencyHandler:
        """The interrupt handler owned by this runner."""
        return self._emergency

    def install_signal_handlers(self) -> None:
        """Register SIGINT and SIGTERM. Must be called from the main thread."""
        self._emergency.install()

    def finalize(self) -> None:
        """Release resources if that has not already happened."""
        self._emergency.finalize_once()

    def exit_code(self, succeeded: bool) -> int:
        """Exit status for a run that returned ``succeeded`` from :meth:`run`.

        See :func:`~mvp_mission_bebop.engine.exit_codes.mission_exit_code`.
        """
        return mission_exit_code(
            succeeded,
            aborted=self._emergency.triggered,
            touchdown_confirmed=getattr(self.ctx.blackboard, "touchdown_confirmed", None),
        )

    # -------------------------------------------------------------- execution

    def run(self) -> bool:
        """Execute every configured step in order.

        Returns
        -------
        bool
            True when every step reported success.
        """
        logger.info("Commencing autonomous mission execution (%d steps).", len(self.steps))
        all_succeeded = True
        index = 0
        entered_by_jump = False
        hold_window = _HoldWindowScope(self.ctx)

        try:
            while index < len(self.steps):
                if self.ctx.emergency_event.is_set():
                    logger.warning("Emergency signal active. Halting step progression.")
                    all_succeeded = False
                    break

                step = self.steps[index]
                alerts_before = self._alerts_raised()
                # Read by the stage gate (engine.stage_gate) from the executor
                # thread that receives the station's goto requests; named under
                # the same lock the gate decides under. A request for this very
                # stage that passed the gate before it was named (the 4 -> 5
                # boundary) is already satisfied; left pending it would
                # interrupt the stage at once and restart it.
                with stage_lock(self.ctx):
                    self.ctx.current_stage = self.stage_numbers[index]
                    # A Stage 5 re-entered after an operator jump chooses its
                    # branch again; a stale commitment would refuse to force it.
                    self.ctx.return_branch_committed = False
                    dropped = self._drop_jump_to(self.stage_numbers[index])
                if dropped:
                    logger.info(
                        "Stage %d requested while it was about to start; request already satisfied.",
                        self.stage_numbers[index],
                    )
                # Read by the steps whose narration presupposes the stage before
                # (``mission.rtl_start``: "inspeção concluída").
                self.ctx.entered_by_jump = entered_by_jump
                self._open_reveal_past_the_search(self.stage_numbers[index])
                hold_window.enter_stage(self.stage_numbers[index])
                status = step.execute(self.ctx)

                # A step that unwound because the operator asked for a different
                # stage did not fail, and must not be reported as though it did.
                # This is checked before the status, because the status it
                # returned is its abort path -- that is how it stops quickly.
                target = self._take_stage_jump()
                if target is not None:
                    index = target
                    entered_by_jump = True
                    continue
                entered_by_jump = False

                if status is StepStatus.ABORTED:
                    logger.warning("Step '%s' signalled ABORT.", step.name)
                    self._announce_failure("Missão abortada", "missão abortada, pousando drone")
                    all_succeeded = False
                    break

                if status is StepStatus.FAILURE:
                    logger.error("Step '%s' failed. Halting mission pipeline.", step.name)
                    # A step or the failsafe that already alerted about this
                    # failure has said the specific thing; a generic "step
                    # failed" after it only delays the next line.
                    already_alerted = self._alerts_raised() > alerts_before or bool(
                        getattr(getattr(self.ctx, "failsafe", None), "failsafe_active", False)
                    )
                    if already_alerted:
                        logger.info("Step failure already alerted; no generic step_failed.")
                    else:
                        stage = self.stage_numbers[index]
                        self._announce_failure(
                            "Falha na etapa",
                            self._spoken_stage_name(stage),
                            priority="CRITICAL",
                            airborne=bool(getattr(getattr(self.ctx, "blackboard", None), "takeoff_committed", False)),
                        )
                    all_succeeded = False
                    break

                index += 1

        except Exception as exc:  # noqa: BLE001 - any step fault must land the drone
            logger.critical("Unhandled exception in mission pipeline: %s", exc, exc_info=True)
            self.ctx.failsafe.trigger_emergency_land(str(exc))
            all_succeeded = False
        finally:
            hold_window.close()

        return all_succeeded

    def _open_reveal_past_the_search(self, stage: int) -> None:
        """Latch the detection reveal open when ``stage`` follows the search."""
        if stage < _DETECTION_REVEAL_STAGE or self.ctx.detection_reveal_enabled:
            return
        logger.info(
            "Stage %d entered without a Stage 2 confirmation; revealing detections.", stage
        )
        self.ctx.detection_reveal_enabled = True

    def _take_stage_jump(self) -> Optional[int]:
        """Consume a pending stage jump and return the step index to run next.

        Returns ``None`` when no jump is pending or the requested stage is not
        one this run holds -- a partial bench run configured with ``--stages 4``
        has nowhere to jump to, and a request naming a stage it does not carry is
        dropped rather than silently redirected to a different one.
        """
        with stage_lock(self.ctx):
            if not self.ctx.stage_jump_event.is_set():
                return None
            requested = self.ctx.requested_stage
            source = getattr(self.ctx, "requested_stage_source", None)
            if requested in self.stage_numbers:
                # Named before the event is cleared, so a duplicate request for
                # the same stage meets the gate with that stage already current.
                self.ctx.current_stage = requested
                # Stage 5 is current from here, before the naming block; a
                # stale commitment would leave a critical request unforced.
                if requested == 5:
                    self.ctx.return_branch_committed = False
            self.ctx.stage_jump_event.clear()
            self.ctx.requested_stage = None

        if requested is None:
            return None
        if requested not in self.stage_numbers:
            logger.warning(
                "Stage jump to %s ignored: this run holds stages %s.",
                requested,
                self.stage_numbers,
            )
            return None

        index = self.stage_numbers.index(requested)
        logger.info(
            "--- [STEP %d: %s] --- %s",
            requested,
            self.steps[index].name,
            _JUMP_SOURCE_LABELS.get(source or "station", _JUMP_SOURCE_LABELS["station"]),
        )
        return index

    def _drop_jump_to(self, stage: int) -> bool:
        """Discard a pending jump to ``stage``, the stage about to run. Caller holds the stage lock.

        Returns
        -------
        bool
            Whether a pending request was discarded.
        """
        if not self.ctx.stage_jump_event.is_set() or self.ctx.requested_stage != stage:
            return False
        self.ctx.stage_jump_event.clear()
        self.ctx.requested_stage = None
        return True

    # ------------------------------------------------------------ termination

    def _spoken_stage_name(self, stage: int) -> str:
        """The stage as the failure alert speaks it; stage 5 by the return plan of the flight."""
        if stage == 5:
            rtl = getattr(getattr(self.ctx, "params", None), "rtl", None)
            return spoken_stage_five_name(bool(getattr(rtl, "enabled", True)))
        return _SPOKEN_STAGE_NAMES.get(stage, f"etapa {stage}")

    def _disarm_nudge(self, reason: str) -> None:
        """Latch the operator's nudge off before any emergency Twist leaves."""
        nudge = getattr(self.ctx, "nudge", None) or getattr(getattr(self.ctx, "drone", None), "nudge", None)
        if nudge is None:
            return
        try:
            nudge.disarm(reason)
        except Exception as exc:  # noqa: BLE001 - the landing proceeds regardless
            logger.critical("Nudge overlay could not be disarmed: %s", exc)

    def _transmit_emergency_landing(self, budget_sec: float) -> None:
        """Transmit a controlled landing within the supplied time budget.

        Never cuts motors: this is a descent command, not a motor kill.
        """
        interval = _EMERGENCY_BURST_INTERVAL_SEC
        affordable = int(budget_sec / interval) if interval > 0.0 else _EMERGENCY_BURST_COUNT
        repeats = max(1, min(_EMERGENCY_BURST_COUNT, affordable))
        self._disarm_nudge("emergency")

        for index in range(repeats):
            self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            self.ctx.drone.land()
            if index < repeats - 1:
                time.sleep(interval)

        logger.info("Emergency landing burst transmitted (%d commands).", repeats)

    def _finalize(self) -> None:
        """Release peripherals and the ROS runtime. Invoked at most once."""
        logger.info("Commencing resource de-allocation and sensor shutdown...")

        self._disarm_nudge("mission end")
        if not self.ctx.blackboard.rtl_completed:
            # The mission did not land on its own, so make sure it is on its way
            # down before the process exits.
            try:
                self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
                self.ctx.drone.land()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Landing command during cleanup failed: %s", exc)

        self._stop_inspection_worker()

        for label, action in (
            ("ImageHandler", self.ctx.handler.cleanup),
            ("drone", self.ctx.drone.cleanup),
        ):
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s cleanup exception: %s", label, exc)

        if self._on_finalize is not None:
            try:
                self._on_finalize()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Finalization hook exception: %s", exc)

        try:
            nectar.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Nectar runtime shutdown exception: %s", exc)

        logger.info("Mission resource cleanup finalized.")

    def _stop_inspection_worker(self) -> None:
        """Settle the victim assessment and stop its thread, bounded.

        Runs after the landing command, so it never delays one. Only a mission
        that landed on its own waits for a pending assessment; an abort or a
        failsafe stops at once, and whatever is pending is settled as
        ``unavailable`` (``timeout``).
        """
        worker = getattr(self.ctx, "inspection_worker", None)
        if worker is None:
            return
        try:
            if self.ctx.blackboard.rtl_completed and not self._emergency.triggered:
                if not worker.drain(_INSPECTION_DRAIN_SEC):
                    logger.warning("Victim assessment still pending after %.1f s.", _INSPECTION_DRAIN_SEC)
                worker.stop(_INSPECTION_STOP_SEC)
            else:
                worker.stop(0.0)
        except Exception as exc:  # noqa: BLE001 - never blocks the teardown
            logger.warning("Inspection worker shutdown exception: %s", exc)

    @staticmethod
    def _alerts_raised() -> int:
        try:
            from mvp_mission_bebop.telemetry.announcer import alerts_raised

            return alerts_raised()
        except Exception:  # noqa: BLE001 - audio is never flight-critical
            return 0

    @staticmethod
    def _announce_failure(
        action: str, detail: str, *, priority: str = "URGENT", airborne: Optional[bool] = None
    ) -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            details = {"etapa": detail}
            if airborne is not None:
                details["em_voo"] = airborne
            announce_sync(action, details=details, priority=priority, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
