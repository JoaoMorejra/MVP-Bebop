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
 * Bench runs are excluded, whole missions (`benchMode`) and single routines
 * (`benchStage`) alike: the motors are inert, and locking the operator out of
 * the parameters they are on the bench to adjust would defeat the bench.
 */
export function preflightLocked(
  state: MissionState,
  benchMode: boolean,
  benchStage: number | null
): boolean {
  const committed = state === 'arming' || state === 'running' || state === 'aborting';
  return committed && !benchMode && benchStage === null;
}
