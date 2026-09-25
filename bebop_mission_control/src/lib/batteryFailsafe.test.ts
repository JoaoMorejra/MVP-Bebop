import { describe, expect, it } from 'vitest';
import { clampThreshold, failsafeAction } from './batteryFailsafe';

describe('clampThreshold', () => {
  it('keeps every whole percentage from 0 to 100 exactly', () => {
    for (const pct of [0, 1, 5, 10, 40, 41, 99, 100]) expect(clampThreshold(pct)).toBe(pct);
  });

  it('rounds to the 1% step and clamps to the range', () => {
    expect(clampThreshold(12.6)).toBe(13);
    expect(clampThreshold(-3)).toBe(0);
    expect(clampThreshold(140)).toBe(100);
    expect(clampThreshold('25')).toBe(25);
  });

  it('refuses what is not a number', () => {
    expect(clampThreshold(undefined)).toBeNull();
    expect(clampThreshold('abc')).toBeNull();
    expect(clampThreshold(Number.NaN)).toBeNull();
  });
});

describe('failsafeAction', () => {
  it('flies the return from the search, the approach and the inspection', () => {
    for (const stage of [2, 3, 4]) expect(failsafeAction(stage, true)).toBe('rtl');
  });

  it('leaves a return already under way alone', () => {
    expect(failsafeAction(5, true)).toBe('none');
  });

  it('lands in place over the pad during takeoff, and before any stage is reported', () => {
    expect(failsafeAction(1, true)).toBe('land');
    expect(failsafeAction(0, true)).toBe('land');
  });

  it('lands in place when no mission process can take the jump', () => {
    for (const stage of [0, 2, 3, 5]) expect(failsafeAction(stage, false)).toBe('land');
  });
});
