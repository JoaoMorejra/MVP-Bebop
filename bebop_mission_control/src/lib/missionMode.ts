/**
 * The two mission profiles the pre-flight screen offers.
 *
 * Mirrors `mvp_mission_bebop/mission_mode.py`: the value travels in the launch
 * document as `mission_mode` and the mission resolves it once, after the go.
 * Both fly the five stages; only what stages 2 to 4 do differs, so the
 * cockpit, the stage bar and the `[STEP N:` markers are shared. What stage 5
 * does is the return plan, `rtl.enabled`, chosen per flight in either profile.
 * The former Intermediate profile is the Advanced one without the return;
 * stored documents and keys that name it are migrated on load
 * (`launchDocument.ts:migrateLegacyMode`, `parameterModes.ts:migrateLegacyIntermediate`).
 */
import { marginUnlocked } from './altitudeEnvelope';
import { getPath } from './paths';

export type MissionMode = 'simple' | 'advanced';

export const MISSION_MODE_PATH = 'mission_mode';

export const DEFAULT_MISSION_MODE: MissionMode = 'advanced';

export interface MissionModeDescriptor {
  id: MissionMode;
  label: string;
  /** One line, shown under the selector. */
  summary: string;
}

export const MISSION_MODES: ReadonlyArray<MissionModeDescriptor> = [
  {
    id: 'simple',
    label: 'Simples',
    summary: 'Giro no lugar, foto e retorno do rumo',
  },
  {
    id: 'advanced',
    label: 'Avançado',
    summary: 'Varredura lateral, foto e retorno lateral',
  },
];

/** The removed profile, still found in stored documents and keys written before it was dropped. */
export const LEGACY_INTERMEDIATE_MODE = 'intermediate';

/**
 * The mode named by `raw`, or `null` when it is not one of the two. Case and
 * spaces are ignored. The removed `intermediate` is `null` here: a document is
 * migrated (`launchDocument.ts:migrateLegacyMode`) before its mode is read.
 */
export function parseMissionMode(raw: unknown): MissionMode | null {
  if (typeof raw !== 'string') return null;
  const token = raw.trim().toLowerCase();
  return token === 'simple' || token === 'advanced' ? token : null;
}

/** The mode a launch document asks for. A document that names none is the full mission. */
export function readMissionMode(doc: unknown): MissionMode {
  return parseMissionMode(getPath(doc, MISSION_MODE_PATH)) ?? DEFAULT_MISSION_MODE;
}

/** Whether the document names a mode that is neither absent nor one of the two. */
export function hasInvalidMissionMode(doc: unknown): boolean {
  const raw = getPath(doc, MISSION_MODE_PATH);
  if (raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '')) return false;
  return parseMissionMode(raw) === null;
}

export const RTL_ENABLED_PATH = 'rtl.enabled';

const INACTIVE_BY_MODE: Readonly<Record<MissionMode, readonly string[]>> = {
  advanced: ['yaw_pattern.scan_vyaw'],
  simple: ['rtl.timeout_sec', 'rtl.reverse_cruise_velocity', 'kinematics.forward_cruise_velocity'],
};

/** The return-leg and marker fields: not read by a flight that lands in place. */
const RETURN_PATHS: readonly string[] = [
  'rtl.timeout_sec',
  'rtl.reverse_cruise_velocity',
  'rtl.target_aruco_id',
  'rtl.marker_dict',
  'rtl.tag_size',
  'rtl.centering_timeout_sec',
  'rtl.camera_tilt_deg',
];

/** The return plan of `doc`: true unless `rtl.enabled` is exactly `false`. */
export function readRtlEnabled(doc: unknown): boolean {
  return getPath(doc, RTL_ENABLED_PATH) !== false;
}

/**
 * The document paths a flight in `mode` with the plan of `doc` does not read.
 *
 * An inactive path stays in the document with its stored value; it is only
 * hidden from the sheet and left out of the launch checks, so an operator is
 * never blocked by a field they cannot see.
 */
export function inactivePaths(mode: MissionMode, doc?: unknown): ReadonlySet<string> {
  const paths = new Set(INACTIVE_BY_MODE[mode]);
  if (!readRtlEnabled(doc)) for (const path of RETURN_PATHS) paths.add(path);
  // On the pad top at or below 2.0 m the ceiling is fixed and the margin is not read (D2).
  if (!marginUnlocked(doc)) paths.add('kinematics.altitude_ceiling_margin_m');
  return paths;
}

/** Whether a document path matters to a flight in `mode` with the plan of `doc` ({@link inactivePaths}). */
export function isPathActive(mode: MissionMode, path: string, doc?: unknown): boolean {
  return !inactivePaths(mode, doc).has(path);
}

/**
 * Name of stage `step`; only the last stage changes, by the return plan: with
 * the return, "Pouso na base" (Simple) or "Retorno à base" (Advanced); without
 * it, "Pouso no local" in either profile.
 */
export function stageLabel(mode: MissionMode, step: number, fallback: string, rtlEnabled = true): string {
  if (step !== 5) return fallback;
  if (!rtlEnabled) return 'Pouso no local';
  return mode === 'simple' ? 'Pouso na base' : 'Retorno à base';
}
