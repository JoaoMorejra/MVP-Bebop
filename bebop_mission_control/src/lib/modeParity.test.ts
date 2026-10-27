import { describe, expect, it } from 'vitest';
import { abortEnabled } from './flightState';
import { failsafeAction } from './batteryFailsafe';
import { preflightLocked } from './navigationLock';
import { finishLockState, type FinishLockInput } from './finishLock';
import type { MissionState } from '../types/mission';

const STATES: MissionState[] = ['idle', 'arming', 'running', 'aborting', 'finished', 'finished_unconfirmed', 'aborted', 'faulted'];

const LOCKED_WITHOUT_PROCESS: Record<MissionState, boolean> = {
  idle: false,
  arming: true,
  running: true,
  aborting: true,
  finished: false,
  finished_unconfirmed: false,
  aborted: false,
  faulted: false,
};

const LOCKED_WITH_PROCESS: Record<MissionState, boolean> = {
  idle: false,
  arming: true,
  running: true,
  aborting: true,
  finished: true,
  finished_unconfirmed: true,
  aborted: true,
  faulted: true,
};

const BASE_FINISH: FinishLockInput = {
  missionState: 'running',
  processUp: true,
  exitCode: null,
  benchMode: false,
  flyingState: 2,
  flyingStateSince: 0,
  navFresh: true,
  navSource: 'aircraft',
  telemetryAgeSec: 0.1,
  finishing: false,
  benchFlyingState: null,
  exitedAt: null,
};

describe('operation rules do not depend on the mission mode', () => {
  it('declares no mode in the signature of the lock, abort, finish and failsafe rules', () => {
    expect(preflightLocked.length).toBe(2);
    expect(abortEnabled.length).toBeLessThanOrEqual(3);
    expect(failsafeAction.length).toBe(2);
    expect(finishLockState.length).toBe(2);
  });

  it.each(STATES)('locks the pre-flight tab as the table says in state %s', (state) => {
    expect(preflightLocked(state, false)).toBe(LOCKED_WITHOUT_PROCESS[state]);
    expect(preflightLocked(state, true)).toBe(LOCKED_WITH_PROCESS[state]);
  });

  it('asks for the stage 5 jump from stages 1 to 4 and lands in place only without a mission, for every mode', () => {
    for (const stage of [1, 2, 3, 4]) expect(failsafeAction(stage, true)).toBe('rtl');
    expect(failsafeAction(5, true)).toBe('none');
    expect(failsafeAction(1, false)).toBe('land');
    expect(failsafeAction(3, false)).toBe('land');
  });

  it('keeps the abort available while flying and unavailable on the ground', () => {
    expect(abortEnabled('running', 2)).toBe(true);
    expect(abortEnabled('arming', null)).toBe(true);
    expect(abortEnabled('idle', 0)).toBe(false);
  });

  it.each([
    [{}, 'in_flight', false],
    [{ missionState: 'aborting' as const }, 'aborting', false],
    [{ finishing: true }, 'finishing', false],
    [{ processUp: false, missionState: 'aborted' as const, flyingState: 3 }, 'await_ground', false],
    [{ processUp: false, missionState: 'idle' as const, flyingState: 0 }, 'no_mission', false],
    [
      { processUp: false, missionState: 'finished' as const, flyingState: 0, exitedAt: 0, flyingStateSince: 0 },
      'grounded',
      true,
    ],
  ])('holds the finish lock for %j as %s', (patch, state, enabled) => {
    const result = finishLockState({ ...BASE_FINISH, ...patch }, 5000);
    expect(result.state).toBe(state);
    expect(result.enabled).toBe(enabled);
  });
});
