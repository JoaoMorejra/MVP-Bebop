import { describe, expect, it } from 'vitest';
import {
  COMPLETE_RATE,
  EMPTY_FILL,
  EMPTY_SIGNALS,
  MEASURED_CEILING,
  advanceFill,
  fillInputFor,
  creep,
  measuredProgress,
  type FillInput,
  type FillState,
  type StageSignals,
} from './stageProgress';
import { recordMilestone } from '../hooks/useStageProgress';

const T0 = 1_000_000;
const signals = (overrides: Partial<StageSignals> = {}): StageSignals => ({ ...EMPTY_SIGNALS, entered: {}, ...overrides });
const input = (overrides: Partial<FillInput> = {}): FillInput => ({ stage: 1, measured: 0, finished: false, halted: false, ...overrides });

/** Run the strip for `ms` in ticks of 100 ms with a fixed input. */
function run(state: FillState, inp: FillInput, ms: number): FillState {
  let next = state;
  for (let t = 0; t < ms; t += 100) next = advanceFill(next, inp, 100);
  return next;
}

describe('creep', () => {
  it('moves toward its ceiling and never reaches it', () => {
    expect(creep(0, 1000, 0.1, 0.9)).toBe(0.1);
    expect(creep(1000, 1000, 0, 1)).toBeCloseTo(1 - Math.exp(-1));
    expect(creep(1e9, 1000, 0, 0.9)).toBeLessThanOrEqual(0.9);
    expect(creep(60_000, 1000, 0, 0.9)).toBeLessThan(0.9 + 1e-9);
  });
});

describe('measuredProgress', () => {
  it('follows the countdown, then the climb measured against the target altitude', () => {
    const counting = signals({ entered: { 1: T0 }, countdown: { remaining: 5, total: 10 } });
    expect(measuredProgress(1, counting, T0 + 1000, null)).toBeCloseTo(0.08 + 0.32 * 0.5);
    const climbing = signals({ entered: { 1: T0 }, countdown: { remaining: 0, total: 10 }, takeoffAt: T0, targetAltitude: 2 });
    expect(measuredProgress(1, climbing, T0 + 1000, 1)).toBeCloseTo(0.42 + 0.43 * 0.5);
    expect(measuredProgress(1, climbing, T0 + 1000, 0)).toBeCloseTo(0.42);
  });

  it('settles after the altitude is reached without ever completing the stage', () => {
    const settled = signals({ entered: { 1: T0 }, takeoffAt: T0, targetAltitude: 2, altitudeReachedAt: T0 });
    expect(measuredProgress(1, settled, T0 + 600_000, 2)).toBeLessThanOrEqual(MEASURED_CEILING);
    expect(measuredProgress(1, settled, T0 + 600_000, 2)).toBeGreaterThan(0.95);
  });

  it('reads the scan as the time spent in the window it announced, and a target found as nearly done', () => {
    const scan = signals({ entered: { 2: T0 }, scan: { at: T0, windowSec: 60 } });
    expect(measuredProgress(2, scan, T0 + 30_000, null)).toBeCloseTo(0.04 + 0.9 * 0.5);
    expect(measuredProgress(2, { ...scan, targetFoundAt: T0 + 1000 }, T0 + 2000, null)).toBeGreaterThanOrEqual(0.96);
  });

  it('creeps through an approach whose window is only a timeout', () => {
    const approach = signals({ entered: { 3: T0 }, approach: { at: T0, windowSec: 30 } });
    const early = measuredProgress(3, approach, T0 + 5000, null);
    const late = measuredProgress(3, approach, T0 + 30_000, null);
    expect(late).toBeGreaterThan(early);
    expect(late).toBeLessThan(0.95);
  });

  it('jumps the inspection on the capture', () => {
    const before = measuredProgress(4, signals({ entered: { 4: T0 } }), T0 + 2000, null);
    const after = measuredProgress(4, signals({ entered: { 4: T0 }, captureAt: T0 + 2000 }), T0 + 2000, null);
    expect(before).toBeLessThan(0.75);
    expect(after).toBeGreaterThanOrEqual(0.8);
  });

  it('measures the landing against the altitude it started from, and the touchdown at the ceiling', () => {
    const landing = signals({ entered: { 5: T0 }, rtlAt: T0, landing: { at: T0 + 10_000, altitude: 2 } });
    expect(measuredProgress(5, landing, T0 + 12_000, 1)).toBeCloseTo(0.6 + 0.37 * 0.5);
    expect(measuredProgress(5, { ...landing, touchdownAt: T0 + 15_000 }, T0 + 15_000, 0)).toBe(MEASURED_CEILING);
  });

  it('never reads a full stage from the signals alone', () => {
    const everything = signals({
      entered: { 1: T0, 2: T0, 3: T0, 4: T0, 5: T0 },
      countdown: { remaining: 0, total: 10 },
      takeoffAt: T0,
      targetAltitude: 1,
      altitudeReachedAt: T0,
      scan: { at: T0, windowSec: 1 },
      targetFoundAt: T0,
      approach: { at: T0, windowSec: 1 },
      captureAt: T0,
      rtlAt: T0,
      landing: { at: T0, altitude: 1 },
      touchdownAt: T0,
    });
    for (const step of [1, 2, 3, 4, 5]) expect(measuredProgress(step, everything, T0 + 1e7, 0)).toBeLessThan(1);
  });
});

describe('advanceFill', () => {
  it('follows the measured progress no faster than its rate and never backwards', () => {
    let state = advanceFill(EMPTY_FILL, input({ measured: 0.5 }), 100);
    expect(state.cursor).toBe(1);
    expect(state.fills[0]).toBeLessThan(0.5);
    state = run(state, input({ measured: 0.5 }), 2000);
    expect(state.fills[0]).toBeCloseTo(0.5);
    state = run(state, input({ measured: 0.2 }), 1000);
    expect(state.fills[0]).toBeCloseTo(0.5);
  });

  it('completes a stage only once the mission moves on, with a full fill animation', () => {
    let state = run(EMPTY_FILL, input({ measured: 0.5 }), 5000);
    expect(state.fills[0]).toBeCloseTo(0.5);
    state = advanceFill(state, input({ stage: 2, measured: 0 }), 100);
    expect(state.cursor).toBe(1);
    expect(state.fills[0]).toBeGreaterThan(0.5);
    expect(state.fills[0]).toBeLessThan(1);
    state = run(state, input({ stage: 2, measured: 0 }), 300);
    expect(state.fills[0]).toBe(1);
    expect(state.cursor).toBe(2);
  });

  it('fills every skipped stage in full, one after the other, before the selected one starts', () => {
    let state = run(EMPTY_FILL, input({ stage: 2, measured: 0.3 }), 3000);
    expect(state.cursor).toBe(2);
    const jump = input({ stage: 5, measured: 0.4 });
    state = advanceFill(state, jump, 100);
    expect(state.cursor).toBe(2);
    expect(state.fills[2]).toBe(0);
    const seen: number[] = [];
    for (let t = 0; t < 4000; t += 100) {
      state = advanceFill(state, jump, 100);
      seen.push(state.cursor);
      if (state.cursor < 5) expect(state.fills[4]).toBe(0);
    }
    expect([...new Set(seen)]).toEqual([2, 3, 4, 5]);
    expect(state.fills.slice(0, 4)).toEqual([1, 1, 1, 1]);
    expect(state.fills[4]).toBeGreaterThan(0);
    expect(4 * (1 / COMPLETE_RATE)).toBeLessThan(4000);
  });

  it('completes the last stage when the flight finishes', () => {
    let state = run(EMPTY_FILL, input({ stage: 5, measured: 0.9 }), 6000);
    expect(state.fills[4]).toBeLessThan(1);
    state = run(state, input({ stage: 5, measured: 0.9, finished: true }), 1000);
    expect(state.fills[4]).toBe(1);
  });

  it('holds where it is on an abort', () => {
    let state = run(EMPTY_FILL, input({ stage: 2, measured: 0.4 }), 3000);
    const held = run(state, input({ stage: 2, measured: 0.9, halted: true }), 3000);
    expect(held).toEqual(state);
  });

  it('empties the stages from an earlier one the mission is sent back to, and everything on a new cycle', () => {
    let state = run(EMPTY_FILL, input({ stage: 4, measured: 0.5 }), 5000);
    state = advanceFill(state, input({ stage: 2, measured: 0 }), 100);
    expect(state.cursor).toBe(2);
    expect(state.fills[0]).toBe(1);
    expect(state.fills.slice(2)).toEqual([0, 0, 0]);
    expect(advanceFill(state, input({ stage: 0 }), 100)).toEqual(EMPTY_FILL);
  });
});

describe('recordMilestone', () => {
  it('collects the signals each stage reads from the mission', () => {
    let s = signals();
    s = recordMilestone(s, { key: 'mission.parameters', payload: { countdown_sec: 10 }, at: T0 });
    s = recordMilestone(s, { key: 'mission.countdown', payload: { remaining_sec: 7 }, at: T0 });
    s = recordMilestone(s, { key: 'mission.takeoff', payload: { target_altitude_m: 2 }, at: T0 + 1 });
    s = recordMilestone(s, { key: 'mission.scan_start', payload: { window_sec: 60 }, at: T0 + 2 });
    s = recordMilestone(s, { key: 'mission.approaching', payload: { window_sec: 30 }, at: T0 + 3 });
    s = recordMilestone(s, { key: 'mission.landing', payload: { altitude_m: 1.8 }, at: T0 + 4 });
    expect(s.countdown).toEqual({ remaining: 7, total: 10 });
    expect(s.takeoffAt).toBe(T0 + 1);
    expect(s.targetAltitude).toBe(2);
    expect(s.scan).toEqual({ at: T0 + 2, windowSec: 60 });
    expect(s.approach).toEqual({ at: T0 + 3, windowSec: 30 });
    expect(s.landing).toEqual({ at: T0 + 4, altitude: 1.8 });
    expect(recordMilestone(s, { key: 'mission.start', payload: {}, at: T0 + 5 })).toEqual(signals());
  });
});

describe('fillInputFor', () => {
  it.each(['aborting', 'aborted'] as const)('runs an operator abort (%s) on to the end like a flight that is ending', (state) => {
    expect(fillInputFor(state, 2, 0.3)).toEqual({ stage: 5, measured: 0.3, finished: true, halted: false });
    let fill = advanceFill(EMPTY_FILL, input({ stage: 2, measured: 0.3 }), 100);
    for (let t = 0; t < 20000; t += 100) fill = advanceFill(fill, fillInputFor(state, 2, 0.3), 100);
    expect(fill.fills).toEqual([1, 1, 1, 1, 1]);
  });

  it('holds the strip where a fault stopped it', () => {
    expect(fillInputFor('faulted', 2, 0.3)).toEqual({ stage: 2, measured: 0.3, finished: false, halted: true });
  });

  it('passes a running flight through unchanged', () => {
    expect(fillInputFor('running', 3, 0.5)).toEqual({ stage: 3, measured: 0.5, finished: false, halted: false });
  });
});
