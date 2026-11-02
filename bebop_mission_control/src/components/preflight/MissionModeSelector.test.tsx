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

const trigger = () => host.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')!;
const listbox = () => host.querySelector<HTMLElement>('[role="listbox"]');
const options = () => Array.from(host.querySelectorAll<HTMLElement>('[role="option"]'));

const press = (target: HTMLElement, key: string) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  });

const open = () => act(() => trigger().click());

describe('MissionModeSelector', () => {
  it('shows the selected mode label on the trigger', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    expect(trigger().textContent).toBe('Avançado');
    expect(listbox()).toBeNull();
  });

  it('lists Simples, Avançado and Teste when opened', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    open();
    expect(listbox()).not.toBeNull();
    expect(options().map((o) => o.textContent)).toEqual(['Simples', 'Avançado', 'Teste']);
    expect(trigger().getAttribute('aria-expanded')).toBe('true');
  });

  it('marks the selected option with aria-selected', () => {
    act(() => root.render(<MissionModeSelector value="test" onChange={() => undefined} />));
    open();
    expect(options().map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true']);
  });

  it('reports the picked mode and closes', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    open();
    act(() => options()[2].click());
    expect(onChange).toHaveBeenCalledWith('test');
    expect(listbox()).toBeNull();
  });

  it('does not report the already selected mode', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    open();
    act(() => options()[1].click());
    expect(onChange).not.toHaveBeenCalled();
    expect(listbox()).toBeNull();
  });

  it('closes on Escape and returns focus to the trigger', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    open();
    press(listbox()!, 'Escape');
    expect(listbox()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('closes on a click outside', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    open();
    act(() => {
      document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
    });
    expect(listbox()).toBeNull();
  });

  it('is inert when disabled', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} disabled />));
    open();
    expect(listbox()).toBeNull();
    expect(trigger().getAttribute('aria-disabled')).toBe('true');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('moves selection with ArrowDown/ArrowUp and wraps', () => {
    const onChange = vi.fn();
    act(() => root.render(<MissionModeSelector value="advanced" onChange={onChange} />));
    open();
    expect(document.activeElement).toBe(options()[1]);
    press(listbox()!, 'ArrowDown');
    expect(document.activeElement).toBe(options()[2]);
    press(listbox()!, 'ArrowDown');
    expect(document.activeElement).toBe(options()[0]);
    press(listbox()!, 'ArrowUp');
    expect(document.activeElement).toBe(options()[2]);
    press(listbox()!, 'Enter');
    expect(onChange).toHaveBeenCalledWith('test');
    expect(listbox()).toBeNull();
  });

  it('runs the shimmer only on the modes on offer in the open list, never on the chosen one', () => {
    act(() => root.render(<MissionModeSelector value="simple" onChange={() => undefined} />));
    expect(trigger().querySelector('.mode-shimmer')).toBeNull();
    open();
    const shimmers = Array.from(host.querySelectorAll<HTMLElement>('.mode-shimmer')).map((s) => s.getAttribute('data-text'));
    expect(shimmers).toEqual(['Avançado', 'Teste']);
    expect(trigger().querySelector('.mode-shimmer')).toBeNull();
  });

  it('draws the track on the trigger only, never in the open list', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    open();
    expect(trigger().querySelector('.mode-track')).not.toBeNull();
    expect(options().some((o) => o.querySelector('.mode-track') !== null)).toBe(false);
  });

  it('lights the stop of the current mode on the trigger track', () => {
    act(() => root.render(<MissionModeSelector value="test" onChange={() => undefined} />));
    const stops = Array.from(trigger().querySelectorAll('.mode-track-stop')).map((s) => s.hasAttribute('data-lit'));
    expect(stops).toEqual([false, false, true]);
  });

  it('keeps the folded list inert and grows the capsule in place when opened', () => {
    act(() => root.render(<MissionModeSelector value="advanced" onChange={() => undefined} />));
    const root_ = host.querySelector<HTMLElement>('.mode-select')!;
    const body = host.querySelector<HTMLElement>('.mode-select-body-inner')!;
    expect(root_.hasAttribute('data-open')).toBe(false);
    expect(body.hasAttribute('inert')).toBe(true);
    open();
    expect(root_.hasAttribute('data-open')).toBe(true);
    expect(body.hasAttribute('inert')).toBe(false);
  });

  it('takes the cyan theme in Teste and the mint one in the flight modes', () => {
    act(() => root.render(<MissionModeSelector value="test" onChange={() => undefined} />));
    expect(host.querySelector('.mode-select')?.getAttribute('data-theme')).toBe('test');
    act(() => root.render(<MissionModeSelector value="simple" onChange={() => undefined} />));
    expect(host.querySelector('.mode-select')?.getAttribute('data-theme')).toBe('flight');
  });
});
