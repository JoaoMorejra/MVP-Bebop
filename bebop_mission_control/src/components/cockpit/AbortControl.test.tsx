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

  it.each(['commanded', 'landing', 'landed'] as const)(
    'shows nothing of the abort while the aircraft lands (%s): same wording, no chip, no motion',
    (phase) => {
      act(() => root.render(<AbortControl onAbort={vi.fn()} disabled busy landProgress={phase} />));
      const btn = getButton();
      expect(btn.disabled).toBe(true);
      expect(btn.textContent).toContain('ABORTAR MISSÃO');
      expect(host.textContent).not.toMatch(/pouso comandado|pousando|pousada/i);
      expect(btn.className).not.toMatch(/animate-|active:scale|transition/);
      // Refuses a second press, but keeps its look while the abort lands.
      expect(btn.className).toContain('border-ember');
      expect(btn.className).not.toContain('bg-hull');
    }
  );

  it('does not flash when pressed', () => {
    const onAbort = vi.fn();
    act(() => root.render(<AbortControl onAbort={onAbort} />));
    const btn = getButton();
    const before = btn.className;
    act(() => {
      btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    expect(onAbort).toHaveBeenCalledTimes(1);
    expect(getButton().className).toBe(before);
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
