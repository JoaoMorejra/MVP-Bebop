// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MissionModeSelector } from './MissionModeSelector';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const radios = () => Array.from(host.querySelectorAll<HTMLElement>('[role="radio"]'));

const press = (target: HTMLElement, key: string) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });

describe('MissionModeSelector', () => {
  it('offers the two modes in order inside a radiogroup', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    expect(host.querySelector('[role="radiogroup"]')).not.toBeNull();
    expect(radios().map((r) => r.textContent)).toEqual(['Simples', 'Avançado']);
  });

  it('marks exactly the active mode', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    expect(radios().map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true']);
  });

  it('reports the mode the operator picks', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    act(() => radios()[0].click());
    expect(onChange).toHaveBeenCalledWith('simple');
  });

  it('does not re-report the active mode', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    act(() => radios()[1].click());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('is inert when disabled', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} disabled />));
    act(() => radios()[0].click());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('describes the active mode under the control', () => {
    act(() => root.render(<MissionModeSelector value="simple" onChange={() => undefined} />));
    expect(host.textContent).toContain('Giro no lugar, foto e retorno do rumo');
  });
});

describe('MissionModeSelector keyboard', () => {
  it('keeps only the active mode in the tab order', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, 0]);
  });

  it('moves the selection with the arrow keys, wrapping at the ends', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    press(radios()[1], 'ArrowRight');
    expect(onChange).toHaveBeenLastCalledWith('simple');
    press(radios()[1], 'ArrowLeft');
    expect(onChange).toHaveBeenLastCalledWith('simple');
  });

  it('moves the focus to the mode it selected', () => {
    function Controlled() {
      const [mode, setMode] = React.useState<'simple' | 'advanced'>('simple');
      return <MissionModeSelector value={mode} onChange={setMode} />;
    }
    act(() => root.render(<Controlled />));
    radios()[0].focus();
    press(radios()[0], 'ArrowRight');
    expect(document.activeElement).toBe(radios()[1]);
    expect(radios()[1].getAttribute('aria-checked')).toBe('true');
  });

  it('ignores the arrow keys and announces the lock when disabled', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} disabled />));
    press(radios()[1], 'ArrowRight');
    expect(onChange).not.toHaveBeenCalled();
    expect(radios().every((r) => r.getAttribute('aria-disabled') === 'true')).toBe(true);
    expect(host.querySelector('[role="radiogroup"]')!.getAttribute('aria-disabled')).toBe('true');
  });
});
