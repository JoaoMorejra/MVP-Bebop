import type { MissionState } from '../types/mission';

/**
 * Exit statuses of `mission.py`, mirrored from
 * `mvp_mission_bebop/engine/exit_codes.py` and pinned by
 * `test/test_contracts.py::test_gcs_outcome_map_uses_the_same_exit_codes`.
 */
export const EXIT_COMPLETE = 0;
export const EXIT_ABORTED_LANDED = 3;
export const EXIT_TOUCHDOWN_UNCONFIRMED = 4;

/**
 * The final state a mission exit status stands for.
 *
 * Only a clean completion reads `finished`. An operator abort and a touchdown
 * the odometry never confirmed used to exit 0 as well and were announced as a
 * safe landing at base; each now has its own state. Anything else, including a
 * forced exit, a signal and a launch that never started, is a fault.
 */
export function missionStateForExit(code: number | null | undefined): MissionState {
  switch (code) {
    case EXIT_COMPLETE:
      return 'finished';
    case EXIT_ABORTED_LANDED:
      return 'aborted';
    case EXIT_TOUCHDOWN_UNCONFIRMED:
      return 'finished_unconfirmed';
    default:
      return 'faulted';
  }
}

const OVER_STATES: ReadonlySet<MissionState> = new Set<MissionState>([
  'finished',
  'finished_unconfirmed',
  'aborted',
  'faulted',
]);

/** Whether the mission process has ended, however it ended. */
export function isMissionOver(state: MissionState): boolean {
  return OVER_STATES.has(state);
}
