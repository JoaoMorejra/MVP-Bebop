// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AbortControl } from './AbortControl';
import { isTextInput } from './CockpitScreen';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

// Polyfill PointerEvent for jsdom if needed
if (typeof globalThis.PointerEvent === 'undefined') {
  // @ts-expect-error test polyfill
  globalThis.PointerEvent = class PointerEvent extends MouseEvent {};
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

const getButton = (): HTMLButtonElement => host.querySelector('button')!;

describe('AbortControl (R6 / L2)', () => {
  it('renders default ABORTAR MISSÃO button', () => {
    const onAbort = vi.fn();
    act(() => root.render(<AbortControl onAbort={onAbort} disabled={false} busy={false} />));
    const btn = getButton();
    expect(btn).toBeTruthy();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('ABORTAR MISSÃO');
  });

  it('renders disabled button in commanded phase', () => {
    const onAbort = vi.fn();
    act(() =>
      root.render(<AbortControl onAbort={onAbort} disabled={true} busy={true} landProgress="commanded" />)
    );
    const btn = getButton();
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('POUSO COMANDADO');
    expect(host.textContent).toContain('Pouso comandado');
  });

  it('renders disabled button in landing phase', () => {
    const onAbort = vi.fn();
    act(() =>
      root.render(<AbortControl onAbort={onAbort} disabled={true} busy={false} landProgress="landing" />)
    );
    const btn = getButton();
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('POUSANDO');
    expect(host.textContent).toContain('Pousando');
  });

  it('renders disabled button in landed phase', () => {
    const onAbort = vi.fn();
    act(() =>
      root.render(<AbortControl onAbort={onAbort} disabled={true} busy={false} landProgress="landed" />)
    );
    const btn = getButton();
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('POUSADA');
    expect(host.textContent).toContain('Pousada');
  });

  it('re-enables button with REENVIAR POUSO in unconfirmed phase (L2)', () => {
    const onAbort = vi.fn();
    act(() =>
      root.render(
        <AbortControl
          onAbort={onAbort}
          disabled={true} // even if disabled/busy prop was passed
          busy={true}
          landProgress="unconfirmed"
        />
      )
    );
    const btn = getButton();
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('REENVIAR POUSO');
    expect(host.textContent).toContain('Pouso não confirmado: reenviar');

    // Clicking fires onAbort
    act(() => {
      btn.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }));
    });
    expect(onAbort).toHaveBeenCalledTimes(1);
  });
});

describe('isTextInput (R11)', () => {
  it('identifies input elements', () => {
    const input = document.createElement('input');
    expect(isTextInput(input)).toBe(true);
  });

  it('identifies textarea elements', () => {
    const textarea = document.createElement('textarea');
    expect(isTextInput(textarea)).toBe(true);
  });

  it('does not take a select, a range, a checkbox or a button for a text field (D8)', () => {
    expect(isTextInput(document.createElement('select'))).toBe(false);
    for (const type of ['range', 'checkbox', 'radio', 'button']) {
      const input = document.createElement('input');
      input.type = type;
      expect(isTextInput(input)).toBe(false);
    }
  });

  it('identifies contenteditable elements', () => {
    const div = document.createElement('div');
    div.setAttribute('contenteditable', 'true');
    expect(isTextInput(div)).toBe(true);
  });

  it('returns false for regular elements', () => {
    const button = document.createElement('button');
    expect(isTextInput(button)).toBe(false);

    const div = document.createElement('div');
    expect(isTextInput(div)).toBe(false);

    expect(isTextInput(null)).toBe(false);
  });
});
