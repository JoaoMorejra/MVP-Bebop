import type { MissionState } from '../types/mission';

/**
 * Whether the pre-flight tab is locked.
 *
 * Locked from the operator's launch click — the runtime is `arming` before the
 * mission process has even been spawned, so the whole countdown is covered —
 * through the flight and any abort, until the process reports its exit. The
 * pre-flight screen's one control commands a launch, and the cockpit holds the
 * only control that stops one, so neither may be one tab away from the other
 * while an aircraft is committed.
 *
 * The bench is no exception, a whole mission or a single routine: it rehearses
 * the flight, so it holds the operator in the cockpit the same way. The tab
 * frees as soon as the process reports its exit.
 */
export function preflightLocked(state: MissionState): boolean {
  return state === 'arming' || state === 'running' || state === 'aborting';
}
