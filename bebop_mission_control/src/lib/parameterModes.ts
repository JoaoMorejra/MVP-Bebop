/**
 * Flight parameters and the operator's favourite, kept apart for each mission mode.
 *
 * The station edits one document (`mission_config.json`), but an operator tunes
 * three different missions: the figures that suit the Simple mission are not the
 * ones of the Advanced. Each mode therefore keeps its own last-used document and
 * its own favourite in `localStorage`, under `bmg.working-parameters.<mode>` and
 * `bmg.operator-preset.<mode>`. Whole documents are stored, but only the paths a
 * mode OWNS are ever copied back (`MODE_OWNED_PATHS`): PID gains, calibration
 * statistics and the arming mode are shared and never travel between modes.
 */
import type { MissionMode } from './missionMode';
import { LEGACY_INTERMEDIATE_MODE, MISSION_MODE_PATH, RTL_ENABLED_PATH } from './missionMode';
import { normalizeForMode, reconcileCoupledPaths } from './launchDocument';
import { ADVANCED_PATHS, ALL_PARAMETERS } from './parameterSchema';
import { getPath, setPath, type Doc } from './paths';

export const WORKING_KEY_PREFIX = 'bmg.working-parameters.';
export const PRESET_KEY_PREFIX = 'bmg.operator-preset.';
export const ACTIVE_MODE_KEY = 'bmg.active-mode';

const LEGACY_WORKING_KEY = 'bmg.working-parameters.v2';
const LEGACY_PRESET_KEY = 'bmg.operator-preset.v2';

export const workingKey = (mode: MissionMode): string => `${WORKING_KEY_PREFIX}${mode}`;
export const presetKey = (mode: MissionMode): string => `${PRESET_KEY_PREFIX}${mode}`;

/** The part of `Storage` this module touches. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
}

/** Paths whose values belong to a mode: the sheet fields, their mirrors and the advanced pad fields. */
export const MODE_OWNED_PATHS: readonly string[] = Array.from(
  new Set([
    ...ALL_PARAMETERS.flatMap((spec) => [
      spec.path,
      ...(spec.kind === 'number' ? spec.mirrors?.map((mirror) => mirror.path) ?? [] : []),
    ]),
    ...ADVANCED_PATHS,
  ])
);

/** `target` with the owned paths `source` holds copied in; everything else in `target` is kept. */
export function applyOwnedPaths<T extends Doc>(target: T, source: unknown): T {
  let next = target;
  for (const path of MODE_OWNED_PATHS) {
    const value = getPath(source, path);
    if (value !== undefined) next = setPath(next, path, value);
  }
  return next;
}

/** A stored document, or null when absent, unreadable, or not a JSON object. Never throws. */
export function readStoredDoc(storage: StorageLike | null, key: string): Doc | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Doc) : null;
  } catch {
    return null;
  }
}

/** Persist a document. Returns false when the store refused. Never throws. */
export function writeStoredDoc(storage: StorageLike | null, key: string, doc: Doc): boolean {
  if (!storage) return false;
  try {
    storage.setItem(key, JSON.stringify(doc));
    return true;
  } catch {
    return false;
  }
}

/**
 * Carry the single pre-modes document and favourite over to the Advanced mode,
 * the only mode that existed when they were written. Idempotent and
 * non-destructive: the old keys stay, and nothing already stored is overwritten.
 */
export function migrateLegacyKeys(storage: StorageLike | null): void {
  const pairs: ReadonlyArray<readonly [string, string]> = [
    [LEGACY_WORKING_KEY, workingKey('advanced')],
    [LEGACY_PRESET_KEY, presetKey('advanced')],
  ];
  for (const [legacy, target] of pairs) {
    if (readStoredDoc(storage, target)) continue;
    const old = readStoredDoc(storage, legacy);
    if (old) writeStoredDoc(storage, target, old);
  }
}

export interface ModeSwitchInput {
  /** The document being edited, with any unsaved edits. */
  working: Doc;
  from: MissionMode;
  to: MissionMode;
  /** What `to` last used, or null the first time it is entered. */
  stored: Doc | null;
}

export interface ModeSwitchResult {
  /** The document to edit in `to`. */
  doc: Doc;
  /** The document to store for `from`, so leaving it loses no edit. */
  stash: Doc;
  /** False when `to` had nothing stored and starts from a copy of the current values. */
  usedStored: boolean;
}

/**
 * Switch the editing document from one mode to another.
 *
 * Pure. The target mode's owned values come from `stored` when it has any,
 * otherwise from a copy of `working` (first use). The altitude is then moved
 * into the envelope of the launch surface and the coupled cruise paths are
 * reconciled.
 */
export function switchModeDocument({ working, from, to, stored }: ModeSwitchInput): ModeSwitchResult {
  const stash = setPath(working, MISSION_MODE_PATH, from);
  const base = stored ? applyOwnedPaths(working, stored) : working;
  const moved = setPath(base, MISSION_MODE_PATH, to);
  const doc = reconcileCoupledPaths(normalizeForMode(moved));
  return { doc, stash, usedStored: stored !== null };
}

/**
 * Carry the keys of the removed Intermediate profile over to the Advanced one.
 *
 * `bmg.active-mode = 'intermediate'` becomes `'advanced'`, and the Advanced
 * working document receives `rtl.enabled = false` (the Intermediate profile was
 * the Advanced one without the return); an Advanced document that does not
 * exist yet is started from the Intermediate one. The Intermediate working
 * document and favourite keys are then removed. Idempotent; every storage
 * fault is swallowed, the document on disk being the source of truth.
 */
export function migrateLegacyIntermediate(storage: StorageLike | null): void {
  if (!storage) return;
  const legacyWorking = `${WORKING_KEY_PREFIX}${LEGACY_INTERMEDIATE_MODE}`;
  const legacyPreset = `${PRESET_KEY_PREFIX}${LEGACY_INTERMEDIATE_MODE}`;
  let active: string | null = null;
  try {
    active = storage.getItem(ACTIVE_MODE_KEY);
  } catch {
    active = null;
  }
  if (active !== null && active.trim().toLowerCase() === LEGACY_INTERMEDIATE_MODE) {
    const base = readStoredDoc(storage, workingKey('advanced')) ?? readStoredDoc(storage, legacyWorking);
    if (base) {
      const migrated = setPath(setPath(base, MISSION_MODE_PATH, 'advanced'), RTL_ENABLED_PATH, false);
      writeStoredDoc(storage, workingKey('advanced'), migrated);
    }
    try {
      storage.setItem(ACTIVE_MODE_KEY, 'advanced');
    } catch {
      /* the next mode switch writes it again */
    }
  }
  for (const key of [legacyWorking, legacyPreset]) {
    try {
      storage.removeItem?.(key);
    } catch {
      /* an undeletable key is never read again */
    }
  }
}
