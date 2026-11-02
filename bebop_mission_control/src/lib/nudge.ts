/**
 * The operator's keyboard nudge: W A S D translate, Q R turn.
 *
 * The station sends a ternary intent per axis; the mission
 * (`actuators/nudge_overlay.py`) scales, ramps and saturates it, adds it to
 * the autonomous command and zeroes it on its own deadman. Nothing here can
 * interrupt the mission: the nudge has no path to the stage control, the
 * abort or the landing.
 */

import type { LandProgressPhase, NudgeIntent } from '../types/bmg';
import type { MissionState } from '../types/mission';

/** Keys by physical position (`KeyboardEvent.code`), so a non-QWERTY layout keeps the pattern. */
export const NUDGE_KEY_MAP: Readonly<Record<string, { axis: keyof NudgeIntent; sign: 1 | -1 }>> = Object.freeze({
  KeyW: { axis: 'vx', sign: 1 },
  KeyS: { axis: 'vx', sign: -1 },
  KeyA: { axis: 'vy', sign: 1 },
  KeyD: { axis: 'vy', sign: -1 },
  KeyQ: { axis: 'vyaw', sign: 1 },
  KeyR: { axis: 'vyaw', sign: -1 },
});

/**
 * Re-send period of a held intent. The mission's deadman is 0.35 s, so three
 * heartbeats fit inside it and a single dropped message does not release.
 */
export const NUDGE_HEARTBEAT_MS = 100;

/** Ceiling enforced by the main process on intents that are not a release. */
export const NUDGE_MAX_RATE_HZ = 20;

/** The all-zero intent: the release. */
export const NUDGE_RELEASE: NudgeIntent = Object.freeze({ vx: 0, vy: 0, vyaw: 0 }) as NudgeIntent;

/** ARSDK flying states in which the nudge may act: hovering (2) and flying (3). */
const NUDGE_FLYING_STATES: ReadonlySet<number> = new Set([2, 3]);

/** Whether `code` is one of the six nudge keys. */
export function isNudgeKey(code: string): boolean {
  return Object.prototype.hasOwnProperty.call(NUDGE_KEY_MAP, code);
}

/**
 * The set of held nudge keys after one keyboard event.
 *
 * A repeat, a key outside the map, or any chord with Ctrl, Meta or Alt leaves
 * the set unchanged, so Ctrl+R, Ctrl+C and Alt+Q keep their meaning.
 */
export function reduceNudgeKeys(
  pressed: ReadonlySet<string>,
  event: Pick<KeyboardEvent, 'type' | 'code' | 'repeat' | 'ctrlKey' | 'metaKey' | 'altKey'>,
): ReadonlySet<string> {
  if (!isNudgeKey(event.code)) return pressed;
  if (event.type === 'keyup') {
    if (!pressed.has(event.code)) return pressed;
    const next = new Set(pressed);
    next.delete(event.code);
    return next;
  }
  if (event.type !== 'keydown' || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return pressed;
  if (pressed.has(event.code)) return pressed;
  const next = new Set(pressed);
  next.add(event.code);
  return next;
}

/** The intent of the held keys; opposite keys on one axis cancel. */
export function nudgeIntent(pressed: ReadonlySet<string>): NudgeIntent {
  const sum = { vx: 0, vy: 0, vyaw: 0 };
  for (const code of pressed) {
    const entry = NUDGE_KEY_MAP[code];
    if (entry) sum[entry.axis] += entry.sign;
  }
  const clip = (value: number): -1 | 0 | 1 => (value > 0 ? 1 : value < 0 ? -1 : 0);
  return { vx: clip(sum.vx), vy: clip(sum.vy), vyaw: clip(sum.vyaw) };
}

/** Whether two intents are the same command. */
export function sameIntent(a: NudgeIntent, b: NudgeIntent): boolean {
  return a.vx === b.vx && a.vy === b.vy && a.vyaw === b.vyaw;
}

/**
 * Whether the station may send a nudge at all.
 *
 * A running mission, the aircraft hovering or flying, a stage past the
 * takeoff, and no landing under way. The mission applies its own, finer
 * permit (precision landing, Stage 4 capture, failsafe) on top.
 */
export function nudgeEnabled(input: {
  missionState: MissionState;
  flyingState: number | null | undefined;
  stage: number;
  landProgress?: LandProgressPhase | null;
}): boolean {
  return (
    input.missionState === 'running' &&
    typeof input.flyingState === 'number' &&
    NUDGE_FLYING_STATES.has(input.flyingState) &&
    input.stage >= 2 &&
    (input.landProgress === null || input.landProgress === undefined)
  );
}
