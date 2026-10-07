/**
 * The parameter document as the launch sends it.
 *
 * `--params-json` is the only source of a launch's parameters: the station no
 * longer adds per-field flags, so nothing here falls back to a default. A
 * required number that is missing or not finite blocks the launch and is named
 * to the operator, instead of being replaced by a figure they never saw.
 */
import { ALL_PARAMETERS } from './parameterSchema';
import { getPath, setPath, type Doc } from './paths';

/**
 * Numbers a launch refuses to go without: every field the parameter sheet
 * edits, and every one the station used to pass as its own CLI flag.
 */
export const REQUIRED_LAUNCH_NUMBERS: readonly string[] = Array.from(
  new Set([
    ...ALL_PARAMETERS.map((spec) => spec.path),
    'kinematics.target_altitude_m',
    'kinematics.forward_cruise_velocity',
    'kinematics.hover_duration_sec',
    'kinematics.countdown_sec',
    'timeouts.search_timeout_sec',
    'rtl.max_speed',
    'rtl.arrival_radius_m',
    'vision.confidence_threshold',
  ])
);

/**
 * Bounds of the required numbers outside the sheet, as `(low, high]` with
 * `lowExclusive`. The sheet fields use their slider range, inclusive.
 */
const EXTRA_BOUNDS: Readonly<Record<string, { low: number; high: number; lowExclusive: boolean }>> = {
  'vision.confidence_threshold': { low: 0, high: 1, lowExclusive: true },
  'rtl.max_speed': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
  'rtl.arrival_radius_m': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
  'kinematics.hover_duration_sec': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
};

function inEnvelope(path: string, value: number): boolean {
  const spec = ALL_PARAMETERS.find((item) => item.path === path);
  if (spec) return value >= spec.min && value <= spec.max;
  const bounds = EXTRA_BOUNDS[path];
  if (!bounds) return true;
  return (bounds.lowExclusive ? value > bounds.low : value >= bounds.low) && value <= bounds.high;
}

/**
 * Required paths of `doc` that do not hold a finite number inside its
 * envelope, in declaration order.
 *
 * The envelope is the parameter sheet's slider range for its fields, and a
 * positive value (a confidence in (0, 1]) for the others. Measured: a test
 * fixture with confidence 12 and the nadir tilt at +5 deg reached the station
 * store; every finite, so it launched, and the detector refused to load.
 */
export function launchBlockers(doc: unknown): string[] {
  return REQUIRED_LAUNCH_NUMBERS.filter((path) => {
    const value = getPath(doc, path);
    return typeof value !== 'number' || !Number.isFinite(value) || !inEnvelope(path, value);
  });
}

/**
 * The launch countdown `doc` asks for, in seconds; 0 means none.
 *
 * Read literally. `null` when the field is missing, not finite or negative,
 * which {@link launchBlockers} or the mission's own validation then refuses.
 */
export function launchCountdownSec(doc: unknown): number | null {
  const value = getPath(doc, 'kinematics.countdown_sec');
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * `target` with the parameter-sheet fields taken from `source`.
 *
 * A preset or the factory defaults carry a whole document, but applying one
 * may only move what the sheet shows: PID gains, the calibration statistics
 * the mission writes back and the arming mode stay as they are. A field
 * `source` does not hold is left untouched.
 */
export function applySchemaFields<T extends Doc>(target: T, source: unknown): T {
  let next = target;
  for (const spec of ALL_PARAMETERS) {
    const value = getPath(source, spec.path);
    if (value !== undefined) next = setPath(next, spec.path, value);
  }
  return next;
}
