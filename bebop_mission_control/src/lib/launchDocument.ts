/**
 * The parameter document as the launch sends it.
 *
 * `--params-json` is the only source of a launch's parameters: the station no
 * longer adds per-field flags, so nothing here falls back to a default. A
 * required number that is missing or not finite blocks the launch and is named
 * to the operator, instead of being replaced by a figure they never saw.
 */
import {
  TAKEOFF_FROM_GROUND_PATH,
  altitudeEnvelopeViolation,
  clampAltitude,
  inAltitudeEnvelope,
  launchesFromGround,
} from './altitudeEnvelope';
import {
  ALL_PARAMETERS,
  ALTITUDE_PATH,
  BOOLEAN_PARAMETERS,
  NUMBER_PARAMETERS,
  CENTERING_TIMEOUT_RANGE_SEC,
  isKnownMarkerDictionary,
  markerCount,
  type ParameterSpec,
} from './parameterSchema';
import {
  LEGACY_INTERMEDIATE_MODE,
  MISSION_MODE_PATH,
  RTL_ENABLED_PATH,
  hasInvalidMissionMode,
  isPathActive,
  readMissionMode,
  type MissionMode,
} from './missionMode';
import { blockingConflicts } from './parameterConflicts';
import { getPath, setPath, type Doc } from './paths';

const FORWARD_PATH = 'kinematics.forward_cruise_velocity';
const REVERSE_PATH = 'rtl.reverse_cruise_velocity';
const CEILING_PATH = 'kinematics.max_horizontal_speed';
const MARKER_ID_PATH = 'rtl.target_aruco_id';
const MARKER_DICT_PATH = 'rtl.marker_dict';
const TAG_SIZE_PATH = 'rtl.tag_size';
const CENTERING_TIMEOUT_PATH = 'rtl.centering_timeout_sec';

/** Largest pad marker edge the sheet accepts, in metres. */
export const MAX_TAG_SIZE_M = 1.0;
const FORWARD_SPEC = NUMBER_PARAMETERS.find((spec) => spec.path === FORWARD_PATH)!;

/**
 * Highest horizontal speed ceiling the station lifts to on its own.
 *
 * `telemetry/failsafe.py` saturates every vx/vy at `max_horizontal_speed`; the
 * ceiling is the guard against a defective guidance law, not a mission limit,
 * so it must sit above every legitimate demand and below 1.0 (the audit's
 * envelope in `config_audit.py`). 0.95 keeps it strictly inside.
 */
export const MAX_LIFTED_CEILING = 0.95;

/** Clearance the lifted ceiling keeps above the cruise, before rounding up to 0.05. */
export const CEILING_CLEARANCE = 0.05;

/**
 * Launch countdown, seconds. Not an operator parameter: mirror of
 * `FlightKinematicsConfig.countdown_sec` (`mvp_mission_bebop/parameters.py`),
 * pinned in every document by {@link reconcileCoupledPaths} so the overlay and
 * the mission count the same window.
 */
export const LAUNCH_COUNTDOWN_SEC = 10;
const COUNTDOWN_PATH = 'kinematics.countdown_sec';

/**
 * Numbers a launch refuses to go without: every numeric field the parameter
 * sheet edits, and every one the station used to pass as its own CLI flag. The
 * sheet's booleans are not numbers: they are {@link REQUIRED_LAUNCH_BOOLEANS}.
 */
export const REQUIRED_LAUNCH_NUMBERS: readonly string[] = Array.from(
  new Set([
    ...NUMBER_PARAMETERS.map((spec) => spec.path),
    'kinematics.target_altitude_m',
    'kinematics.forward_cruise_velocity',
    'kinematics.hover_duration_sec',
    COUNTDOWN_PATH,
    'timeouts.search_timeout_sec',
    'rtl.max_speed',
    'rtl.arrival_radius_m',
    'vision.confidence_threshold',
    REVERSE_PATH,
    CEILING_PATH,
    MARKER_ID_PATH,
    TAG_SIZE_PATH,
    CENTERING_TIMEOUT_PATH,
  ])
);

interface Bounds {
  low: number;
  high: number;
  lowExclusive?: boolean;
  highExclusive?: boolean;
}

/**
 * Bounds of the required numbers outside the sheet. The sheet fields use
 * their slider range, inclusive. The return cruise is the negated range of
 * the forward cruise it mirrors.
 */
const EXTRA_BOUNDS: Readonly<Record<string, Bounds>> = {
  'vision.confidence_threshold': { low: 0, high: 1, lowExclusive: true },
  'rtl.max_speed': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
  'rtl.arrival_radius_m': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
  'kinematics.hover_duration_sec': { low: 0, high: Number.POSITIVE_INFINITY, lowExclusive: true },
  [COUNTDOWN_PATH]: { low: 0, high: 30 },
  [REVERSE_PATH]: { low: -FORWARD_SPEC.max, high: -FORWARD_SPEC.min },
  [CEILING_PATH]: { low: 0, high: 1, lowExclusive: true, highExclusive: true },
  [MARKER_ID_PATH]: { low: 0, high: Number.POSITIVE_INFINITY },
  [TAG_SIZE_PATH]: { low: 0, high: MAX_TAG_SIZE_M, lowExclusive: true },
  [CENTERING_TIMEOUT_PATH]: { low: CENTERING_TIMEOUT_RANGE_SEC.min, high: CENTERING_TIMEOUT_RANGE_SEC.max },
};

function inEnvelope(path: string, value: number, doc?: unknown): boolean {
  if (path === ALTITUDE_PATH) return inAltitudeEnvelope(doc, value);
  const spec = NUMBER_PARAMETERS.find((item) => item.path === path);
  if (spec) return value >= spec.min && value <= spec.max;
  const bounds = EXTRA_BOUNDS[path];
  if (!bounds) return true;
  const aboveLow = bounds.lowExclusive ? value > bounds.low : value >= bounds.low;
  const belowHigh = bounds.highExclusive ? value < bounds.high : value <= bounds.high;
  return aboveLow && belowHigh;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * `doc` with the removed Intermediate profile carried over to its equivalent:
 * `mission_mode: 'intermediate'` (any case, trimmed) becomes `'advanced'` with
 * `rtl.enabled = false`, keeping an `rtl.enabled` the document states. Same
 * rule as `parameters.migrate_legacy_mission_mode` in the mission. Pure and
 * idempotent; returns `doc` itself when there is nothing to migrate.
 */
export function migrateLegacyMode<T extends Doc>(doc: T): T {
  const raw = getPath(doc, MISSION_MODE_PATH);
  if (typeof raw !== 'string' || raw.trim().toLowerCase() !== LEGACY_INTERMEDIATE_MODE) return doc;
  const next = setPath(doc, MISSION_MODE_PATH, 'advanced');
  return getPath(next, RTL_ENABLED_PATH) === undefined ? setPath(next, RTL_ENABLED_PATH, false) : next;
}

/** Schema paths introduced after a store may have been written; filled from the factory document when absent. */
export const BACKFILL_PATHS: readonly string[] = ['yaw_pattern.scan_vyaw'];

/**
 * The sheet's booleans, with the value a store written before them stands for:
 * the return (`rtl.enabled = true`) and the pad top
 * (`platform.takeoff_from_ground = false`), what every flight did then. A
 * launch never needs them present (the mission has the same defaults); one
 * present must be a JSON boolean ({@link launchBlockers}).
 */
export const REQUIRED_LAUNCH_BOOLEANS: Readonly<Record<string, boolean>> = Object.fromEntries(
  BOOLEAN_PARAMETERS.map((spec) => [spec.path, spec.path === TAKEOFF_FROM_GROUND_PATH ? false : true])
);

/**
 * `doc` with each of `paths` taken from `factory` when `doc` does not hold a
 * finite number there, and each of {@link REQUIRED_LAUNCH_BOOLEANS} filled when
 * absent: from `factory` when it holds a boolean, else with its default.
 */
export function fillMissing<T extends Doc>(doc: T, factory: unknown, paths: readonly string[] = BACKFILL_PATHS): T {
  let next = doc;
  for (const path of paths) {
    if (finite(getPath(next, path))) continue;
    const value = getPath(factory, path);
    if (finite(value)) next = setPath(next, path, value);
  }
  for (const [path, fallback] of Object.entries(REQUIRED_LAUNCH_BOOLEANS)) {
    if (getPath(next, path) !== undefined) continue;
    const value = getPath(factory, path);
    next = setPath(next, path, typeof value === 'boolean' ? value : fallback);
  }
  return next;
}

/**
 * What {@link normalizeForMode} did to the altitude of `before`, in the
 * operator's words; `null` when it is unchanged. Shown next to the sheet so a
 * value moved by a switch of surface or of mode is never silent.
 */
export function altitudeAdjustment(before: unknown, after: unknown): string | null {
  const was = getPath(before, ALTITUDE_PATH);
  const now = getPath(after, ALTITUDE_PATH);
  if (!finite(was) || !finite(now) || Math.abs(was - now) < 1e-9) return null;
  const figure = `${now.toFixed(2).replace('.', ',')} m`;
  if (now > was) return `Cota ajustada para ${figure}: mínimo sobre ${launchesFromGround(after) ? 'o piso' : 'o totem'}.`;
  return `Cota ajustada para ${figure}: máximo permitido.`;
}

/**
 * `doc` with the altitude moved into the slider range of its launch surface
 * (`altitudeEnvelope.ts`), the same in every mode. Returns `doc` itself when the
 * altitude is already inside, or is not a finite number ({@link launchBlockers}
 * names it then). No other field is touched: the ceiling margin the operator set
 * is kept.
 */
export function normalizeForMode<T extends Doc>(doc: T): T {
  const altitude = getPath(doc, ALTITUDE_PATH);
  if (!finite(altitude) || inAltitudeEnvelope(doc, altitude)) return doc;
  return setPath(doc, ALTITUDE_PATH, clampAltitude(doc, altitude));
}

/**
 * Required paths of `doc` that do not hold a finite number inside its own
 * range, in declaration order. Each field is judged alone.
 */
export function envelopeBlockers(doc: unknown): string[] {
  return REQUIRED_LAUNCH_NUMBERS.filter((path) => {
    const value = getPath(doc, path);
    return !finite(value) || !inEnvelope(path, value, doc);
  });
}

/**
 * Whether `path` breaks a rule tying it to another field. Only judged when
 * both fields are finite: a missing one is already named on its own.
 */
function breaksCoupling(doc: unknown, path: string): boolean {
  const forward = getPath(doc, FORWARD_PATH);
  if (!finite(forward)) return false;
  const value = getPath(doc, path);
  if (!finite(value)) return false;
  if (path === REVERSE_PATH) return Math.abs(forward + value) > 1e-9;
  if (path === CEILING_PATH) return value < forward;
  return false;
}

/**
 * Whether `rtl.marker_dict` is a value the station may send: a dictionary the
 * sheet lists, or a non-negative integer (a legacy order 4 to 7 or an OpenCV
 * enum, as a number or a digit string). Whether such an integer resolves is
 * the mission's call; the sheet cannot prove it either way.
 */
export function markerDictionaryAccepted(value: unknown): boolean {
  if (isKnownMarkerDictionary(value)) return true;
  if (typeof value === 'string') return /^\s*\d+\s*$/.test(value);
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

/** Whether `rtl.target_aruco_id` is an integer inside the chosen dictionary, when its size is known. */
function markerIdBlocked(doc: unknown): boolean {
  const id = getPath(doc, MARKER_ID_PATH);
  if (!finite(id) || !Number.isInteger(id)) return true;
  const count = markerCount(getPath(doc, MARKER_DICT_PATH));
  return count !== null && id >= count;
}

/**
 * Required paths of `doc` that do not hold a finite number inside its
 * envelope, in declaration order.
 *
 * The envelope is the parameter sheet's slider range for its fields, and a
 * positive value (a confidence in (0, 1]) for the others. Measured: a test
 * fixture with confidence 12 and the nadir tilt at +5 deg reached the station
 * store; every finite, so it launched, and the detector refused to load.
 * The return cruise must also be the negated forward cruise, and the
 * horizontal speed ceiling at least the forward cruise. The pad marker ID must
 * be an integer inside its dictionary and the dictionary a value the station
 * may send (`rtl.marker_dict` is named after the required numbers). Last come
 * the paths of every `error` conflict between parameters
 * (`parameterConflicts.ts`); a warning never blocks.
 *
 * The mission mode is read from `doc` itself (`mission_mode`, the full mission
 * when absent). A path the mode does not fly is never named, even when its
 * stored value is out of range or missing, and a conflict between such paths
 * only is dropped (`blockingConflicts` goes through `activeConflicts`): the
 * operator cannot see those fields, and the mission does not read them. A
 * `doc` that is not an object holds no mode to read and is refused on every
 * required path. A `mission_mode` the mission cannot parse (`"expert"`, a
 * number) is named last, so the GCS never launches a profile the mission
 * would refuse at the go; absent or blank is the full mission and passes.
 */
export function launchBlockers(doc: unknown): string[] {
  const outside = new Set(envelopeBlockers(doc));
  if (markerIdBlocked(doc)) outside.add(MARKER_ID_PATH);
  const blocked = REQUIRED_LAUNCH_NUMBERS.filter((path) => outside.has(path) || breaksCoupling(doc, path));
  if (!markerDictionaryAccepted(getPath(doc, MARKER_DICT_PATH))) blocked.push(MARKER_DICT_PATH);
  const surface = altitudeEnvelopeViolation(doc);
  if (surface !== null && !blocked.includes(surface)) blocked.push(surface);
  for (const conflict of blockingConflicts(doc)) {
    for (const path of conflict.paths) if (!blocked.includes(path)) blocked.push(path);
  }
  if (doc === null || typeof doc !== 'object') return blocked;
  const mode = readMissionMode(doc);
  const active = blocked.filter((path) => isPathActive(mode, path, doc));
  if (hasInvalidMissionMode(doc)) active.push(MISSION_MODE_PATH);
  return active;
}

function roundTo(value: number, precision: number): number {
  const factor = 10 ** precision;
  return Math.round(value * factor) / factor;
}

function signed(sign: 1 | -1, value: number): number {
  return value === 0 ? 0 : sign * value;
}

/**
 * `doc` with `value` written at the spec's path and at each of its mirrors.
 *
 * A finite value is rounded to the spec's precision before it is mirrored, so
 * both sides are exactly symmetric; a negative zero is never written. A path
 * that is not a sheet field, or a value that is not a finite number, is
 * written alone.
 */
export function setLinkedPath<T extends Doc>(doc: T, spec: ParameterSpec | string, value: unknown): T {
  const resolved = typeof spec === 'string' ? ALL_PARAMETERS.find((item) => item.path === spec) : spec;
  const path = typeof spec === 'string' ? spec : spec.path;
  if (resolved?.kind !== 'number' || !resolved.mirrors || !finite(value)) return setPath(doc, path, value);
  const rounded = roundTo(value, resolved.precision);
  let next = setPath(doc, path, rounded);
  for (const mirror of resolved.mirrors) next = setPath(next, mirror.path, signed(mirror.sign, rounded));
  return next;
}

/** Smallest multiple of 0.05 at or above `value`. */
function ceilToFiveHundredths(value: number): number {
  return Math.ceil(roundTo(value * 20, 6) - 1e-9) / 20;
}

/**
 * `doc` with every mirrored path forced to its source, and the horizontal
 * speed ceiling lifted to clear the forward cruise.
 *
 * The lift only ever raises: `max(current, ceil_0.05(forward + 0.05))`,
 * capped at {@link MAX_LIFTED_CEILING}, so a ceiling the operator already set
 * higher is kept. Idempotent; returns `doc` itself when nothing changes. A
 * source that is missing or not finite is left alone, and so is a ceiling
 * that is not a finite number: {@link launchBlockers} names both.
 */
export function reconcileCoupledPaths<T extends Doc>(doc: T): T {
  let next = doc;
  for (const spec of NUMBER_PARAMETERS) {
    const source = getPath(doc, spec.path);
    if (!spec.mirrors || !finite(source)) continue;
    for (const mirror of spec.mirrors) {
      const expected = signed(mirror.sign, roundTo(source, spec.precision));
      if (getPath(next, mirror.path) !== expected) next = setPath(next, mirror.path, expected);
    }
  }
  const forward = getPath(next, FORWARD_PATH);
  const ceiling = getPath(next, CEILING_PATH);
  if (finite(forward) && finite(ceiling)) {
    const lifted = Math.min(MAX_LIFTED_CEILING, ceilToFiveHundredths(Math.abs(forward) + CEILING_CLEARANCE));
    if (lifted > ceiling) next = setPath(next, CEILING_PATH, lifted);
  }
  if (getPath(next, COUNTDOWN_PATH) !== LAUNCH_COUNTDOWN_SEC) next = setPath(next, COUNTDOWN_PATH, LAUNCH_COUNTDOWN_SEC);
  return next;
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
 * `source` does not hold is left untouched. Mirrored fields follow their
 * source, so the factory defaults restore a return cruise of minus the
 * forward cruise rather than the dataclass's independent value.
 */
export function applySchemaFields<T extends Doc>(target: T, source: unknown): T {
  let next = target;
  for (const spec of ALL_PARAMETERS) {
    const value = getPath(source, spec.path);
    if (value !== undefined) next = setLinkedPath(next, spec, value);
  }
  return next;
}

/**
 * Why each advanced field of `doc` would block a launch, in Portuguese, keyed
 * by path. A field absent from the result is accepted.
 */
export function advancedFieldErrors(doc: unknown): Partial<Record<string, string>> {
  const errors: Partial<Record<string, string>> = {};
  const id = getPath(doc, MARKER_ID_PATH);
  const dict = getPath(doc, MARKER_DICT_PATH);
  const count = markerCount(dict);
  if (!finite(id) || !Number.isInteger(id) || id < 0) {
    errors[MARKER_ID_PATH] = 'O ID precisa ser um inteiro igual ou maior que zero.';
  } else if (count !== null && id >= count) {
    errors[MARKER_ID_PATH] = `O ID ${id} não existe em ${String(dict)} (válidos 0 a ${count - 1}).`;
  }
  if (!markerDictionaryAccepted(dict)) {
    errors[MARKER_DICT_PATH] = 'Dicionário não reconhecido: o pouso de precisão fica sem detector.';
  }
  const tag = getPath(doc, TAG_SIZE_PATH);
  if (!finite(tag) || tag <= 0 || tag > MAX_TAG_SIZE_M) {
    errors[TAG_SIZE_PATH] = `O tamanho precisa ser maior que zero e no máximo ${MAX_TAG_SIZE_M.toFixed(1)} m.`;
  }
  const window = getPath(doc, CENTERING_TIMEOUT_PATH);
  if (!finite(window) || !inEnvelope(CENTERING_TIMEOUT_PATH, window, doc)) {
    errors[CENTERING_TIMEOUT_PATH] = `A janela de centragem precisa ficar entre ${CENTERING_TIMEOUT_RANGE_SEC.min} e ${CENTERING_TIMEOUT_RANGE_SEC.max} s.`;
  }
  return errors;
}
