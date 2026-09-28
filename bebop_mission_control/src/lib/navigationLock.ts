import type { MissionState } from '../types/mission';

/**
 * Whether the pre-flight tab is locked.
 *
 * Locked from the operator's launch click -- the runtime is `arming` before the
 * mission process has even been spawned, so the whole countdown is covered --
 * through the flight, any abort and the landing, until the operator ends the
 * cycle with "Finalizar missão" (which resets the runtime to `idle`). The
 * process exiting is not the end of the cycle: the aircraft has only just
 * landed and the report is being read, and the pre-flight screen's one
 * control commands the next launch.
 *
 * `ran` says a mission process existed. A launch whose process never started
 * also reads `faulted`, and it leaves the tab free: nothing flew, and the
 * operator has to get back to the control that failed.
 *
 * The bench is no exception, a whole mission or a single routine.
 */
export function preflightLocked(state: MissionState, ran: boolean): boolean {
  if (state === 'arming' || state === 'running' || state === 'aborting') return true;
  return ran && (state === 'finished' || state === 'faulted');
}
