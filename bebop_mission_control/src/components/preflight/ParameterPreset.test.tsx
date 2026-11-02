// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument } from '../../lib/__fixtures__/missionDocument';
import { getPath, setPath } from '../../lib/paths';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { ParameterSheet } from './ParameterSheet';
import {
  LEGACY_PRESET_NAME,
  MAX_PRESETS,
  activePresetId,
  addPreset,
  applyPresetValues,
  deletePreset,
  migrateSinglePreset,
  presetNameError,
  presetsKey,
  readPresets,
  renamePreset,
  writePresets,
  type OperatorPreset,
} from '../../lib/operatorPresets';
import { MODE_OWNED_PATHS, presetKey } from '../../lib/parameterModes';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

function tuned(): ParamsDoc {
  return completeDocument({ 'calibration.samples': 12 });
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

class MemoryStorage {
  data = new Map<string, string>();
  getItem(key: string) { return this.data.has(key) ? this.data.get(key)! : null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

const NOW = new Date('2026-10-21T12:00:00Z');
let counter = 0;
const nextId = () => `id-${(counter += 1)}`;

function saved(list: OperatorPreset[], name: string, doc: unknown): OperatorPreset[] {
  const result = addPreset(list, name, doc, nextId(), NOW);
  if (!result.ok) throw new Error(result.error);
  return result.presets;
}

describe('operator favourites (lib)', () => {
  it('saves only the values a mode owns, numbers and booleans', () => {
    const [preset] = saved([], 'Pista A', setPath(tuned(), 'no_fly', true));
    expect(Object.keys(preset.values).every((path) => MODE_OWNED_PATHS.includes(path))).toBe(true);
    expect(preset.values).not.toHaveProperty('no_fly');
    expect(preset.values['rtl.enabled']).toBe(true);
    expect(preset.savedAt).toBe(NOW.toISOString());
  });

  it('refuses an empty, a too long or a duplicate name regardless of case', () => {
    const list = saved([], 'Pista A', tuned());
    expect(presetNameError(list, '   ')).toMatch(/nome/);
    expect(presetNameError(list, 'x'.repeat(41))).toMatch(/40/);
    expect(presetNameError(list, ' pista a ')).toMatch(/Já existe/);
    expect(presetNameError(list, 'Pista A', list[0].id)).toBeNull();
    expect(addPreset(list, 'PISTA A', tuned(), nextId())).toEqual({ ok: false, error: expect.stringMatching(/Já existe/) });
  });

  it('keeps at most 12 favourites per mode', () => {
    let list: OperatorPreset[] = [];
    for (let i = 0; i < MAX_PRESETS; i += 1) list = saved(list, `F${i}`, tuned());
    expect(addPreset(list, 'mais um', tuned(), nextId())).toEqual({ ok: false, error: expect.stringMatching(/12/) });
  });

  it('renames and deletes by id', () => {
    const list = saved(saved([], 'A', tuned()), 'B', tuned());
    const renamed = renamePreset(list, list[0].id, '  C  ');
    expect(renamed.ok && renamed.presets.map((p) => p.name)).toEqual(['C', 'B']);
    expect(renamePreset(list, list[0].id, 'b')).toEqual({ ok: false, error: expect.stringMatching(/Já existe/) });
    expect(deletePreset(list, list[1].id).map((p) => p.name)).toEqual(['A']);
  });

  it('applies the values through the envelope and leaves the rest of the document', () => {
    const [preset] = saved([], 'Alto', setPath(tuned(), 'kinematics.target_altitude_m', 1.9));
    const target = setPath(setPath(tuned(), 'lateral_pid.kp', 0.77), 'platform.height_m', 1);
    const applied = applyPresetValues(setPath(target, 'kinematics.target_altitude_m', 1.4), preset);
    expect(getPath(applied, 'kinematics.target_altitude_m')).toBe(1.9);
    expect(getPath(applied, 'lateral_pid.kp')).toBe(0.77);
    const low = saved([], 'Baixo', setPath(tuned(), 'kinematics.target_altitude_m', 0.5))[0];
    expect(getPath(applyPresetValues(target, low), 'kinematics.target_altitude_m')).toBe(1.3);
  });

  it('finds the active favourite comparing only the paths active for the mode and the document', () => {
    const doc = tuned();
    const list = saved([], 'A', doc);
    expect(activePresetId(list, 'advanced', doc)).toBe(list[0].id);
    expect(activePresetId(list, 'advanced', setPath(doc, 'kinematics.target_altitude_m', 1.95))).toBeNull();
    // The turn rate is not flown in the advanced mode: a change there keeps the favourite active.
    expect(activePresetId(list, 'advanced', setPath(doc, 'yaw_pattern.scan_vyaw', 0.25))).toBe(list[0].id);
    expect(activePresetId(list, 'simple', setPath(doc, 'yaw_pattern.scan_vyaw', 0.25))).toBeNull();
    expect(activePresetId([], 'advanced', doc)).toBeNull();
  });

  it('keeps the favourites of each mode apart in storage', () => {
    const storage = new MemoryStorage();
    writePresets(storage, 'simple', saved([], 'S', tuned()));
    expect(readPresets(storage, 'simple').map((p) => p.name)).toEqual(['S']);
    expect(readPresets(storage, 'advanced')).toEqual([]);
    expect(presetsKey('simple')).toBe('bmg.operator-presets.v2.simple');
  });

  it('reads corrupt JSON as an empty list, with a warning, and never throws', () => {
    const storage = new MemoryStorage();
    storage.setItem(presetsKey('advanced'), '{not json');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(readPresets(storage, 'advanced')).toEqual([]);
    expect(warn).toHaveBeenCalled();
    const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
    expect(readPresets(broken, 'advanced')).toEqual([]);
    expect(writePresets(broken, 'advanced', [])).toBe(false);
    warn.mockRestore();
  });

  it('migrates the single "Meu ajuste" into the list once and removes the old key', () => {
    const storage = new MemoryStorage();
    storage.setItem(presetKey('advanced'), JSON.stringify(setPath(tuned(), 'kinematics.target_altitude_m', 1.7)));
    migrateSinglePreset(storage, 'advanced', nextId, NOW);
    migrateSinglePreset(storage, 'advanced', nextId, NOW);
    const list = readPresets(storage, 'advanced');
    expect(list.map((p) => p.name)).toEqual([LEGACY_PRESET_NAME]);
    expect(list[0].values['kinematics.target_altitude_m']).toBe(1.7);
    expect(storage.getItem(presetKey('advanced'))).toBeNull();
  });
});

describe('ParameterSheet favourite', () => {
  const sheet = (overrides: Partial<React.ComponentProps<typeof ParameterSheet>> = {}) => {
    const props = {
      working: tuned(),
      defaults: tuned(),
      changedPaths: new Set<string>(),
      dirty: false,
      saving: false,
      favoriteActive: false,
      hasFavorite: false,
      onEdit: vi.fn(),
      onSave: () => undefined,
      onDiscard: () => undefined,
      onSaveFavorite: vi.fn(),
      onRestoreFavorite: vi.fn(),
      ...overrides,
    };
    act(() => root.render(<ParameterSheet {...props} />));
    return props;
  };
  const star = () => container.querySelector<HTMLButtonElement>('button[aria-label="Salvar como favorito"]')!;
  const restore = () =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Restaurar Padrões'))!;

  it('the star is outlined without a favorite and filled when the working values match it', () => {
    sheet();
    expect(star().getAttribute('aria-pressed')).toBe('false');
    expect(star().getAttribute('data-filled')).toBe('false');
    sheet({ hasFavorite: true, favoriteActive: false });
    expect(star().getAttribute('aria-pressed')).toBe('false');
    sheet({ hasFavorite: true, favoriteActive: true });
    expect(star().getAttribute('aria-pressed')).toBe('true');
    expect(star().getAttribute('data-filled')).toBe('true');
  });

  it('clicking the star calls onSaveFavorite', () => {
    const props = sheet();
    act(() => star().click());
    expect(props.onSaveFavorite).toHaveBeenCalledTimes(1);
  });

  it('Restaurar Padrões calls onRestoreFavorite when a favorite exists, and falls back to factory defaults otherwise', () => {
    const withFavorite = sheet({ hasFavorite: true });
    act(() => restore().click());
    expect(withFavorite.onRestoreFavorite).toHaveBeenCalledTimes(1);
    expect(withFavorite.onEdit).not.toHaveBeenCalled();

    const factory = sheet({ hasFavorite: false });
    act(() => restore().click());
    expect(factory.onRestoreFavorite).not.toHaveBeenCalled();
    expect(factory.onEdit).toHaveBeenCalled();
  });

  it('has no favourites menu and no config file name', () => {
    sheet();
    expect(container.textContent).not.toContain('Favoritos');
    expect(container.textContent).not.toContain('Meu ajuste');
    expect(container.textContent).not.toContain('mission_config.json');
  });
});
