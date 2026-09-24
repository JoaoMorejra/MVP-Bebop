import { describe, expect, it } from 'vitest';
import {
  MAX_DISPLAY_ZOOM,
  MIN_DISPLAY_ZOOM,
  displayZoom,
  pixelsPerMetre,
  tileZoom,
  formatAccuracy,
  accuracyIsCoarse,
} from './mapView';

const LAT = -22.412;

describe('displayZoom', () => {
  it('fits a short bench flight instead of drawing it under the marker', () => {
    // 5.8 m of flight at the old fixed zoom 18 was about eleven pixels.
    const zoom = displayZoom(12, { width: 880, height: 360 }, LAT);
    const span = pixelsPerMetre(zoom, LAT) * 12;
    expect(span).toBeGreaterThan(0.5 * 360);
    expect(span).toBeLessThanOrEqual(360);
  });

  it('zooms out as the flight spreads, and never past its bounds', () => {
    const near = displayZoom(12, { width: 880, height: 360 }, LAT);
    const far = displayZoom(400, { width: 880, height: 360 }, LAT);
    expect(far).toBeLessThan(near);
    expect(displayZoom(0.01, { width: 880, height: 360 }, LAT)).toBe(MAX_DISPLAY_ZOOM);
    expect(displayZoom(1e7, { width: 880, height: 360 }, LAT)).toBe(MIN_DISPLAY_ZOOM);
  });

  it('moves in half steps, so a growing flight does not refetch tiles every frame', () => {
    for (const extent of [12, 13, 17.5, 31, 64, 150]) {
      const zoom = displayZoom(extent, { width: 880, height: 360 }, LAT);
      expect(zoom * 2).toBe(Math.round(zoom * 2));
    }
  });

  it('survives a degenerate viewport or extent', () => {
    for (const zoom of [
      displayZoom(Number.NaN, { width: 880, height: 360 }, LAT),
      displayZoom(12, { width: 0, height: 0 }, LAT),
    ]) {
      expect(Number.isFinite(zoom)).toBe(true);
    }
  });
});

describe('tileZoom', () => {
  it('requests the display zoom when the source has it, and overzooms beyond', () => {
    expect(tileZoom(18.5, 20)).toEqual({ z: 18, scale: 2 ** 0.5 });
    expect(tileZoom(21.5, 19)).toEqual({ z: 19, scale: 2 ** 2.5 });
    expect(tileZoom(17, 19)).toEqual({ z: 17, scale: 1 });
  });
});

describe('pixelsPerMetre', () => {
  it('is the inverse of Web Mercator ground resolution', () => {
    const mpp = (156543.03392 * Math.cos((LAT * Math.PI) / 180)) / 2 ** 18;
    expect(pixelsPerMetre(18, LAT)).toBeCloseTo(1 / mpp, 9);
  });
});

describe('base accuracy', () => {
  it('reads as metres or kilometres', () => {
    expect(formatAccuracy(12.4)).toBe('±12 m');
    expect(formatAccuracy(850)).toBe('±850 m');
    expect(formatAccuracy(5000)).toBe('±5 km');
    expect(formatAccuracy(1500)).toBe('±1,5 km');
  });

  it('flags a base too coarse to register the flight on the streets', () => {
    expect(accuracyIsCoarse(20)).toBe(false);
    expect(accuracyIsCoarse(5000)).toBe(true);
    expect(accuracyIsCoarse(Number.NaN)).toBe(true);
  });
});
