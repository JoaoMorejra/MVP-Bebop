// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument, validDocument } from '../../lib/__fixtures__/missionDocument';
import { activeConflicts } from '../../lib/parameterConflicts';
import { ALL_PARAMETERS, PARAMETER_GROUPS } from '../../lib/parameterSchema';
import { getPath, setPath } from '../../lib/paths';
import { ParameterSheet } from './ParameterSheet';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
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

function render(working: Record<string, unknown>, defaults: Record<string, unknown> | null, onEdit = vi.fn()) {
  act(() =>
    root.render(
      <ParameterSheet
        working={working}
        defaults={defaults}
        changedPaths={new Set()}
        dirty={false}
        saving={false}
        presets={[]}
        activePresetId={null}
        onEdit={onEdit}
        onSave={() => undefined}
        onDiscard={() => undefined}
        onSavePreset={() => null}
          onRenamePreset={() => null}
          onDeletePreset={() => undefined}
        onApplyPreset={() => undefined}
      />
    )
  );
  return onEdit;
}

const button = (label: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(label))!;

describe('ParameterSheet defaults', () => {
  it('prints the default the mission holds, not one of its own', () => {
    const defaults = setPath(completeDocument(), 'kinematics.target_altitude_m', 1.0);
    render(completeDocument(), defaults);
    expect(container.textContent).toContain('padrão 1.00m');
  });

  it('restores every sheet field from the mission defaults', () => {
    const defaults = completeDocument();
    const onEdit = render(completeDocument({ 'kinematics.target_altitude_m': 3.3 }), defaults);
    act(() => button('Restaurar Padrões').click());
    for (const spec of ALL_PARAMETERS) expect(onEdit).toHaveBeenCalledWith(spec.path, getPath(defaults, spec.path));
  });

  it('cannot restore without the mission defaults', () => {
    render(completeDocument(), null);
    expect(button('Restaurar Padrões').disabled).toBe(true);
    expect(container.textContent).not.toContain('padrão');
  });

  it('shows a missing value as missing instead of a default', () => {
    render(completeDocument({ 'kinematics.target_altitude_m': null }), completeDocument());
    expect(container.querySelector('[data-missing-value="kinematics.target_altitude_m"]')).not.toBeNull();
  });

  it('lists the countdown with the other figures', () => {
    render(completeDocument(), completeDocument());
    expect(container.textContent).toContain('Contagem Regressiva');
  });
});

describe('ParameterSheet slider ranges', () => {
  it.each([
    ['kinematics.target_altitude_m', '0.1', '2'],
    ['kinematics.altitude_ceiling_margin_m', '0', '2'],
    ['kinematics.forward_cruise_velocity', '0.01', '0.6'],
    ['gimbal.search_tilt_deg', '-45', '0'],
    ['gimbal.nadir_tilt_deg', '-80', '0'],
  ])('draws %s from %s to %s', (path, min, max) => {
    render(completeDocument(), null);
    const spec = ALL_PARAMETERS.find((item) => item.path === path)!;
    const range = container.querySelector(`input[type="range"][aria-label="${spec.label}"]`) as HTMLInputElement;
    expect(range.min).toBe(min);
    expect(range.max).toBe(max);
  });
});

describe('ParameterSheet launch refusal', () => {
  it('marks the field the mission refused at the launch as an error', () => {
    act(() =>
      root.render(
        <ParameterSheet
          working={completeDocument()}
          defaults={null}
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
          refusedPath="kinematics.target_altitude_m"
        />
      )
    );
    const field = container.querySelector('[data-parameter="kinematics.target_altitude_m"]');
    expect(field?.getAttribute('data-conflict')).toBe('error');
    expect(container.querySelector('[data-parameter="kinematics.takeoff_stabilize_duration_sec"]')?.getAttribute('data-conflict')).toBeNull();
  });
});

describe('ParameterSheet advanced settings', () => {
  const pad = (overrides: Record<string, unknown> = {}) =>
    completeDocument({
      'rtl.target_aruco_id': 8,
      'rtl.marker_dict': 'DICT_APRILTAG_36h11',
      'rtl.tag_size': 0.2,
      'rtl.centering_timeout_sec': 25,
      ...overrides,
    });
  const dialog = () => document.querySelector('[role="dialog"][aria-labelledby="advanced-settings-title"]');
  const field = (label: string) => dialog()!.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
  const dialogButton = (label: string) =>
    Array.from(dialog()!.querySelectorAll('button')).find((b) => b.textContent?.includes(label))!;
  const setValue = (input: HTMLInputElement | HTMLSelectElement, value: string) => {
    const proto = input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  };

  it('opens from the footer next to the preset button', () => {
    render(pad(), null);
    expect(dialog()).toBeNull();
    act(() => button('Configurações Avançadas').click());
    expect(dialog()).not.toBeNull();
    expect(dialog()!.textContent).toContain('IDs válidos: 0 a 586');
  });

  it('writes each field straight to the working document', () => {
    const onEdit = render(pad(), null);
    act(() => button('Configurações Avançadas').click());
    act(() => setValue(field('ID do marcador'), '12'));
    expect(onEdit).toHaveBeenLastCalledWith('rtl.target_aruco_id', 12);
    act(() => setValue(field('Dicionário do marcador'), 'DICT_4X4_50'));
    expect(onEdit).toHaveBeenLastCalledWith('rtl.marker_dict', 'DICT_4X4_50');
    act(() => setValue(field('Tamanho da tag'), '0.175'));
    expect(onEdit).toHaveBeenLastCalledWith('rtl.tag_size', 0.175);
    act(() => dialogButton('0.16').click());
    expect(onEdit).toHaveBeenLastCalledWith('rtl.tag_size', 0.16);
    act(() => setValue(field('Janela de centragem'), '40'));
    expect(onEdit).toHaveBeenLastCalledWith('rtl.centering_timeout_sec', 40);
  });

  it('shows an ID outside the chosen dictionary as an error without correcting it', () => {
    const onEdit = render(pad({ 'rtl.marker_dict': 'DICT_4X4_50', 'rtl.target_aruco_id': 50 }), null);
    act(() => button('Configurações Avançadas').click());
    expect(dialog()!.textContent).toContain('IDs válidos: 0 a 49');
    expect(dialog()!.querySelector('[data-field-error="rtl.target_aruco_id"]')).not.toBeNull();
    expect(onEdit).not.toHaveBeenCalled();
  });

  it('shows a dictionary the sheet does not list as not recognised', () => {
    render(pad({ 'rtl.marker_dict': 5 }), null);
    act(() => button('Configurações Avançadas').click());
    expect(dialog()!.textContent).toContain('não reconhecido pela ficha');
  });

  it('restores the advanced fields from the mission defaults only from inside the dialog', () => {
    const defaults = pad({
      'rtl.target_aruco_id': 3,
      'rtl.marker_dict': 'DICT_5X5_100',
      'rtl.tag_size': 0.15,
      'rtl.centering_timeout_sec': 30,
    });
    const onEdit = render(pad({ 'rtl.target_aruco_id': 30 }), defaults);
    act(() => button('Restaurar Padrões').click());
    expect(onEdit.mock.calls.map((call) => call[0])).not.toContain('rtl.target_aruco_id');
    onEdit.mockClear();
    act(() => button('Configurações Avançadas').click());
    act(() => dialogButton('Restaurar padrões avançados').click());
    expect(onEdit.mock.calls).toEqual([
      ['rtl.target_aruco_id', 3],
      ['rtl.marker_dict', 'DICT_5X5_100'],
      ['rtl.tag_size', 0.15],
      ['rtl.centering_timeout_sec', 30],
    ]);
  });

  it('closes on Fechar and on Escape', () => {
    render(pad(), null);
    act(() => button('Configurações Avançadas').click());
    act(() => dialogButton('Fechar').click());
    expect(dialog()).toBeNull();
    act(() => button('Configurações Avançadas').click());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(dialog()).toBeNull();
  });
});

describe('ParameterSheet conflicts on save', () => {
  const sheet = (working: Record<string, unknown>, onSave = vi.fn(), saveAttempted = false) => {
    act(() =>
      root.render(
        <ParameterSheet
          working={working}
          defaults={null}
          changedPaths={new Set(['kinematics.target_altitude_m'])}
          dirty
          saving={false}
          presets={[]}
          activePresetId={null}
          saveAttempted={saveAttempted}
          onEdit={() => undefined}
          onSave={onSave}
          onDiscard={() => undefined}
          onSavePreset={() => null}
          onRenamePreset={() => null}
          onDeletePreset={() => undefined}
          onApplyPreset={() => undefined}
        />
      )
    );
    return onSave;
  };
  const conflictDialog = () => document.querySelector('[aria-labelledby="parameter-conflict-title"]');
  const dialogButton = (label: string) =>
    Array.from(conflictDialog()?.querySelectorAll('button') ?? []).find((b) => b.textContent?.includes(label));
  const save = () =>
    act(() => Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Salvar')!.click());
  const altitudeCard = () =>
    container.querySelector('[data-parameter="kinematics.target_altitude_m"]') as HTMLElement;
  const low = () => validDocument({ 'platform.height_m': 1, 'kinematics.target_altitude_m': 1.25 });

  it('refuses an inexecutable combination: explains it and does not save', () => {
    const onSave = sheet(low());
    save();
    expect(onSave).not.toHaveBeenCalled();
    expect(conflictDialog()).not.toBeNull();
    expect(conflictDialog()!.getAttribute('data-mode')).toBe('blocked');
    expect(conflictDialog()!.textContent).toContain('Cota fora do envelope da superfície de decolagem');
    expect(conflictDialog()!.textContent).toContain('Altitude de Voo');
    expect(conflictDialog()!.textContent).toContain('1.25m');
    expect(dialogButton('Salvar mesmo assim')).toBeUndefined();
    act(() => dialogButton('Voltar e ajustar')!.click());
    expect(conflictDialog()).toBeNull();
  });

  it('asks before saving a combination that only warns', () => {
    const onSave = sheet(validDocument({ 'kinematics.countdown_sec': 4 }));
    save();
    expect(conflictDialog()!.getAttribute('data-mode')).toBe('confirm');
    expect(onSave).not.toHaveBeenCalled();
    act(() => dialogButton('Salvar mesmo assim')!.click());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(conflictDialog()).toBeNull();
  });

  it('saves straight away without conflicts', () => {
    const onSave = sheet(validDocument());
    save();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(conflictDialog()).toBeNull();
  });

  it('marks the fields involved only after an attempt, and clears them once corrected', () => {
    sheet(low());
    expect(altitudeCard().getAttribute('data-conflict')).toBeNull();
    save();
    act(() => dialogButton('Voltar e ajustar')!.click());
    expect(altitudeCard().getAttribute('data-conflict')).toBe('error');
    const envelopePaths = new Set(PARAMETER_GROUPS[0].items.map((item) => item.path));
    const expected = activeConflicts(low()).filter((c) => c.paths.some((path) => envelopePaths.has(path))).length;
    expect(expected).toBeGreaterThan(0);
    expect(container.querySelector('[data-group-conflicts="envelope"]')?.textContent).toBe(String(expected));

    sheet(validDocument({ 'kinematics.target_altitude_m': 0.1, 'kinematics.altitude_ceiling_margin_m': 0.9 }));
    expect(altitudeCard().getAttribute('data-conflict')).toBe('warning');
    sheet(validDocument());
    expect(altitudeCard().getAttribute('data-conflict')).toBeNull();
  });

  it('marks the fields after a refused save elsewhere, such as the launch', () => {
    sheet(low(), vi.fn(), true);
    expect(altitudeCard().getAttribute('data-conflict')).toBe('error');
  });
});

describe('ParameterSheet by mission mode', () => {
  const withMode = (mode: string) => ({ ...completeDocument(), mission_mode: mode }) as Record<string, unknown>;
  const text = () => container.textContent ?? '';

  it('shows every group and field in the advanced mode', () => {
    render(withMode('advanced'), completeDocument());
    expect(text()).toContain('Envelope de Voo');
    expect(text()).toContain('Gimbal & Câmera');
    expect(text()).toContain('Limites de Tempo');
    expect(text()).toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Giro');
    expect(text()).toContain('Modo: Avançado');
  });

  it('hides the return timeout and the turn rate in the advanced mode without the return', () => {
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Giro');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).toContain('Contagem Regressiva');
    expect(text()).toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Gimbal & Câmera');
    expect(text()).toContain('Modo: Avançado');
  });

  it('swaps the cruise velocity for the turn rate in the simple mode', () => {
    render(withMode('simple'), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Velocidade de Giro');
    expect(text()).toContain('Inclinação Inicial de Varredura');
    expect(text()).toContain('Ângulo da Câmera Apontada para Baixo');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).toContain('Contagem Regressiva');
    expect(text()).toContain('Modo: Simples');
    expect(text()).toContain('Margem do Teto de Altitude');
  });

  it('keeps the ceiling margin in the advanced mode, with or without the return', () => {
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(text()).toContain('Margem do Teto de Altitude');
    render(withMode('advanced'), completeDocument());
    expect(text()).toContain('Margem do Teto de Altitude');
  });

  it('offers the advanced settings only with the return, in both modes', () => {
    const hasButton = () =>
      Array.from(container.querySelectorAll('button')).some((b) => b.textContent?.includes('Configurações Avançadas'));
    render(withMode('advanced'), completeDocument());
    expect(hasButton()).toBe(true);
    render(withMode('simple'), completeDocument());
    expect(hasButton()).toBe(true);
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(hasButton()).toBe(false);
    render(setPath(withMode('simple'), 'rtl.enabled', false), completeDocument());
    expect(hasButton()).toBe(false);
  });

  it('closes an open advanced dialog when the return is turned off', () => {
    render(withMode('advanced'), completeDocument());
    act(() => button('Configurações Avançadas').click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(container.textContent).toContain('Janela de centragem');
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).not.toContain('Janela de centragem');
    render(withMode('advanced'), completeDocument());
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it('still restores every field, hidden ones included', () => {
    const defaults = completeDocument();
    const onEdit = render(withMode('simple'), defaults);
    act(() => button('Restaurar Padrões').click());
    for (const spec of ALL_PARAMETERS) expect(onEdit).toHaveBeenCalledWith(spec.path, getPath(defaults, spec.path));
  });
});

describe('ParameterSheet launch surface and return plan', () => {
  const checkbox = (path: string) =>
    container.querySelector(`[data-parameter="${path}"] input[type="checkbox"]`) as HTMLInputElement | null;
  const card = (path: string) => container.querySelector(`[data-parameter="${path}"]`) as HTMLElement | null;
  const pad = (overrides: Record<string, unknown> = {}) =>
    completeDocument({ 'platform.height_m': 1, 'kinematics.target_altitude_m': 1.8, 'platform.venue_ceiling_agl_m': 2.5, ...overrides });

  it('offers both booleans as labelled checkboxes that report a JSON boolean', () => {
    const onEdit = render(pad(), completeDocument());
    for (const path of ['platform.takeoff_from_ground', 'rtl.enabled']) {
      const box = checkbox(path)!;
      expect(box).not.toBeNull();
      expect(box.labels?.[0]?.textContent).toMatch(path === 'rtl.enabled' ? /Retorno a base \(RTL\)/ : /Decolagem do Solo/);
      expect(box.getAttribute('aria-describedby')).toBeTruthy();
    }
    expect(checkbox('rtl.enabled')!.checked).toBe(true);
    act(() => checkbox('platform.takeoff_from_ground')!.click());
    expect(onEdit).toHaveBeenCalledWith('platform.takeoff_from_ground', true);
    act(() => checkbox('rtl.enabled')!.click());
    expect(onEdit).toHaveBeenCalledWith('rtl.enabled', false);
  });

  it('places the surface right after the altitude', () => {
    render(pad(), completeDocument());
    const envelope = Array.from(container.querySelectorAll('[data-parameter]')).map((node) => node.getAttribute('data-parameter'));
    const altitude = envelope.indexOf('kinematics.target_altitude_m');
    expect(envelope[altitude + 1]).toBe('platform.takeoff_from_ground');
  });

  it('locks the margin on the pad top with the fixed ceiling as its legend, in both modes', () => {
    for (const mode of ['simple', 'advanced']) {
      render(pad({ mission_mode: mode }), completeDocument());
      const margin = card('kinematics.altitude_ceiling_margin_m')!;
      expect(margin.getAttribute('data-locked')).toBe('true');
      expect(margin.textContent).toContain('Teto fixo do perfil de totem: 2,50 m sobre o piso');
      expect((margin.querySelector('input[type="range"]') as HTMLInputElement).disabled).toBe(true);
    }
  });

  it('unlocks the margin from the floor', () => {
    render(pad({ 'platform.takeoff_from_ground': true }), completeDocument());
    const margin = card('kinematics.altitude_ceiling_margin_m')!;
    expect(margin.getAttribute('data-locked')).toBeNull();
    expect((margin.querySelector('input[type="range"]') as HTMLInputElement).disabled).toBe(false);
  });

  it('shows the plan chips of the document', () => {
    render(pad(), completeDocument());
    expect(container.querySelector('[data-plan-chip="end"]')?.textContent).toBe('Final: RTL + pouso de precisão');
    expect(container.querySelector('[data-plan-chip="surface"]')?.textContent).toBe('Decolagem: totem 1,00 m');
    render(pad({ 'rtl.enabled': false, 'platform.takeoff_from_ground': true }), completeDocument());
    expect(container.querySelector('[data-plan-chip="end"]')?.textContent).toBe('Final: pouso no local');
    expect(container.querySelector('[data-plan-chip="surface"]')?.textContent).toBe('Decolagem: solo');
  });

  it('shows the notice of an altitude the envelope moved', () => {
    act(() =>
      root.render(
        <ParameterSheet
          working={pad()}
          defaults={null}
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
          altitudeNotice="Cota ajustada para 1,30 m: mínimo sobre o totem."
        />
      )
    );
    expect(container.querySelector('[data-altitude-notice]')?.textContent).toBe('Cota ajustada para 1,30 m: mínimo sobre o totem.');
  });

  it('restores the booleans with the other sheet fields', () => {
    const onEdit = render(pad({ 'rtl.enabled': false }), completeDocument());
    act(() => button('Restaurar Padrões').click());
    expect(onEdit).toHaveBeenCalledWith('rtl.enabled', true);
    expect(onEdit).toHaveBeenCalledWith('platform.takeoff_from_ground', false);
  });
});
