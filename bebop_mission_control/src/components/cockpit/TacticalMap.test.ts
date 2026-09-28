import { describe, expect, it } from 'vitest';
import { fitExtent } from './TacticalMap';

describe('fitExtent', () => {
  it('keeps the floor while the flight stays inside it', () => {
    expect(fitExtent([], 0, 0, 300)).toBe(300);
    expect(fitExtent([{ x: 2, y: 3 }], 2, 3, 300)).toBe(300);
  });

  it('only widens as the trail spreads beyond the floor', () => {
    expect(fitExtent([{ x: 0, y: 0 }, { x: 200, y: 0 }], 200, 0, 300)).toBe(480);
  });

  it('carries a wider resting extent into the mission instead of closing to the default', () => {
    const atRest = fitExtent([{ x: 0, y: 0 }, { x: 0, y: 150 }], 0, 150, 300);
    expect(atRest).toBe(360);
    expect(fitExtent([], 0, 0, atRest)).toBe(360);
  });
});
