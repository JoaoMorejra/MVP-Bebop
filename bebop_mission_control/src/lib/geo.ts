/**
 * The telemetry bridge synthesises latitude and longitude from ENU odometry
 * against a fixed base (`streamer/telemetry_bridge.py:73-77`). Reversing that
 * projection recovers the metres the mission actually controls in, which is
 * what the survey plot draws.
 */

const METRES_PER_DEGREE = 111139;

export interface Enu {
  x: number;
  y: number;
}

export function toEnu(lat: number, lng: number, baseLat: number, baseLng: number): Enu {
  const y = (lat - baseLat) * METRES_PER_DEGREE;
  const x = (lng - baseLng) * METRES_PER_DEGREE * Math.cos((baseLat * Math.PI) / 180);
  return { x, y };
}

/** A round number of metres that keeps the plot between 3 and 8 grid squares. */
export function niceGridStep(extentMetres: number): number {
  const raw = extentMetres / 5;
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(raw, 0.01)));
  const normalized = raw / magnitude;
  const snapped = normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1;
  return snapped * magnitude;
}

/** The inverse of `toEnu`: a point `east`/`north` metres from a base, in degrees. */
export function fromEnu(
  east: number,
  north: number,
  baseLat: number,
  baseLng: number
): { lat: number; lng: number } {
  const lat = baseLat + north / METRES_PER_DEGREE;
  const lng =
    baseLng + east / (METRES_PER_DEGREE * Math.max(1e-6, Math.cos((baseLat * Math.PI) / 180)));
  return { lat, lng };
}

/** Whether a latitude/longitude pair is a place at all (not 0,0 and not the Bebop's 500,500). */
export function isValidCoordinate(lat: number | null | undefined, lng: number | null | undefined) {
  return (
    typeof lat === 'number' &&
    typeof lng === 'number' &&
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lng) <= 180 &&
    (Math.abs(lat) > 1e-6 || Math.abs(lng) > 1e-6)
  );
}
