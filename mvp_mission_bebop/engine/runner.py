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
    ) -> None:
        self.ctx = context
        self.steps = steps or []
        self._on_finalize = on_finalize

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

        try:
            for step in self.steps:
                if self.ctx.emergency_event.is_set():
                    logger.warning("Emergency signal active. Halting step progression.")
                    all_succeeded = False
                    break

                status = step.execute(self.ctx)

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

        except Exception as exc:  # noqa: BLE001 - any step fault must land the drone
            logger.critical("Unhandled exception in mission pipeline: %s", exc, exc_info=True)
            self.ctx.failsafe.trigger_emergency_land(str(exc))
            all_succeeded = False

        return all_succeeded

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
