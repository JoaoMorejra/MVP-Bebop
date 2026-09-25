// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DiagnosticsOverlayHeader } from './DiagnosticsOverlayHeader';

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

describe('DiagnosticsOverlayHeader', () => {
  it('has one close control, and it closes the overlay', () => {
    const onClose = vi.fn();
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={onClose} />));

    const buttons = container.querySelectorAll('header button');
    expect(buttons).toHaveLength(1);
    act(() => (buttons[0] as HTMLButtonElement).click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('draws red, yellow and green at the top-left, before the title', () => {
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => undefined} />));

    const header = container.querySelector('header')!;
    const [lights, title] = Array.from(header.children);
    expect(lights.hasAttribute('data-traffic-lights')).toBe(true);
    expect(title.textContent).toBe('Diagnóstico · Terminal');
    expect(header.className).toContain('justify-start');

    const [red, yellow, green] = Array.from(lights.children);
    expect(red.tagName).toBe('BUTTON');
    expect(red.getAttribute('aria-label')).toBe('Fechar diagnóstico');
    const colour = (el: Element, selector: string) => (el.querySelector(selector) as HTMLElement).style.backgroundColor;
    expect(colour(red, '[data-close-circle]')).toBe('rgb(255, 95, 87)');
    expect(colour(yellow, '[data-minimize-circle]')).toBe('rgb(254, 188, 46)');
    expect(colour(green, '[data-zoom-circle]')).toBe('rgb(40, 200, 64)');
  });

  it('keeps yellow and green out of the accessibility tree and the tab order', () => {
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => undefined} />));
    const [, yellow, green] = Array.from(container.querySelector('[data-traffic-lights]')!.children);
    for (const light of [yellow, green]) {
      expect(light.getAttribute('aria-hidden')).toBe('true');
      expect(light.tagName).not.toBe('BUTTON');
    }
  });

  it('carries no X icon of its own in the top-right corner', () => {
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => undefined} />));
    const header = container.querySelector('header')!;
    expect(header.lastElementChild?.tagName).not.toBe('BUTTON');
  });
});
