import { describe, expect, it } from 'vitest';
import {
  EXIT_ABORTED_LANDED,
  EXIT_COMPLETE,
  EXIT_TOUCHDOWN_UNCONFIRMED,
  isMissionOver,
  missionStateForExit,
} from './missionOutcome';
import type { MissionState } from '../types/mission';

describe('missionStateForExit', () => {
  it.each([
    [EXIT_COMPLETE, 'finished'],
    [EXIT_ABORTED_LANDED, 'aborted'],
    [EXIT_TOUCHDOWN_UNCONFIRMED, 'finished_unconfirmed'],
    [1, 'faulted'],
    [130, 'faulted'],
    [-1, 'faulted'],
    [null, 'faulted'],
  ] as const)('exit %s reads as %s', (code, state) => {
    expect(missionStateForExit(code)).toBe(state);
  });

  it('keeps the wire values the mission process exits with', () => {
    expect([EXIT_COMPLETE, EXIT_ABORTED_LANDED, EXIT_TOUCHDOWN_UNCONFIRMED]).toEqual([0, 3, 4]);
  });
});

describe('isMissionOver', () => {
  it.each([
    ['finished', true],
    ['finished_unconfirmed', true],
    ['aborted', true],
    ['faulted', true],
    ['idle', false],
    ['arming', false],
    ['running', false],
    ['aborting', false],
  ] as Array<[MissionState, boolean]>)('%s -> %s', (state, over) => {
    expect(isMissionOver(state)).toBe(over);
  });
});
