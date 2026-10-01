// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument } from '../../lib/__fixtures__/missionDocument';
import { ALL_PARAMETERS } from '../../lib/parameterSchema';
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
        hasPreset={false}
        presetActive={false}
        onEdit={onEdit}
        onSave={() => undefined}
        onDiscard={() => undefined}
        onSavePreset={() => undefined}
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
    expect(container.textContent).toContain('padrão 1.0m');
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
