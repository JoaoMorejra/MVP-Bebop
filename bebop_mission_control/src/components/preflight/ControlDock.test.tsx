// @vitest-environment jsdom
import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ControlDock, FUSE_MS, SPLIT_MS } from './ControlDock';

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

function render(fused: boolean, onAbort = vi.fn()) {
  act(() =>
    root.render(
      <ControlDock
        fused={fused}
        onAbort={onAbort}
        parameters={<button type="button">Parâmetros</button>}
        mode={<button type="button">Avançado</button>}
      />
    )
  );
  return onAbort;
}

const dock = () => host.querySelector<HTMLElement>('[data-fused]')!;
const phase = () => dock().getAttribute('data-phase');
const abortButton = () => host.querySelector<HTMLButtonElement>('[aria-label="Abortar missão e pousar imediatamente"]');
const slots = () => Array.from(host.querySelectorAll<HTMLElement>('[data-dock-slot]'));
const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms));

describe('ControlDock', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the parameters and mode slots when split', () => {
    render(false);
    expect(dock().getAttribute('data-fused')).toBe('false');
    expect(phase()).toBe('idle');
    expect(slots().map((slot) => slot.textContent)).toEqual(['Parâmetros', 'Avançado']);
    expect(slots().every((slot) => slot.getAttribute('aria-hidden') === null)).toBe(true);
    expect(abortButton()!.tabIndex).toBe(-1);
    expect(abortButton()!.closest('[aria-hidden="true"]')).not.toBeNull();
  });

  it('fuses in steps into the cockpit abort, then holds it', () => {
    render(false);
    render(true);
    expect(phase()).toBe('fusing');
    expect(abortButton()!.closest('[aria-hidden="true"]')).toBeNull();
    for (const slot of slots()) {
      expect(slot.getAttribute('aria-hidden')).toBe('true');
      expect(slot.hasAttribute('inert')).toBe(true);
      for (const control of Array.from(slot.querySelectorAll<HTMLElement>('button'))) expect(control.tabIndex).toBe(-1);
    }
    wait(FUSE_MS - 1);
    expect(phase()).toBe('fusing');
    wait(1);
    expect(phase()).toBe('fused');
    // The same control as the cockpit: its label, its icon, its ember edge.
    expect(abortButton()!.textContent).toBe('ABORTAR MISSÃO');
    expect(abortButton()!.className).toContain('border-ember');
    expect(abortButton()!.querySelector('svg')).not.toBeNull();
  });

  it('plays the fusion backwards when the launch is cancelled, from the button or the keys alike', () => {
    render(true);
    expect(phase()).toBe('fused');
    render(false);
    expect(phase()).toBe('splitting');
    // Not pressable while it goes, and the slots come back only once apart.
    expect(abortButton()!.closest('[inert]')).not.toBeNull();
    expect(slots().every((slot) => slot.hasAttribute('inert'))).toBe(true);
    wait(SPLIT_MS);
    expect(phase()).toBe('idle');
    expect(slots().every((slot) => !slot.hasAttribute('inert'))).toBe(true);
  });

  it('reverses from wherever the fusion was', () => {
    render(false);
    render(true);
    wait(FUSE_MS / 2);
    render(false);
    expect(phase()).toBe('splitting');
    wait(SPLIT_MS);
    expect(phase()).toBe('idle');
  });

  it('keeps the footprint of the pair', () => {
    render(false);
    const width = dock().style.width;
    expect(width).toBe('calc(var(--pair-w) * 2 + 12px)');
    render(true);
    expect(dock().style.width).toBe(width);
  });

  it('pressing Abortar Missão calls onAbort', () => {
    const onAbort = render(true);
    act(() => {
      abortButton()!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('has a transition and respects reduced motion', () => {
    render(false);
    expect(host.querySelector('.dock-motion')).not.toBeNull();
    const css = readFileSync(resolve(__dirname, '../../styles/globals.css'), 'utf-8');
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(reduced).toContain('.dock-motion');
    expect(reduced).toContain('.dock-fuse *');
  });
});
