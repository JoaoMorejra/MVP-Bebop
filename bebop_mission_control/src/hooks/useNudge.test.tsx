// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NudgeIntent } from '../types/bmg';
import { useNudge } from './useNudge';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
let sendNudge: ReturnType<typeof vi.fn>;

const Probe: React.FC<{ enabled: boolean }> = ({ enabled }) => {
  useNudge({ sendNudge: sendNudge as unknown as (intent: NudgeIntent) => void }, enabled);
  return null;
};

async function mount(enabled = true): Promise<void> {
  await act(async () => {
    root.render(<Probe enabled={enabled} />);
  });
}

function press(type: 'keydown' | 'keyup', code: string, init: KeyboardEventInit = {}, target: EventTarget = window): KeyboardEvent {
  const event = new KeyboardEvent(type, { code, bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}

const sent = (): NudgeIntent[] => sendNudge.mock.calls.map(([intent]) => intent as NudgeIntent);
const RELEASE = { vx: 0, vy: 0, vyaw: 0 };

beforeEach(() => {
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  sendNudge = vi.fn();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('useNudge', () => {
  it('sends on the change and then on the heartbeat only', async () => {
    await mount();
    press('keydown', 'KeyW');
    expect(sent()).toEqual([{ vx: 1, vy: 0, vyaw: 0 }]);
    for (let i = 0; i < 20; i += 1) {
      press('keydown', 'KeyW', { repeat: true });
      act(() => {
        vi.advanceTimersByTime(50);
      });
    }
    const count = sent().length;
    expect(count).toBeGreaterThanOrEqual(9);
    expect(count).toBeLessThanOrEqual(11);
  });

  it('a repeat generates no extra message', async () => {
    await mount();
    press('keydown', 'KeyA');
    press('keydown', 'KeyA', { repeat: true });
    press('keydown', 'KeyA', { repeat: true });
    expect(sent()).toEqual([{ vx: 0, vy: 1, vyaw: 0 }]);
  });

  it('releasing every key sends the zero intent at once and stops the heartbeat', async () => {
    await mount();
    press('keydown', 'KeyW');
    press('keydown', 'KeyQ');
    press('keyup', 'KeyW');
    press('keyup', 'KeyQ');
    expect(sent()[sent().length - 1]).toEqual(RELEASE);
    const count = sent().length;
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(sent().length).toBe(count);
  });

  it('blur and a hidden document release the keys', async () => {
    await mount();
    press('keydown', 'KeyD');
    act(() => {
      window.dispatchEvent(new Event('blur'));
    });
    expect(sent()[sent().length - 1]).toEqual(RELEASE);
    press('keydown', 'KeyD');
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    expect(sent()[sent().length - 1]).toEqual(RELEASE);
  });

  it('disabling with a key held releases and ignores new keys until re-enabled', async () => {
    await mount(true);
    press('keydown', 'KeyW');
    await mount(false);
    expect(sent()[sent().length - 1]).toEqual(RELEASE);
    const count = sent().length;
    press('keydown', 'KeyS');
    expect(sent().length).toBe(count);
    await mount(true);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(sent().length).toBe(count);
    press('keydown', 'KeyA');
    expect(sent()[sent().length - 1]).toEqual({ vx: 0, vy: 1, vyaw: 0 });
  });

  it('leaves text fields alone but captures on a range slider', async () => {
    await mount();
    const text = document.createElement('input');
    text.type = 'text';
    const area = document.createElement('textarea');
    const range = document.createElement('input');
    range.type = 'range';
    document.body.append(text, area, range);
    press('keydown', 'KeyW', {}, text);
    press('keydown', 'KeyW', {}, area);
    expect(sent()).toEqual([]);
    press('keydown', 'KeyW', {}, range);
    expect(sent()).toEqual([{ vx: 1, vy: 0, vyaw: 0 }]);
    text.remove();
    area.remove();
    range.remove();
  });

  it('prevents the default only for the mapped keys while enabled', async () => {
    await mount(true);
    expect(press('keydown', 'KeyW').defaultPrevented).toBe(true);
    expect(press('keydown', 'KeyX').defaultPrevented).toBe(false);
    expect(press('keydown', 'KeyR', { ctrlKey: true }).defaultPrevented).toBe(false);
    await mount(false);
    expect(press('keydown', 'KeyA').defaultPrevented).toBe(false);
  });

  it('unmounting sends the zero intent', async () => {
    await mount();
    press('keydown', 'KeyW');
    act(() => root.unmount());
    expect(sent()[sent().length - 1]).toEqual(RELEASE);
    root = createRoot(container);
  });
});
