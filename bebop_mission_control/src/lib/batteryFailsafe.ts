/**
 * The battery failsafe's configuration bounds and the decision it takes.
 *
 * The mission process is the authority on a critical charge: it jumps to
 * stage 5 (`ClosedLoopRTLStep`) on its own, from the climb onwards and
 * whatever `rtl.enabled` says. The station is the redundancy: while a mission
 * runs it asks for the same jump on the same path as the bench's stage control
 * (`bmg:goto-stage`), which `mission.py` refuses as a duplicate once stage 5
 * is running. It never lands the aircraft where it is while a mission is
 * there to bring it home.
 */

import type { BatteryFailsafe } from '../types/bmg';
import { LAUNCH_COUNTDOWN_SEC } from './launchDocument';
import { setPath } from './paths';

export const FAILSAFE_MIN_PCT = 0;
export const FAILSAFE_MAX_PCT = 100;

/**
 * One window of the station's wait for the mission to acknowledge the return,
 * by reporting stage 5 or the `mission.battery_return` milestone. A step
 * notices the jump at its next control tick; five seconds is well beyond that
 * and short against the charge left at a critical threshold. Without an
 * acknowledgement the jump is asked once more, and only a second silent window
 * lands the aircraft where it is.
 */
export const RTL_ACK_TIMEOUT_MS = 5000;

/**
 * Stages from which a return is flown: the climb, the search, the approach and
 * the inspection. {@link failsafeAction} asks for the return from these and
 * also before any stage is reported (0), so it needs no lookup.
 */
export const RETURNABLE_STAGES: ReadonlySet<number> = new Set([1, 2, 3, 4]);

/** The return stage, `ClosedLoopRTLStep`. */
export const RTL_STAGE = 5;

/** ARSDK flying states that arm the station's trigger: hovering (2) and flying (3). */
const BATTERY_TRIGGER_STATES: ReadonlySet<number> = new Set([2, 3]);

/** Whether the station's battery trigger is armed: hovering or flying only, since the mission owns the climb and the descent. */
export function batteryTriggerArmed(flyingState: number | null | undefined): boolean {
  return typeof flyingState === 'number' && BATTERY_TRIGGER_STATES.has(flyingState);
}

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
 * - `land`: land where it is. Only without a mission process (an airframe
 *   flown by hand, or a mission already gone), where there is nothing to jump.
 */
export type FailsafeAction = 'rtl' | 'none' | 'land';

/**
 * The station's response to a critical charge.
 *
 * Parameters
 * ----------
 * stage
 *   The stage the mission last reported; 0 before any `[STEP N: ...]`. The
 *   caller consults this only while the aircraft reports a flying state in
 *   the air: the mission's stage gate refuses stage 5 only while stage 5 runs,
 *   so a request on the ground would fly the return on the ground. An
 *   airborne aircraft with no stage reported yet is asked for the return.
 * missionRunning
 *   Whether a mission process is up to take the jump.
 */
export function failsafeAction(stage: number, missionRunning: boolean): FailsafeAction {
  if (!missionRunning) return 'land';
  if (stage === RTL_STAGE) return 'none';
  return 'rtl';
}

/**
 * Where the mission reads its onboard land threshold:
 * `BatteryConfig.land_pct` in `mvp_mission_bebop/parameters.py`.
 */
export const LAND_PCT_PATH = 'battery.land_pct';

/**
 * Where the mission reads whether its battery failsafe acts at all:
 * `BatteryConfig.enabled`. Off, the mission neither returns on a low charge
 * nor refuses the launch, and the launch gates stop gating on the charge.
 */
export const ENABLED_PATH = 'battery.enabled';

/**
 * The `--params-json` payload for a launch or a bench routine.
 *
 * The parameter document goes out whole, with the battery-flyout threshold
 * written under {@link LAND_PCT_PATH}, the charge at which the mission itself
 * starts the return, and the operator's switch under {@link ENABLED_PATH}. The flyout keeps its own persistence, so the value is
 * injected into a clone and never becomes a second stored copy. The countdown
 * goes out as {@link LAUNCH_COUNTDOWN_SEC} whatever the document holds, so the
 * mission never counts a window the overlay does not show.
 */
export function launchParamsJson(
  doc: unknown,
  failsafe: Pick<BatteryFailsafe, 'enabled' | 'thresholdPct'>,
): string {
  const withThreshold = setPath(doc ?? {}, LAND_PCT_PATH, failsafe.thresholdPct);
  const withSwitch = setPath(withThreshold, ENABLED_PATH, failsafe.enabled === true);
  return JSON.stringify(setPath(withSwitch, 'kinematics.countdown_sec', LAUNCH_COUNTDOWN_SEC));
}
