/**
 * The battery failsafe's configuration bounds and the decision it takes.
 *
 * The station does not land the aircraft where it is when it can bring it home
 * instead: during the search, the approach or the inspection, the mission is
 * asked to jump to stage 5 (`ClosedLoopRTLStep`), whose every nominal exit is
 * the marker-guided, odometrically confirmed touchdown on the pad. The jump
 * travels the same path as the bench's stage control (`bmg:goto-stage`), which
 * `mission.py` accepts mid-flight: the running step unwinds through its abort
 * path and the runner continues at the requested stage.
 */

export const FAILSAFE_MIN_PCT = 0;
export const FAILSAFE_MAX_PCT = 100;

/**
 * How long the station waits for the mission to report stage 5 after asking
 * for it before it lands the aircraft where it is. A step notices the jump at
 * its next control tick; five seconds is well beyond that and short against
 * the charge left at a critical threshold.
 */
export const RTL_ACK_TIMEOUT_MS = 5000;

/** Stages from which a return is flown rather than a landing in place. */
const RETURNABLE_STAGES: ReadonlySet<number> = new Set([2, 3, 4]);

/** The return stage, `ClosedLoopRTLStep`. */
export const RTL_STAGE = 5;

/**
 * A stored or typed threshold as a whole percentage inside the slider's range.
 *
 * @returns null when `value` is not a finite number, so the caller chooses
 *   the default rather than this function guessing one.
 */
export function clampThreshold(value: unknown): number | null {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : Number.NaN;
  if (!Number.isFinite(n)) return null;
  return Math.min(FAILSAFE_MAX_PCT, Math.max(FAILSAFE_MIN_PCT, Math.round(n)));
}

/**
 * - `rtl`: ask the mission for stage 5.
 * - `none`: stage 5 is already running; restarting it would throw away the
 *   progress it has made towards the pad.
 * - `land`: land where it is. Stage 1 is still over the pad, and without a
 *   mission process (an airframe flown by hand) there is nothing to jump.
 */
export type FailsafeAction = 'rtl' | 'none' | 'land';

export function failsafeAction(stage: number, missionRunning: boolean): FailsafeAction {
  if (!missionRunning) return 'land';
  if (stage === RTL_STAGE) return 'none';
  return RETURNABLE_STAGES.has(stage) ? 'rtl' : 'land';
}
