import { describe, expect, it } from 'vitest';
import type { MissionState } from '../types/mission';
import { NUDGE_KEY_MAP, nudgeEnabled, nudgeIntent, reduceNudgeKeys } from './nudge';

const key = (type: 'keydown' | 'keyup', code: string, extra: Partial<KeyboardEvent> = {}) => ({
  type,
  code,
  repeat: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...extra,
});

const hold = (...codes: string[]) => codes.reduce((set, code) => reduceNudgeKeys(set, key('keydown', code)), new Set<string>() as ReadonlySet<string>);

describe('NUDGE_KEY_MAP', () => {
  it('maps every key to its axis and sign', () => {
    expect(NUDGE_KEY_MAP).toEqual({
      KeyW: { axis: 'vx', sign: 1 },
      KeyS: { axis: 'vx', sign: -1 },
      KeyA: { axis: 'vy', sign: 1 },
      KeyD: { axis: 'vy', sign: -1 },
      KeyQ: { axis: 'vyaw', sign: 1 },
      KeyR: { axis: 'vyaw', sign: -1 },
    });
  });
});

describe('reduceNudgeKeys', () => {
  it('ignores repeats, chords and keys outside the map', () => {
    const pressed = hold('KeyW');
    expect(reduceNudgeKeys(pressed, key('keydown', 'KeyW', { repeat: true }))).toBe(pressed);
    expect(reduceNudgeKeys(new Set(), key('keydown', 'KeyR', { ctrlKey: true })).size).toBe(0);
    expect(reduceNudgeKeys(new Set(), key('keydown', 'KeyC', { ctrlKey: true })).size).toBe(0);
    expect(reduceNudgeKeys(new Set(), key('keydown', 'KeyQ', { altKey: true })).size).toBe(0);
    expect(reduceNudgeKeys(new Set(), key('keydown', 'KeyR', { metaKey: true })).size).toBe(0);
    expect(reduceNudgeKeys(new Set(), key('keydown', 'KeyX')).size).toBe(0);
  });

  it('removes a key on keyup', () => {
    expect(reduceNudgeKeys(hold('KeyW', 'KeyA'), key('keyup', 'KeyW'))).toEqual(new Set(['KeyA']));
  });
});

describe('nudgeIntent', () => {
  it('cancels opposite keys and combines the axes', () => {
    expect(nudgeIntent(hold('KeyA', 'KeyD')).vy).toBe(0);
    expect(nudgeIntent(hold('KeyW', 'KeyS')).vx).toBe(0);
    expect(nudgeIntent(hold('KeyQ', 'KeyR')).vyaw).toBe(0);
    expect(nudgeIntent(hold('KeyW', 'KeyD', 'KeyQ'))).toEqual({ vx: 1, vy: -1, vyaw: 1 });
    expect(nudgeIntent(new Set())).toEqual({ vx: 0, vy: 0, vyaw: 0 });
  });
});

describe('nudgeEnabled', () => {
  const at = (missionState: MissionState, flyingState: number | null, stage: number, landProgress: 'landing' | null = null) =>
    nudgeEnabled({ missionState, flyingState, stage, landProgress });

  it('is off on the ground, in takeoff, in landing, at stage 1, outside a running mission or while landing', () => {
    expect(at('running', 0, 2)).toBe(false);
    expect(at('running', 1, 2)).toBe(false);
    expect(at('running', 4, 2)).toBe(false);
    expect(at('running', 2, 1)).toBe(false);
    for (const state of ['idle', 'arming', 'faulted', 'finished'] as MissionState[]) expect(at(state, 2, 2)).toBe(false);
    expect(at('running', 2, 3, 'landing')).toBe(false);
    expect(at('running', null, 3)).toBe(false);
  });

  it('is on hovering or flying from stage 2', () => {
    expect(at('running', 2, 2)).toBe(true);
    expect(at('running', 3, 5)).toBe(true);
  });
});
