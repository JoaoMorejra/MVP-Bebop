// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ALL_PARAMETERS } from '../../lib/parameterSchema';
import { setPath } from '../../lib/paths';
import { matchesPreset, type ParamsDoc } from '../../hooks/useMissionParameters';
import { ParameterSheet } from './ParameterSheet';
import { DiscardChangesDialog } from './DiscardChangesDialog';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

function tuned(): ParamsDoc {
  let doc: ParamsDoc = { no_fly: false, calibration: { samples: 12 } };
  for (const spec of ALL_PARAMETERS) doc = setPath(doc, spec.path, spec.defaultValue);
  return doc;
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

describe('matchesPreset', () => {
  it('holds when every flight parameter equals the preset', () => {
    expect(matchesPreset(tuned(), tuned())).toBe(true);
  });

  it('breaks as soon as one flight parameter moves', () => {
    const edited = setPath(tuned(), ALL_PARAMETERS[0].path, ALL_PARAMETERS[0].defaultValue + 1);
    expect(matchesPreset(edited, tuned())).toBe(false);
  });

  it('ignores the arming mode and what the mission writes back on its own', () => {
    const afterRun = setPath(setPath(tuned(), 'no_fly', true), 'calibration.samples', 40);
    expect(matchesPreset(afterRun, tuned())).toBe(true);
  });

  it('is false without a preset or a document', () => {
    expect(matchesPreset(tuned(), null)).toBe(false);
    expect(matchesPreset(null, tuned())).toBe(false);
  });
});

describe('ParameterSheet', () => {
  const renderSheet = (presetActive: boolean) =>
    act(() =>
      root.render(
        <ParameterSheet
          working={tuned()}
          changedPaths={new Set()}
          dirty={false}
          saving={false}
          hasPreset
          presetActive={presetActive}
          onEdit={() => undefined}
          onSave={() => undefined}
          onDiscard={() => undefined}
          onSavePreset={() => undefined}
          onApplyPreset={() => undefined}
        />
      )
    );

  it('fills the star while the working values are the preset', () => {
    renderSheet(true);
    const star = container.querySelector('[data-preset-star]')!;
    expect(star.getAttribute('data-preset-star')).toBe('filled');
    expect(star.getAttribute('fill')).toBe('currentColor');
    expect(star.closest('button')!.getAttribute('aria-pressed')).toBe('true');
  });

  it('draws the star as an outline otherwise', () => {
    renderSheet(false);
    const star = container.querySelector('[data-preset-star]')!;
    expect(star.getAttribute('data-preset-star')).toBe('outline');
    expect(star.getAttribute('fill')).toBe('none');
  });

  it('no longer shows the config file name', () => {
    renderSheet(false);
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
