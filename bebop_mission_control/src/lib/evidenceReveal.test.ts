import { afterEach, describe, expect, it } from 'vitest';
import { clearRevealed, recordRevealed, revealedProgress, scanProgress } from './evidenceReveal';

afterEach(clearRevealed);

describe('evidenceReveal', () => {
  it('keeps where the flight stood when a capture was revealed, the first record winning', () => {
    expect(revealedProgress('a')).toBeNull();
    expect(recordRevealed('a', 0.4)).toBe(0.4);
    expect(recordRevealed('a', 0.9)).toBe(0.4);
    expect(revealedProgress('a')).toBe(0.4);
    expect(revealedProgress('b')).toBeNull();
    expect(revealedProgress(null)).toBeNull();
  });

  it('clamps and refuses what is not a progress', () => {
    expect(recordRevealed('low', -3)).toBe(0);
    expect(recordRevealed('high', 7)).toBe(1);
    expect(recordRevealed('nan', Number.NaN)).toBe(0);
  });

  it('maps the flight between the reveal and touchdown onto the scan, 0 to 1', () => {
    expect(scanProgress(0.5, 0.5)).toBe(0);
    expect(scanProgress(0.5, 0.75)).toBeCloseTo(0.5, 10);
    expect(scanProgress(0.5, 1)).toBe(1);
    expect(scanProgress(0.5, 0.2)).toBe(0);
    expect(scanProgress(0.5, undefined)).toBe(0);
    // Revealed with nothing left of the flight: the pass is already at its end.
    expect(scanProgress(1, 1)).toBe(1);
  });

  it('forgets everything on clear', () => {
    recordRevealed('a', 0.4);
    clearRevealed();
    expect(revealedProgress('a')).toBeNull();
  });
});
