// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScrambleText, TiltLogo } from './motion';
import { TabSwitch } from './TabSwitch';

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
  vi.useRealTimers();
});

const Pointer: typeof MouseEvent = (window as unknown as { PointerEvent?: typeof MouseEvent }).PointerEvent ?? MouseEvent;

const visible = () => host.querySelector('[aria-hidden]')?.textContent ?? '';

describe('ScrambleText', () => {
  it('shows the plain name when mounted already loaded', () => {
    act(() => root.render(<ScrambleText text="odom" scrambling={false} />));
    expect(visible()).toBe('odom');
  });

  it('churns while loading and resolves to the name once loaded', () => {
    vi.useFakeTimers();
    act(() => root.render(<ScrambleText text="cmd_vel" scrambling />));
    expect(visible()).not.toBe('cmd_vel');
    expect(host.querySelector('.sr-only')?.textContent).toBe('cmd_vel');
    act(() => root.render(<ScrambleText text="cmd_vel" scrambling={false} />));
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(visible()).toBe('cmd_vel');
  });
});

describe('TiltLogo', () => {
  it('leans toward the pointer and settles back on leave', () => {
    act(() => root.render(<TiltLogo height={30} />));
    const wrap = host.querySelector<HTMLElement>('[data-logo-tilt]')!;
    const card = wrap.firstElementChild as HTMLElement;
    wrap.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 40 } as DOMRect);
    act(() => {
      wrap.dispatchEvent(new Pointer('pointermove', { bubbles: true, clientX: 100, clientY: 0 }));
    });
    expect(wrap.classList.contains('is-hover')).toBe(true);
    expect(card.style.getPropertyValue('--tilt-ry')).toBe('13.00deg');
    act(() => {
      wrap.dispatchEvent(new Pointer('pointerout', { bubbles: true, relatedTarget: document.body }));
    });
    expect(card.style.getPropertyValue('--tilt-ry')).toBe('0deg');
    expect(wrap.classList.contains('is-hover')).toBe(false);
  });
});

describe('TabSwitch', () => {
  it('slides one pill under the active tab', () => {
    act(() => root.render(<TabSwitch screen="preflight" onNavigate={() => undefined} live={false} locked={false} />));
    expect(host.querySelectorAll('[data-tab-pill]')).toHaveLength(1);
    const pill = host.querySelector<HTMLElement>('[data-tab-pill]')!;
    expect(pill.style.transform).toMatch(/^translateX\(/);
    expect(host.querySelector('[aria-selected="true"]')?.textContent).toBe('Inicio');
  });
});
