// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument } from '../../lib/__fixtures__/missionDocument';
import { getPath, setPath } from '../../lib/paths';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { ParameterSheet } from './ParameterSheet';
import { FavoritesMenu, relativeDate } from './FavoritesMenu';
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
import { DiscardChangesDialog } from './DiscardChangesDialog';

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

describe('FavoritesMenu', () => {
  const list = () => saved(saved([], 'Pista A', tuned()), 'Pista B', tuned());
  const trigger = () => Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.startsWith('Favoritos'))!;
  const byLabel = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLElement;
  const press = (key: string) =>
    act(() => {
      container.querySelector('[role="dialog"]')!.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
    });

  const renderMenu = (presets: OperatorPreset[], active: string | null = null, handlers: Partial<Record<string, unknown>> = {}) => {
    const props = {
      presets,
      activePresetId: active,
      onSave: vi.fn(() => null),
      onApply: vi.fn(),
      onRename: vi.fn(() => null),
      onDelete: vi.fn(),
      now: () => NOW,
      ...handlers,
    };
    act(() => root.render(<FavoritesMenu {...(props as React.ComponentProps<typeof FavoritesMenu>)} />));
    return props;
  };

  it('counts the favourites and opens a labelled panel, marking the active one', () => {
    const presets = list();
    renderMenu(presets, presets[1].id);
    expect(trigger().textContent).toBe('Favoritos (2)');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    act(() => trigger().click());
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('[role="dialog"]')!.getAttribute('aria-label')).toBe('Favoritos deste modo');
    const items = Array.from(container.querySelectorAll('[data-favorite]'));
    expect(items.map((item) => item.getAttribute('aria-current'))).toEqual([null, 'true']);
    expect(container.textContent).toContain('agora');
    expect(trigger().querySelector('[data-preset-star]')!.getAttribute('data-preset-star')).toBe('filled');
  });

  it('applies a favourite and closes', () => {
    const presets = list();
    const props = renderMenu(presets);
    act(() => trigger().click());
    act(() => byLabel('Aplicar Pista B').click());
    expect(props.onApply).toHaveBeenCalledWith(presets[1].id);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it('saves the current values under a typed name, showing a refusal', () => {
    const onSave = vi.fn((name: string) => (name === 'Pista A' ? 'Já existe um favorito com esse nome neste modo.' : null));
    renderMenu(list(), null, { onSave });
    act(() => trigger().click());
    const saveButton = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Salvar configuração atual')!;
    act(() => saveButton.click());
    const field = byLabel('Nome do favorito') as HTMLInputElement;
    const type = (value: string) =>
      act(() => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        setter.call(field, value);
        field.dispatchEvent(new Event('input', { bubbles: true }));
      });
    type('Pista A');
    act(() => field.form!.requestSubmit());
    expect(container.querySelector('[role="alert"]')!.textContent).toMatch(/Já existe/);
    type('Pista C');
    act(() => field.form!.requestSubmit());
    expect(onSave).toHaveBeenLastCalledWith('Pista C');
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('asks before deleting', () => {
    const presets = list();
    const props = renderMenu(presets);
    act(() => trigger().click());
    act(() => byLabel('Excluir Pista A').click());
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(container.textContent).toContain('Excluir “Pista A”?');
    const confirm = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Excluir')!;
    act(() => confirm.click());
    expect(props.onDelete).toHaveBeenCalledWith(presets[0].id);
  });

  it('renames in place', () => {
    const presets = list();
    const props = renderMenu(presets);
    act(() => trigger().click());
    act(() => byLabel('Renomear Pista A').click());
    const field = byLabel('Novo nome de Pista A') as HTMLInputElement;
    expect(field.value).toBe('Pista A');
    act(() => field.form!.requestSubmit());
    expect(props.onRename).toHaveBeenCalledWith(presets[0].id, 'Pista A');
  });

  it('moves between favourites with the arrow keys and closes on Escape, back on the trigger', () => {
    renderMenu(list());
    act(() => trigger().click());
    expect(document.activeElement).toBe(byLabel('Aplicar Pista A'));
    press('ArrowDown');
    expect(document.activeElement).toBe(byLabel('Aplicar Pista B'));
    press('ArrowDown');
    expect(document.activeElement).toBe(byLabel('Aplicar Pista A'));
    press('Escape');
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('disables saving at the limit of 12', () => {
    let presets: OperatorPreset[] = [];
    for (let i = 0; i < MAX_PRESETS; i += 1) presets = saved(presets, `F${i}`, tuned());
    renderMenu(presets);
    act(() => trigger().click());
    const saveButton = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Salvar configuração atual')!;
    expect((saveButton as HTMLButtonElement).disabled).toBe(true);
  });

  it('dates a favourite relative to now', () => {
    expect(relativeDate('2026-10-21T11:55:00Z', NOW)).toBe('há 5 min');
    expect(relativeDate('2026-10-21T09:00:00Z', NOW)).toBe('há 3 h');
    expect(relativeDate('2026-10-18T12:00:00Z', NOW)).toBe('há 3 dias');
  });
});

describe('ParameterSheet', () => {
  it('replaces the single preset with the favourites menu and no longer shows the config file name', () => {
    act(() =>
      root.render(
        <ParameterSheet
          working={tuned()}
          defaults={tuned()}
          changedPaths={new Set()}
          dirty={false}
          saving={false}
          presets={[]}
          activePresetId={null}
          onEdit={() => undefined}
          onSave={() => undefined}
          onDiscard={() => undefined}
          onSavePreset={() => null}
          onRenamePreset={() => null}
          onDeletePreset={() => undefined}
          onApplyPreset={() => undefined}
        />
      )
    );
    expect(container.textContent).toContain('Favoritos (0)');
    expect(container.textContent).not.toContain('Meu ajuste');
    expect(container.textContent).not.toContain('mission_config.json');
  });
});

describe('DiscardChangesDialog', () => {
  it('asks the exact question and focuses the safe answer', () => {
    act(() => root.render(<DiscardChangesDialog onConfirm={() => undefined} onCancel={() => undefined} />));
    expect(container.textContent).toContain('As alterações não salvas serão descartadas. Deseja continuar?');
    expect(document.activeElement?.textContent).toBe('Continuar editando');
  });

  it('discards only on the explicit confirm', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    act(() => root.render(<DiscardChangesDialog onConfirm={onConfirm} onCancel={onCancel} />));
    const [keep, discard] = Array.from(container.querySelectorAll('button'));

    act(() => keep.click());
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    act(() => discard.click());
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('treats Escape as keep editing', () => {
    const onCancel = vi.fn();
    act(() => root.render(<DiscardChangesDialog onConfirm={() => undefined} onCancel={onCancel} />));
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
