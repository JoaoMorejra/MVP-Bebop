/**
 * The operator's named favourites, a list per mission mode (D9).
 *
 * Kept in `localStorage` under `bmg.operator-presets.v2.<mode>`, per machine:
 * `mission_config.json` is not shared between stations either. Each favourite
 * holds only the values a mode owns (`MODE_OWNED_PATHS`): applying one never
 * moves PID gains, calibration statistics or the arming mode. The single
 * "Meu ajuste" of the earlier sheet (`bmg.operator-preset.<mode>`) becomes the
 * first item of the list.
 *
 * Every storage access is guarded: a store that refuses or holds corrupt JSON
 * reads as an empty list, with a console warning, and never takes the sheet
 * down. Pure apart from that storage and the console.
 */
import { normalizeForMode, reconcileCoupledPaths, setLinkedPath } from './launchDocument';
import { inactivePaths, type MissionMode } from './missionMode';
import { MODE_OWNED_PATHS, presetKey, readStoredDoc, type StorageLike } from './parameterModes';
import { deepEqual, getPath, type Doc } from './paths';

export const PRESETS_KEY_PREFIX = 'bmg.operator-presets.v2.';
/** Most favourites a mode keeps. */
export const MAX_PRESETS = 12;
/** Longest favourite name, in characters after trimming. */
export const MAX_PRESET_NAME = 40;
/** Name the single favourite of the earlier sheet is carried over under. */
export const LEGACY_PRESET_NAME = 'Meu ajuste';

export type PresetValue = number | boolean;

export interface OperatorPreset {
  /** `crypto.randomUUID()`. */
  id: string;
  /** 1 to {@link MAX_PRESET_NAME} characters, unique in its mode regardless of case. */
  name: string;
  /** ISO 8601 instant of the save. */
  savedAt: string;
  /** The mode-owned values of the document it was saved from. */
  values: Record<string, PresetValue>;
}

export const presetsKey = (mode: MissionMode): string => `${PRESETS_KEY_PREFIX}${mode}`;

function isPreset(value: unknown): value is OperatorPreset {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  if (typeof item.id !== 'string' || typeof item.name !== 'string' || typeof item.savedAt !== 'string') return false;
  if (!item.values || typeof item.values !== 'object' || Array.isArray(item.values)) return false;
  return Object.values(item.values as Record<string, unknown>).every(
    (entry) => typeof entry === 'boolean' || (typeof entry === 'number' && Number.isFinite(entry))
  );
}

/** The favourites of `mode`, oldest first; empty when absent, unreadable or corrupt. Never throws. */
export function readPresets(storage: StorageLike | null, mode: MissionMode): OperatorPreset[] {
  if (!storage) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(presetsKey(mode));
  } catch (error) {
    console.warn('[BMG] favoritos ilegíveis:', error);
    return [];
  }
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('not a list');
    return parsed.filter(isPreset).slice(0, MAX_PRESETS);
  } catch (error) {
    console.warn(`[BMG] favoritos de ${mode} corrompidos; lista vazia.`, error);
    return [];
  }
}

/** Persist the favourites of `mode`. Returns false when the store refused. Never throws. */
export function writePresets(storage: StorageLike | null, mode: MissionMode, presets: readonly OperatorPreset[]): boolean {
  if (!storage) return false;
  try {
    storage.setItem(presetsKey(mode), JSON.stringify(presets));
    return true;
  } catch (error) {
    console.warn('[BMG] favoritos não gravados:', error);
    return false;
  }
}

/** The mode-owned values of `doc` that are numbers or booleans. */
export function presetValues(doc: unknown): Record<string, PresetValue> {
  const values: Record<string, PresetValue> = {};
  for (const path of MODE_OWNED_PATHS) {
    const value = getPath(doc, path);
    if (typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))) values[path] = value;
  }
  return values;
}

/**
 * Why `name` cannot name a favourite of `presets`, in Portuguese; `null` when
 * it can. `exceptId` is the favourite being renamed, which may keep its name.
 */
export function presetNameError(presets: readonly OperatorPreset[], name: string, exceptId?: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return 'Dê um nome ao favorito.';
  if (trimmed.length > MAX_PRESET_NAME) return `Use no máximo ${MAX_PRESET_NAME} caracteres.`;
  const folded = trimmed.toLocaleLowerCase('pt-BR');
  if (presets.some((preset) => preset.id !== exceptId && preset.name.toLocaleLowerCase('pt-BR') === folded)) {
    return 'Já existe um favorito com esse nome neste modo.';
  }
  return null;
}

export type PresetEdit = { ok: true; presets: OperatorPreset[] } | { ok: false; error: string };

/** `presets` with `doc` saved as a new favourite named `name`, last. */
export function addPreset(
  presets: readonly OperatorPreset[],
  name: string,
  doc: unknown,
  id: string,
  now: Date = new Date()
): PresetEdit {
  if (presets.length >= MAX_PRESETS) return { ok: false, error: `Limite de ${MAX_PRESETS} favoritos por modo.` };
  const error = presetNameError(presets, name);
  if (error) return { ok: false, error };
  const preset: OperatorPreset = { id, name: name.trim(), savedAt: now.toISOString(), values: presetValues(doc) };
  return { ok: true, presets: [...presets, preset] };
}

/** `presets` with favourite `id` renamed to `name`. */
export function renamePreset(presets: readonly OperatorPreset[], id: string, name: string): PresetEdit {
  if (!presets.some((preset) => preset.id === id)) return { ok: false, error: 'Favorito não encontrado.' };
  const error = presetNameError(presets, name, id);
  if (error) return { ok: false, error };
  return { ok: true, presets: presets.map((preset) => (preset.id === id ? { ...preset, name: name.trim() } : preset)) };
}

/** `presets` without favourite `id`. */
export function deletePreset(presets: readonly OperatorPreset[], id: string): OperatorPreset[] {
  return presets.filter((preset) => preset.id !== id);
}

/** Name of the single favourite a mode keeps (D8). */
export const FAVORITE_NAME = 'Favorito';

/**
 * The most recently saved of `presets`, or `null` for an empty list; a tie
 * goes to the earlier item. A legacy list of several favourites reads as its
 * latest one until the operator saves the single favourite over it.
 */
export function latestPreset(presets: readonly OperatorPreset[]): OperatorPreset | null {
  let latest: OperatorPreset | null = null;
  let latestMs = Number.NEGATIVE_INFINITY;
  for (const preset of presets) {
    const parsed = Date.parse(preset.savedAt);
    const ms = Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
    if (latest === null || ms > latestMs) {
      latest = preset;
      latestMs = ms;
    }
  }
  return latest;
}

/** The favourite list of a mode reduced to one item, {@link FAVORITE_NAME}, holding the mode-owned values of `doc`. */
export function singleFavorite(doc: unknown, id: string, now: Date = new Date()): OperatorPreset[] {
  return [{ id, name: FAVORITE_NAME, savedAt: now.toISOString(), values: presetValues(doc) }];
}

/**
 * `doc` with the values of `preset`: mirrors follow their source, the altitude
 * is moved into the envelope of the launch surface and the coupled cruise
 * paths are reconciled, as on a mode switch.
 */
export function applyPresetValues<T extends Doc>(doc: T, preset: OperatorPreset): T {
  let next = doc;
  for (const [path, value] of Object.entries(preset.values)) next = setLinkedPath(next, path, value);
  return reconcileCoupledPaths(normalizeForMode(next));
}

/**
 * The favourite `doc` holds in `mode`, or `null`.
 *
 * Only the paths active for `(mode, doc)` are compared (a hidden field keeps
 * whatever it held), and a path the favourite does not hold is not compared.
 * The first match in list order wins.
 */
export function activePresetId(presets: readonly OperatorPreset[], mode: MissionMode, doc: unknown): string | null {
  if (!doc) return null;
  const inactive = inactivePaths(mode, doc);
  const match = presets.find((preset) =>
    Object.entries(preset.values).every(([path, value]) => inactive.has(path) || deepEqual(getPath(doc, path), value))
  );
  return match?.id ?? null;
}

/**
 * Carry the single favourite of the earlier sheet (`bmg.operator-preset.<mode>`)
 * into the list as {@link LEGACY_PRESET_NAME}, then remove the old key.
 * Idempotent: nothing happens once the old key is gone, and the item is not
 * added twice. Never throws.
 */
export function migrateSinglePreset(
  storage: StorageLike | null,
  mode: MissionMode,
  newId: () => string,
  now: Date = new Date()
): void {
  if (!storage) return;
  const legacy = readStoredDoc(storage, presetKey(mode));
  if (legacy) {
    const presets = readPresets(storage, mode);
    if (!presets.some((preset) => preset.name.toLocaleLowerCase('pt-BR') === LEGACY_PRESET_NAME.toLocaleLowerCase('pt-BR'))) {
      const added = addPreset(presets, LEGACY_PRESET_NAME, legacy, newId(), now);
      if (added.ok && !writePresets(storage, mode, added.presets)) return;
    }
  }
  try {
    storage.removeItem?.(presetKey(mode));
  } catch {
    /* the old key is never read again */
  }
}
