// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LaunchDial } from './LaunchDial';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

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
  vi.useFakeTimers();

  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    return setTimeout(() => cb(Date.now()), 16);
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    clearTimeout(id);
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const getButton = (): HTMLButtonElement => host.querySelector('button')!;

describe('LaunchDial (R4 / R8 Arming Hold & Preflight Gate)', () => {
  describe('bench mode', () => {
    it('displays motors offline text and launches on single click', () => {
      const onLaunch = vi.fn();
      act(() => {
        root.render(
          <LaunchDial ready={true} blockedReason={null} benchMode={true} onLaunch={onLaunch} />
        );
      });

      const btn = getButton();
      expect(btn.disabled).toBe(false);
      expect(host.textContent).toContain('INICIAR');
      expect(host.textContent).toContain('Motores desligados');

      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onLaunch).toHaveBeenCalledTimes(1);
    });

    it('prevents double launch on repeated rapid clicks in bench mode', () => {
      const onLaunch = vi.fn();
      act(() => {
        root.render(
          <LaunchDial ready={true} blockedReason={null} benchMode={true} onLaunch={onLaunch} />
        );
      });

      const btn = getButton();
      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onLaunch).toHaveBeenCalledTimes(1);
    });
  });

  describe('real flight mode', () => {
    it('displays warning text and launches on single click', () => {
      const onLaunch = vi.fn();
      act(() => {
        root.render(
          <LaunchDial ready={true} blockedReason={null} benchMode={false} onLaunch={onLaunch} />
        );
      });

      const btn = getButton();
      expect(btn.disabled).toBe(false);
      expect(host.textContent).toContain('INICIAR');
      expect(host.textContent).toContain('VOO REAL: motores serão armados');

      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onLaunch).toHaveBeenCalledTimes(1);
    });

    it('prevents double launch on repeated rapid clicks in real flight mode', () => {
      const onLaunch = vi.fn();
      act(() => {
        root.render(
          <LaunchDial ready={true} blockedReason={null} benchMode={false} onLaunch={onLaunch} />
        );
      });

      const btn = getButton();
      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onLaunch).toHaveBeenCalledTimes(1);
    });
  });

  describe('blocked state', () => {
    it('disables button and displays blocked reason', () => {
      const onLaunch = vi.fn();
      act(() => {
        root.render(
          <LaunchDial
            ready={false}
            blockedReason="Driver ROS 2 fora do ar"
            benchMode={false}
            onLaunch={onLaunch}
          />
        );
      });

      const btn = getButton();
      expect(btn.disabled).toBe(true);
      expect(host.textContent).toContain('Driver ROS 2 fora do ar');

      act(() => {
        btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      });
      expect(onLaunch).not.toHaveBeenCalled();
    });
  });
});
