import { describe, expect, it } from 'vitest';
import { fromEnu, isValidCoordinate, toEnu } from './geo';

/** Mean Earth radius (IUGG), metres. */
const R = 6371008.8;
const rad = (deg: number) => (deg * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

/**
 * Spherical direct geodesic: the point `distance` metres from a start along
 * `bearing` (degrees clockwise from north). Independent of the flat
 * equirectangular approximation `fromEnu` uses, so a sign, axis or scale error
 * there cannot hide behind a shared constant.
 */
function destination(lat: number, lng: number, bearing: number, distance: number) {
  const delta = distance / R;
  const theta = rad(bearing);
  const phi1 = rad(lat);
  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(delta) + Math.cos(phi1) * Math.sin(delta) * Math.cos(theta)
  );
  const lambda2 =
    rad(lng) +
    Math.atan2(
      Math.sin(theta) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2)
    );
  return { lat: deg(phi2), lng: deg(lambda2) };
}

/** Metres between two nearby points, by the haversine formula. */
function separation(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const dPhi = rad(b.lat - a.lat);
  const dLambda = rad(b.lng - a.lng);
  const h =
    Math.sin(dPhi / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLambda / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

const ITAJUBA = { lat: -22.412, lng: -45.46 };

describe('fromEnu', () => {
  const cases: Array<{ name: string; base: { lat: number; lng: number }; east: number; north: number }> = [
    { name: '10 m north', base: ITAJUBA, east: 0, north: 10 },
    { name: '10 m east', base: ITAJUBA, east: 10, north: 0 },
    { name: '10 m south', base: ITAJUBA, east: 0, north: -10 },
    { name: '10 m west', base: ITAJUBA, east: -10, north: 0 },
    { name: '100 m north-east', base: ITAJUBA, east: 70.71, north: 70.71 },
    { name: '250 m south-west', base: ITAJUBA, east: -176.78, north: -176.78 },
    { name: 'equator, 50 m east', base: { lat: 0, lng: 0 }, east: 50, north: 0 },
    { name: '60 deg N, 80 m east', base: { lat: 60, lng: 10 }, east: 80, north: 0 },
  ];

  for (const { name, base, east, north } of cases) {
    it(`lands where the geodesic lands: ${name}`, () => {
      const distance = Math.hypot(east, north);
      const bearing = (deg(Math.atan2(east, north)) + 360) % 360;
      const expected = destination(base.lat, base.lng, bearing, distance);
      const got = fromEnu(east, north, base.lat, base.lng);
      // The flat approximation is good to a few parts in ten thousand at these
      // ranges; an axis swap or a sign error is off by the whole distance.
      expect(separation(got, expected)).toBeLessThan(Math.max(0.02, distance * 0.002));
    });
  }

  it('moves north as latitude and east as longitude, with the right signs', () => {
    const north = fromEnu(0, 10, ITAJUBA.lat, ITAJUBA.lng);
    expect(north.lat).toBeGreaterThan(ITAJUBA.lat);
    expect(north.lng).toBeCloseTo(ITAJUBA.lng, 12);

    const east = fromEnu(10, 0, ITAJUBA.lat, ITAJUBA.lng);
    expect(east.lng).toBeGreaterThan(ITAJUBA.lng);
    expect(east.lat).toBeCloseTo(ITAJUBA.lat, 12);
  });

  it('widens a metre of longitude away from the equator', () => {
    const equator = fromEnu(10, 0, 0, 0).lng;
    const south = fromEnu(10, 0, ITAJUBA.lat, 0).lng;
    expect(south / equator).toBeCloseTo(1 / Math.cos(rad(ITAJUBA.lat)), 6);
  });

  it('is undone by toEnu', () => {
    for (const [east, north] of [
      [3, 4],
      [-12.5, 7.25],
      [0, -30],
    ]) {
      const at = fromEnu(east, north, ITAJUBA.lat, ITAJUBA.lng);
      const back = toEnu(at.lat, at.lng, ITAJUBA.lat, ITAJUBA.lng);
      expect(back.x).toBeCloseTo(east, 9);
      expect(back.y).toBeCloseTo(north, 9);
    }
  });

  it('stays finite at the pole', () => {
    const at = fromEnu(10, 0, 90, 0);
    expect(Number.isFinite(at.lat) && Number.isFinite(at.lng)).toBe(true);
  });
});

describe('isValidCoordinate', () => {
  it('refuses the null island and the Bebop 500,500 placeholder', () => {
    expect(isValidCoordinate(0, 0)).toBe(false);
    expect(isValidCoordinate(500, 500)).toBe(false);
    expect(isValidCoordinate(ITAJUBA.lat, ITAJUBA.lng)).toBe(true);
    expect(isValidCoordinate(Number.NaN, 1)).toBe(false);
  });
});
