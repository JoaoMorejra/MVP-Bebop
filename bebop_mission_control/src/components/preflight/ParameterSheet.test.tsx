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
        favoriteActive={false}
        hasFavorite={false}
        onEdit={onEdit}
        onSave={() => undefined}
        onSaveFavorite={() => undefined}
        onRestoreFavorite={() => undefined}
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

  it('no longer lists the countdown: the window is fixed at 10 s', () => {
    render(completeDocument(), completeDocument());
    expect(container.textContent).not.toContain('Contagem Regressiva');
    expect(container.querySelector('[data-parameter="kinematics.countdown_sec"]')).toBeNull();
  });
});

describe('ParameterSheet slider ranges', () => {
  it.each([
    ['kinematics.target_altitude_m', '0.1', '3'],
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
          favoriteActive={false}
          hasFavorite={false}
          onEdit={() => undefined}
          onSave={() => undefined}
            onSaveFavorite={() => undefined}
          onRestoreFavorite={() => undefined}
          refusedPath="kinematics.target_altitude_m"
        />
      )
    );
    const field = container.querySelector('[data-parameter="kinematics.target_altitude_m"]');
    expect(field?.getAttribute('data-conflict')).toBe('error');
    expect(container.querySelector('[data-parameter="kinematics.takeoff_stabilize_duration_sec"]')?.getAttribute('data-conflict')).toBeNull();
  });
});

describe('ParameterSheet Pouso ArUco', () => {
  const pad = (overrides: Record<string, unknown> = {}) =>
    completeDocument({
      'rtl.target_aruco_id': 8,
      'rtl.marker_dict': 'DICT_APRILTAG_36h11',
      'rtl.tag_size': 0.2,
      'rtl.centering_timeout_sec': 25,
      ...overrides,
    });
  const section = () => container.querySelector('[data-section="aruco"]');
  const range = (path: string) => container.querySelector(`[data-parameter="${path}"] input[type="range"]`) as HTMLInputElement;
  const slide = (input: HTMLInputElement, value: string) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const figure = (path: string) => container.querySelector(`[data-parameter="${path}"] .exact-field input`) as HTMLInputElement;
  const type = (path: string, value: string) => {
    const input = figure(path);
    act(() => input.focus());
    act(() => slide(input, value));
    act(() => input.blur());
  };

  it('lays the pad marker fields on the sheet, with no separate Aruco button or dialog', () => {
    render(pad(), null);
    expect(section()).not.toBeNull();
    expect(section()!.querySelector('h3')?.textContent).toBe('Pouso ArUco');
    const paths = Array.from(section()!.querySelectorAll('[data-parameter]')).map((n) => n.getAttribute('data-parameter'));
    expect(paths).toEqual(['rtl.target_aruco_id', 'rtl.tag_size', 'rtl.centering_timeout_sec']);
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent?.trim() === 'Aruco')).toBe(false);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('does not offer the dictionary, and bounds the ID by the one the document holds', () => {
    render(pad(), null);
    expect(container.textContent).not.toContain('Dicionário');
    expect(container.querySelector('[data-parameter="rtl.marker_dict"]')).toBeNull();
    const onEdit = render(pad(), null);
    type('rtl.target_aruco_id', '999');
    expect(onEdit).toHaveBeenLastCalledWith('rtl.target_aruco_id', 586);
    const small = render(pad({ 'rtl.marker_dict': 'DICT_4X4_50' }), null);
    type('rtl.target_aruco_id', '999');
    expect(small).toHaveBeenLastCalledWith('rtl.target_aruco_id', 49);
  });

  it('offers the ID and the tag size as typed or scrubbed figures, without a slider', () => {
    render(pad(), null);
    for (const path of ['rtl.target_aruco_id', 'rtl.tag_size']) {
      expect(range(path)).toBeNull();
      expect(container.querySelector(`[data-parameter="${path}"] .exact-field`)!.hasAttribute('data-scrub')).toBe(true);
    }
    expect(range('rtl.centering_timeout_sec')).not.toBeNull();
  });

  it('writes each field straight to the working document', () => {
    const onEdit = render(pad(), null);
    type('rtl.target_aruco_id', '12');
    expect(onEdit).toHaveBeenLastCalledWith('rtl.target_aruco_id', 12);
    type('rtl.tag_size', '0,175');
    expect(onEdit).toHaveBeenLastCalledWith('rtl.tag_size', 0.175);
    act(() => slide(range('rtl.centering_timeout_sec'), '40'));
    expect(onEdit).toHaveBeenLastCalledWith('rtl.centering_timeout_sec', 40);
  });

  it('marks an ID outside the dictionary as an error and keeps Salvar shut', () => {
    render(pad({ 'rtl.marker_dict': 'DICT_4X4_50', 'rtl.target_aruco_id': 50 }), null);
    const id = container.querySelector('[data-parameter="rtl.target_aruco_id"]')!;
    expect(id.getAttribute('data-conflict')).toBe('error');
    expect(container.querySelector('[data-save-phase]')!.hasAttribute('data-blocked')).toBe(true);
  });

  it('restores the pad marker fields with every other field', () => {
    const defaults = pad({
      'rtl.target_aruco_id': 3,
      'rtl.marker_dict': 'DICT_5X5_100',
      'rtl.tag_size': 0.15,
      'rtl.centering_timeout_sec': 30,
    });
    const onEdit = render(pad({ 'rtl.target_aruco_id': 30 }), defaults);
    act(() => button('Restaurar Padrões').click());
    expect(onEdit).toHaveBeenCalledWith('rtl.target_aruco_id', 3);
    expect(onEdit).toHaveBeenCalledWith('rtl.tag_size', 0.15);
    expect(onEdit).toHaveBeenCalledWith('rtl.centering_timeout_sec', 30);
  });
});

describe('ParameterSheet conflicts', () => {
  const sheet = (working: Record<string, unknown>, onSave = vi.fn()) => {
    act(() =>
      root.render(
        <ParameterSheet
          working={working}
          defaults={null}
          changedPaths={new Set(['kinematics.target_altitude_m'])}
          dirty
          saving={false}
          favoriteActive={false}
          hasFavorite={false}
          onEdit={() => undefined}
          onSave={onSave}
          onSaveFavorite={() => undefined}
          onRestoreFavorite={() => undefined}
        />
      )
    );
    return onSave;
  };
  const saveButton = () => container.querySelector('[data-save-phase]') as HTMLButtonElement;
  const altitudeCard = () => container.querySelector('[data-parameter="kinematics.target_altitude_m"]') as HTMLElement;
  const low = () => validDocument({ 'platform.height_m': 1, 'kinematics.target_altitude_m': 1.25 });

  it('keeps Salvar shut on an inexecutable combination, with no dialog', () => {
    const onSave = sheet(low());
    expect(saveButton().disabled).toBe(true);
    expect(saveButton().hasAttribute('data-blocked')).toBe(true);
    act(() => saveButton().click());
    expect(onSave).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it('marks the fields of an error at once, with the reason in data-conflict-reason and no hover title', () => {
    sheet(low());
    expect(altitudeCard().getAttribute('data-conflict')).toBe('error');
    expect(altitudeCard().title).toBe('');
    expect(altitudeCard().getAttribute('data-conflict-reason')).toContain('Cota fora do envelope da superfície de decolagem');
    const envelopePaths = new Set(PARAMETER_GROUPS[0].items.map((item) => item.path));
    const expected = PARAMETER_GROUPS[0].items.filter((item) =>
      activeConflicts(low()).some((c) => c.severity === 'error' && c.paths.includes(item.path) && envelopePaths.has(item.path))
    ).length;
    expect(expected).toBeGreaterThan(0);
    expect(container.querySelector('[data-group-conflicts="envelope"]')?.textContent).toBe(String(expected));
  });

  it('saves a combination that only warns straight away, with no warning shown', () => {
    const onSave = sheet(validDocument({ 'kinematics.countdown_sec': 4 }));
    act(() => saveButton().click());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    sheet(validDocument({ 'kinematics.target_altitude_m': 0.1, 'kinematics.altitude_ceiling_margin_m': 0.9 }));
    expect(altitudeCard().getAttribute('data-conflict')).toBeNull();
  });

  it('saves straight away without conflicts', () => {
    const onSave = sheet(validDocument());
    act(() => Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Salvar')!.click());
    expect(onSave).toHaveBeenCalledTimes(1);
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
    expect(container.querySelector('h2')?.textContent).toBe('Avançado');
  });

  it('hides the return timeout and the turn rate in the advanced mode without the return', () => {
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Giro');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).not.toContain('Contagem Regressiva');
    expect(text()).toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Gimbal & Câmera');
    expect(container.querySelector('h2')?.textContent).toBe('Avançado');
  });

  it('swaps the cruise velocity for the turn rate in the simple mode', () => {
    render(withMode('simple'), completeDocument());
    expect(text()).not.toContain('Timeout de Retorno e Pouso ArUco');
    expect(text()).not.toContain('Velocidade de Deslocamento');
    expect(text()).toContain('Velocidade de Giro');
    expect(text()).toContain('Inclinação Inicial de Varredura');
    expect(text()).toContain('Ângulo da Câmera Apontada para Baixo');
    expect(text()).toContain('Timeout de Procura/Varredura');
    expect(text()).not.toContain('Contagem Regressiva');
    expect(container.querySelector('h2')?.textContent).toBe('Simples');
    expect(text()).toContain('Margem do Teto de Altitude');
  });

  it('keeps the ceiling margin in the advanced mode, with or without the return', () => {
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(text()).toContain('Margem do Teto de Altitude');
    render(withMode('advanced'), completeDocument());
    expect(text()).toContain('Margem do Teto de Altitude');
  });

  it('offers the pad marker section only with the return, in both modes', () => {
    const hasSection = () => container.querySelector('[data-section="aruco"]') !== null;
    render(withMode('advanced'), completeDocument());
    expect(hasSection()).toBe(true);
    render(withMode('simple'), completeDocument());
    expect(hasSection()).toBe(true);
    render(setPath(withMode('advanced'), 'rtl.enabled', false), completeDocument());
    expect(hasSection()).toBe(false);
    render(setPath(withMode('simple'), 'rtl.enabled', false), completeDocument());
    expect(hasSection()).toBe(false);
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

  it('offers the takeoff point as Solo or Totem and the return as a labelled switch', () => {
    const onEdit = render(pad(), completeDocument());
    const point = card('platform.takeoff_from_ground')!;
    expect(point.textContent).toContain('Ponto de Decolagem');
    expect(point.textContent).not.toContain('Decolagem do Solo');
    const radios = Array.from(point.querySelectorAll<HTMLInputElement>('input[type="radio"]'));
    expect(radios.map((r) => r.labels?.[0]?.textContent)).toEqual(['Solo', 'Totem']);
    expect(radios[1].checked).toBe(true);
    act(() => radios[0].click());
    expect(onEdit).toHaveBeenCalledWith('platform.takeoff_from_ground', true);
    const rtl = checkbox('rtl.enabled')!;
    expect(rtl.labels?.[0]?.textContent).toMatch(/Retorno a base \(RTL\)/);
    expect(rtl.checked).toBe(true);
    act(() => rtl.click());
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

  it('unlocks the margin on the pad top above 2.0 m over the floor, in both modes', () => {
    for (const mode of ['simple', 'advanced']) {
      render(pad({ mission_mode: mode, 'kinematics.target_altitude_m': 2.5, 'platform.venue_ceiling_agl_m': 3.2 }), completeDocument());
      const margin = card('kinematics.altitude_ceiling_margin_m')!;
      expect(margin.getAttribute('data-locked')).toBeNull();
      expect(margin.textContent).not.toContain('Teto fixo do perfil de totem');
      expect((margin.querySelector('input[type="range"]') as HTMLInputElement).disabled).toBe(false);
    }
  });

  it('unlocks the margin from the floor', () => {
    render(pad({ 'platform.takeoff_from_ground': true }), completeDocument());
    const margin = card('kinematics.altitude_ceiling_margin_m')!;
    expect(margin.getAttribute('data-locked')).toBeNull();
    expect((margin.querySelector('input[type="range"]') as HTMLInputElement).disabled).toBe(false);
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
          favoriteActive={false}
          hasFavorite={false}
          onEdit={() => undefined}
          onSave={() => undefined}
            onSaveFavorite={() => undefined}
          onRestoreFavorite={() => undefined}
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

describe('ParameterSheet minimal layout', () => {
  it('header shows only the mode name', () => {
    render({ ...completeDocument(), mission_mode: 'advanced' }, completeDocument());
    expect(container.querySelector('h2')?.textContent).toBe('Avançado');
    expect(container.textContent).not.toContain('Os ajustes que definem');
    expect(container.textContent).not.toContain('Modo:');
    expect(container.textContent).not.toContain('Parâmetros de voo');
    expect(container.querySelector('[data-plan-chips]')).toBeNull();
  });

  it('shows no help text', () => {
    render(completeDocument(), completeDocument());
    for (const spec of ALL_PARAMETERS) {
      if (spec.hint) expect(container.textContent).not.toContain(spec.hint);
    }
    for (const group of PARAMETER_GROUPS) {
      if (group.summary) expect(container.textContent).not.toContain(group.summary);
    }
    expect(container.textContent).not.toContain('A câmera desce do ângulo de varredura');
    expect(container.querySelector('[data-gimbal-arc]')).toBeNull();
  });

  it('each number parameter is label, slider and numeric input', () => {
    render(completeDocument(), completeDocument());
    const card = container.querySelector('[data-parameter="kinematics.target_altitude_m"]')!;
    const spec = ALL_PARAMETERS.find((item) => item.path === 'kinematics.target_altitude_m')!;
    expect(card.textContent).toContain(spec.label);
    expect(card.querySelector('input[type="range"]')).not.toBeNull();
    expect(card.querySelector('input:not([type="range"])')).not.toBeNull();
  });

  it('each boolean parameter is label and checkbox', () => {
    render(completeDocument(), completeDocument());
    const card = container.querySelector('[data-parameter="rtl.enabled"]')!;
    expect(card.querySelector('input[type="checkbox"]')).not.toBeNull();
    expect(card.querySelector('[id$="-hint"]')).toBeNull();
    expect(card.querySelector('input[type="checkbox"]')!.getAttribute('aria-describedby')).toBeNull();
  });

  it('names a missing value in one short line', () => {
    render(completeDocument({ 'kinematics.target_altitude_m': null }), completeDocument());
    const missing = container.querySelector('[data-missing-value="kinematics.target_altitude_m"]')!;
    expect(missing.textContent).toContain('Valor ausente');
    expect(missing.textContent).not.toContain('Restaure os padrões');
  });
});

describe('ParameterSheet one-screen layout', () => {
  it('draws the return as a switch with its state in one word, and the takeoff point as a choice', () => {
    render(completeDocument({ 'rtl.enabled': true, 'platform.takeoff_from_ground': false }), completeDocument());
    const rtl = container.querySelector('[data-parameter="rtl.enabled"]')!;
    const surface = container.querySelector('[data-parameter="platform.takeoff_from_ground"]')!;
    expect(rtl.querySelector('input[type="checkbox"]')!.getAttribute('role')).toBe('switch');
    expect(rtl.querySelector('.param-state')?.textContent).toBe('Ativo');
    expect(surface.querySelector('[role="radiogroup"]')!.getAttribute('data-choice')).toBe('second');
  });

  it('lays the envelope and the camera on top, the time limits and the pad marker below, with no camera drawing', () => {
    render(completeDocument({ 'rtl.enabled': true }), completeDocument());
    const sections = Array.from(container.querySelectorAll('[data-section]')).map((s) => s.getAttribute('data-section'));
    expect(sections).toEqual(['envelope', 'gimbal', 'timeouts', 'aruco']);
    expect(container.querySelector('[data-gimbal-arc]')).toBeNull();
  });

  it('says Tudo salvo inside the button when nothing is pending, and Salvar once something is', () => {
    render(completeDocument(), completeDocument());
    expect(container.querySelector('[data-save-phase]')!.textContent?.trim()).toBe('Tudo salvo');
    expect(container.textContent).not.toContain('Descartar');
    expect(container.textContent).not.toContain('sem salvar');
  });

  it('shows the figure large and editable in place', () => {
    render(completeDocument(), completeDocument());
    const altitude = container.querySelector('[data-parameter="kinematics.target_altitude_m"]')!;
    expect(altitude.querySelector('.exact-field-display input')).not.toBeNull();
  });
});

describe('ParameterSheet save states', () => {
  const renderSave = (dirty: boolean, saving: boolean) =>
    act(() =>
      root.render(
        <ParameterSheet
          working={completeDocument()}
          defaults={completeDocument()}
          changedPaths={new Set(dirty ? ['kinematics.target_altitude_m'] : [])}
          dirty={dirty}
          saving={saving}
          favoriteActive={false}
          hasFavorite={false}
          onEdit={() => undefined}
          onSave={() => undefined}
            onSaveFavorite={() => undefined}
          onRestoreFavorite={() => undefined}
        />
      )
    );
  const phase = () => container.querySelector('[data-save-phase]')?.getAttribute('data-save-phase');

  it('goes from Salvar to the bouncing dots to Salvo, then settles back', () => {
    vi.useFakeTimers();
    renderSave(true, false);
    expect(phase()).toBe('idle');
    renderSave(true, true);
    expect(phase()).toBe('loading');
    expect(container.querySelector('.save-dots')).not.toBeNull();
    renderSave(false, false);
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(phase()).toBe('done');
    expect(container.querySelector('[data-save-phase]')?.textContent).toContain('Salvo');
    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(phase()).toBe('idle');
    vi.useRealTimers();
  });

  it('shakes Salvar on each nudge from the screen', () => {
    const at = (nudge: number) =>
      act(() =>
        root.render(
          <ParameterSheet
            working={completeDocument()}
            defaults={completeDocument()}
            changedPaths={new Set(['kinematics.target_altitude_m'])}
            dirty
            saving={false}
            favoriteActive={false}
            hasFavorite={false}
            onEdit={() => undefined}
            onSave={() => undefined}
            onSaveFavorite={() => undefined}
            onRestoreFavorite={() => undefined}
            nudgeSave={nudge}
          />
        )
      );
    at(0);
    expect(container.querySelector('[data-save-phase]')!.classList.contains('is-shaking')).toBe(false);
    at(1);
    expect(container.querySelector('[data-save-phase]')!.classList.contains('is-shaking')).toBe(true);
  });

  it('returns to Salvar without the tick when the save left edits pending', () => {
    vi.useFakeTimers();
    renderSave(true, true);
    renderSave(true, false);
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(phase()).toBe('idle');
    vi.useRealTimers();
  });
});

describe('ParameterSheet close button', () => {
  it('sits in the header, on the right, and asks the screen to close', () => {
    const onClose = vi.fn();
    act(() =>
      root.render(
        <ParameterSheet
          working={completeDocument()}
          defaults={completeDocument()}
          changedPaths={new Set()}
          dirty={false}
          saving={false}
          favoriteActive={false}
          hasFavorite={false}
          onEdit={() => undefined}
          onSave={() => undefined}
          onSaveFavorite={() => undefined}
          onRestoreFavorite={() => undefined}
          onClose={onClose}
        />
      )
    );
    const header = container.querySelector('[data-sheet-header]')!;
    const close = header.querySelector<HTMLButtonElement>('button[aria-label="Fechar"]')!;
    expect(close).not.toBeNull();
    expect(header.lastElementChild).toBe(close);
    act(() => close.click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
