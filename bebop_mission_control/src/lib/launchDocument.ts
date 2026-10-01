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

/** Required paths of `doc` that do not hold a finite number, in declaration order. */
export function launchBlockers(doc: unknown): string[] {
  return REQUIRED_LAUNCH_NUMBERS.filter((path) => {
    const value = getPath(doc, path);
    return typeof value !== 'number' || !Number.isFinite(value);
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
