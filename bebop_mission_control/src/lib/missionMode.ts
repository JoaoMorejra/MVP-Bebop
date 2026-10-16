/**
 * The three mission profiles the pre-flight screen offers.
 *
 * Mirrors `mvp_mission_bebop/mission_mode.py`: the value travels in the launch
 * document as `mission_mode` and the mission resolves it once, after the go.
 * All three fly the five stages; only what stages 2 to 5 do differs, so the
 * cockpit, the stage bar and the `[STEP N:` markers are shared.
 */
import { getPath } from './paths';

export type MissionMode = 'simple' | 'intermediate' | 'advanced';

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
    summary: 'Gira sobre o próprio eixo até achar o sinistro, fotografa e pousa na base. Não se desloca.',
  },
  {
    id: 'intermediate',
    label: 'Intermediária',
    summary: 'Missão completa até a foto; depois pousa onde a inspeção terminou, sem retorno.',
  },
  {
    id: 'advanced',
    label: 'Avançada',
    summary: 'Missão completa: varredura, inspeção, retorno pelo marcador ArUco e pouso na base.',
  },
];

/** The mode named by `raw`, or `null` when it is not one of the three. Case and spaces are ignored. */
export function parseMissionMode(raw: unknown): MissionMode | null {
  if (typeof raw !== 'string') return null;
  const token = raw.trim().toLowerCase();
  return token === 'simple' || token === 'intermediate' || token === 'advanced' ? token : null;
}

/** The mode a launch document asks for. A document that names none is the full mission. */
export function readMissionMode(doc: unknown): MissionMode {
  return parseMissionMode(getPath(doc, MISSION_MODE_PATH)) ?? DEFAULT_MISSION_MODE;
}

/** Whether the document names a mode that is neither absent nor one of the three. */
export function hasInvalidMissionMode(doc: unknown): boolean {
  const raw = getPath(doc, MISSION_MODE_PATH);
  if (raw === undefined || raw === null || (typeof raw === 'string' && raw.trim() === '')) return false;
  return parseMissionMode(raw) === null;
}

/**
 * Flight altitude the simple sheet offers, in metres ABOVE THE LAUNCH POINT (the
 * top of the pad). The over-floor range is `platform.height_m` plus this one,
 * see {@link simpleAltitudeRange}. The ceiling is the native
 * takeoff hover height (`FIRMWARE_HOVER_HEIGHT_M` in `parameterConflicts.ts`,
 * kept equal by test; importing it here would close a cycle). The floor keeps
 * `altitude + pinned ceiling margin >= 1.3 m, above the W01 floor of 1.21 m`, so the margin the sheet hides
 * never appears in a conflict.
 */
export const SIMPLE_ALTITUDE_RANGE_M = { min: 0.7, max: 1.0 } as const;

export const PLATFORM_HEIGHT_PATH = 'platform.height_m';

/**
 * Over-floor range of `kinematics.target_altitude_m` in the simple mode for the
 * pad of `doc`: `platform.height_m + SIMPLE_ALTITUDE_RANGE_M`. The pad is 0 when
 * the document holds no finite height there. The bounds are rounded inward to
 * 0.01 m (min up, max down), so a pad with three decimals never yields a
 * ceiling above the one the mission accepts (`pad + NATIVE_HOVER_HEIGHT_M`).
 */
export function simpleAltitudeRange(doc?: unknown): { min: number; max: number } {
  const raw = getPath(doc, PLATFORM_HEIGHT_PATH);
  const pad = typeof raw === 'number' && Number.isFinite(raw) ? raw : 0;
  const noise = 1e-9;
  const up = (value: number): number => Math.ceil(value * 100 - noise) / 100;
  const down = (value: number): number => Math.floor(value * 100 + noise) / 100;
  return { min: up(pad + SIMPLE_ALTITUDE_RANGE_M.min), max: down(pad + SIMPLE_ALTITUDE_RANGE_M.max) };
}

const INACTIVE_PATHS: Readonly<Record<MissionMode, ReadonlySet<string>>> = {
  advanced: new Set(['yaw_pattern.scan_vyaw']),
  intermediate: new Set([
    'yaw_pattern.scan_vyaw',
    'rtl.timeout_sec',
    'rtl.reverse_cruise_velocity',
    'rtl.target_aruco_id',
    'rtl.marker_dict',
    'rtl.tag_size',
    'rtl.centering_timeout_sec',
    'rtl.camera_tilt_deg',
  ]),
  simple: new Set([
    'rtl.timeout_sec',
    'rtl.reverse_cruise_velocity',
    'kinematics.forward_cruise_velocity',
    'kinematics.altitude_ceiling_margin_m',
  ]),
};

/**
 * Whether a document path matters to a flight in `mode`.
 *
 * An inactive path stays in the document with its stored value; it is only
 * hidden from the sheet and left out of the launch checks, so an operator is
 * never blocked by a field they cannot see.
 */
export function isPathActive(mode: MissionMode, path: string): boolean {
  return !INACTIVE_PATHS[mode].has(path);
}

const LAST_STAGE_LABEL: Readonly<Partial<Record<MissionMode, string>>> = {
  intermediate: 'Pouso no local',
  simple: 'Pouso na base',
};

/** Name of stage `step` in `mode`; only the last stage changes. */
export function stageLabel(mode: MissionMode, step: number, fallback: string): string {
  return step === 5 ? LAST_STAGE_LABEL[mode] ?? fallback : fallback;
}
