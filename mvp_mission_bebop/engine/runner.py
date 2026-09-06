"""Deterministic mission pipeline runner and execution engine."""

from __future__ import annotations

import logging
import signal
import sys
import time
from typing import List, Optional

import nectar

from mvp_mission_bebop.context import MissionContext
from mvp_mission_bebop.steps.base import BaseStep, StepStatus

logger = logging.getLogger("MissionRunner")


class MissionRunner:
    """Orchestrates step pipeline execution, signals, and resource finalization."""

    def __init__(self, context: MissionContext, steps: Optional[List[BaseStep]] = None) -> None:
        self.ctx = context
        self.steps = steps or []
        self._emergency_in_progress = False

        # Register signal handlers for operator interruption
        signal.signal(signal.SIGINT, self._handle_operator_emergency)
        signal.signal(signal.SIGTERM, self._handle_operator_emergency)

    def _handle_operator_emergency(self, signum=None, frame=None) -> None:
        """Execute controlled landing burst upon operator interruption. Never cuts motors."""
        self.ctx.emergency_event.set()
        logger.critical("=" * 65)
        logger.critical("OPERATOR EMERGENCY SIGNAL DETECTED. TRANSMITTING CONTROLLED LANDING...")
        logger.critical("=" * 65)

        try:
            for _ in range(5):
                self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
                self.ctx.drone.land()
                time.sleep(0.06)
            logger.info("Emergency land command burst transmitted. Flushing network buffers...")
            time.sleep(1.2)
        except Exception as exc:
            logger.error("Error during emergency landing dispatch: %s", exc)

        if not self._emergency_in_progress:
            self._emergency_in_progress = True
            try:
                self.ctx.handler.cleanup()
                self.ctx.drone.cleanup()
                nectar.shutdown()
            except Exception:
                pass
            logger.info("Emergency landing sequence finalized. Exiting.")
            sys.exit(0)

    def run(self) -> bool:
        """Execute all configured steps sequentially.

        Returns
        -------
        bool
            True if all steps executed successfully, False otherwise.
        """
        logger.info("Commencing autonomous mission execution (%d steps configured)...", len(self.steps))
        all_succeeded = True

        try:
            for step in self.steps:
                if self.ctx.emergency_event.is_set():
                    logger.warning("Emergency signal active. Halting step progression.")
                    all_succeeded = False
                    break

                status = step.execute(self.ctx)

                if status == StepStatus.ABORTED:
                    logger.warning("Step '%s' signaled ABORT.", step.name)
                    all_succeeded = False
                    break
                elif status == StepStatus.FAILURE:
                    logger.error("Step '%s' failed. Halting mission pipeline.", step.name)
                    try:
                        from mvp_mission_bebop.telemetry.announcer import announce_sync
                        announce_sync(
                            "Falha na etapa",
                            details={"etapa": step.name, "erro": f"falha na etapa {step.name}"},
                            priority="CRITICAL",
                            wait=False,
                        )
                    except Exception:
                        pass
                    all_succeeded = False
                    break

        except KeyboardInterrupt:
            self._handle_operator_emergency()
        except Exception as exc:
            logger.critical("Unhandled exception in mission pipeline: %s", exc, exc_info=True)
            self.ctx.failsafe.trigger_emergency_land(str(exc))
            all_succeeded = False
        finally:
            self._cleanup()

        return all_succeeded

    def _cleanup(self) -> None:
        """Clean up peripheral and network resources safely."""
        logger.info("Commencing resource de-allocation and sensor shutdown...")
        if not self.ctx.shared_data.get("rtl_completed", False):
            try:
                self.ctx.drone.move_velocity(vx=0.0, vy=0.0, vz=0.0, vyaw=0.0)
                self.ctx.drone.land()
            except Exception:
                pass

        try:
            self.ctx.handler.cleanup()
        except Exception as exc:
            logger.warning("ImageHandler cleanup exception: %s", exc)

        logger.info("Mission resource cleanup finalized.")
