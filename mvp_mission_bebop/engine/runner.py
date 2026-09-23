"""Deterministic mission pipeline sequencer."""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

import nectar

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.engine.signals import EmergencyHandler
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("MissionRunner")

#: Number of stop-and-land commands transmitted during an emergency. The Bebop
#: subscribes with a queue depth of 1 over best-effort QoS, so a single publish
#: can be dropped; a short burst makes delivery overwhelmingly likely without
#: consuming the time budget.
_EMERGENCY_BURST_COUNT: int = 5

#: Spacing between commands in that burst.
_EMERGENCY_BURST_INTERVAL_SEC: float = 0.04


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

        try:
            while index < len(self.steps):
                if self.ctx.emergency_event.is_set():
                    logger.warning("Emergency signal active. Halting step progression.")
                    all_succeeded = False
                    break

                step = self.steps[index]
                status = step.execute(self.ctx)

                # A step that unwound because the operator asked for a different
                # stage did not fail, and must not be reported as though it did.
                # This is checked before the status, because the status it
                # returned is its abort path -- that is how it stops quickly.
                target = self._take_stage_jump()
                if target is not None:
                    index = target
                    continue

                if status is StepStatus.ABORTED:
                    logger.warning("Step '%s' signalled ABORT.", step.name)
                    self._announce_failure("Missão abortada", "missão abortada, pousando drone")
                    all_succeeded = False
                    break

                if status is StepStatus.FAILURE:
                    logger.error("Step '%s' failed. Halting mission pipeline.", step.name)
                    self._announce_failure(
                        "Falha na etapa", f"falha na etapa {step.name}", priority="CRITICAL"
                    )
                    all_succeeded = False
                    break

                index += 1

        except Exception as exc:  # noqa: BLE001 - any step fault must land the drone
            logger.critical("Unhandled exception in mission pipeline: %s", exc, exc_info=True)
            self.ctx.failsafe.trigger_emergency_land(str(exc))
            all_succeeded = False

        return all_succeeded

    def _take_stage_jump(self) -> Optional[int]:
        """Consume a pending stage jump and return the step index to run next.

        Returns ``None`` when no jump is pending or the requested stage is not
        one this run holds -- a partial bench run configured with ``--stages 4``
        has nowhere to jump to, and a request naming a stage it does not carry is
        dropped rather than silently redirected to a different one.
        """
        if not self.ctx.stage_jump_event.is_set():
            return None

        requested = self.ctx.requested_stage
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
            "--- [STEP %d: %s] --- (salto comandado pela estação)",
            requested,
            self.steps[index].name,
        )
        return index

    # ------------------------------------------------------------ termination

    def _transmit_emergency_landing(self, budget_sec: float) -> None:
        """Transmit a controlled landing within the supplied time budget.

        Never cuts motors: this is a descent command, not a motor kill.
        """
        interval = _EMERGENCY_BURST_INTERVAL_SEC
        affordable = int(budget_sec / interval) if interval > 0.0 else _EMERGENCY_BURST_COUNT
        repeats = max(1, min(_EMERGENCY_BURST_COUNT, affordable))

        for index in range(repeats):
            self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
            self.ctx.drone.land()
            if index < repeats - 1:
                time.sleep(interval)

        logger.info("Emergency landing burst transmitted (%d commands).", repeats)

    def _finalize(self) -> None:
        """Release peripherals and the ROS runtime. Invoked at most once."""
        logger.info("Commencing resource de-allocation and sensor shutdown...")

        if not self.ctx.blackboard.rtl_completed:
            # The mission did not land on its own, so make sure it is on its way
            # down before the process exits.
            try:
                self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
                self.ctx.drone.land()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Landing command during cleanup failed: %s", exc)

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

    @staticmethod
    def _announce_failure(action: str, detail: str, *, priority: str = "URGENT") -> None:
        try:
            from mvp_mission_bebop.telemetry.announcer import announce_sync

            announce_sync(action, details={"etapa": detail}, priority=priority, wait=False)
        except Exception as exc:  # noqa: BLE001 - audio is never flight-critical
            logger.debug("Announcement dispatch failed: %s", exc)
