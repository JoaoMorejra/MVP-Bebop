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

  it('puts the red circle first, at the top-left, before the title', () => {
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => undefined} />));

    const header = container.querySelector('header')!;
    const [first, second] = Array.from(header.children);
    expect(first.tagName).toBe('BUTTON');
    expect(first.getAttribute('aria-label')).toBe('Fechar diagnóstico');
    expect(second.textContent).toBe('Diagnóstico · Terminal');
    expect(header.className).toContain('justify-start');
    const circle = first.querySelector('[data-close-circle]') as HTMLElement;
    expect(circle.className).toContain('rounded-full');
    expect(circle.style.backgroundColor).toBe('rgb(255, 95, 87)');
  });

  it('carries no X icon of its own in the top-right corner', () => {
    act(() => root.render(<DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => undefined} />));
    const header = container.querySelector('header')!;
    expect(header.lastElementChild?.tagName).not.toBe('BUTTON');
  });
});
