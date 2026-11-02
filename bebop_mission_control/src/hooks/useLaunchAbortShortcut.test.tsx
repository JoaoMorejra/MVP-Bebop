// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const state: { bridge: Record<string, unknown> | null } = { bridge: null };
vi.mock('./useBridge', () => ({ useBridge: () => state.bridge }));

import { useLaunchAbortShortcut } from './useLaunchAbortShortcut';

let host: HTMLDivElement;
let root: Root;
let shortcut: () => void = () => undefined;
const armedCalls: boolean[] = [];

function fakeBridge() {
  armedCalls.length = 0;
  state.bridge = {
    setCockpitAbortArmed: (armed: boolean) => armedCalls.push(armed),
    onAbortShortcut: (cb: () => void) => {
      shortcut = cb;
      return () => {
        shortcut = () => undefined;
      };
    },
  };
}

const Probe: React.FC<{ armed: boolean; handoff: boolean; onAbort: () => void }> = (props) => {
  useLaunchAbortShortcut(props);
  return null;
};

function render(armed: boolean, handoff: boolean, onAbort = vi.fn()) {
  act(() => root.render(<Probe armed={armed} handoff={handoff} onAbort={onAbort} />));
  return onAbort;
}

const key = (init: KeyboardEventInit, target: EventTarget = window) => {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  state.bridge = null;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe('useLaunchAbortShortcut with the bridge', () => {
  it('arming publishes true and onAbortShortcut triggers onAbort', () => {
    fakeBridge();
    const onAbort = render(true, false);
    expect(armedCalls).toEqual([true]);
    act(() => shortcut());
    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it('disarming publishes false', () => {
    fakeBridge();
    const onAbort = render(true, false);
    render(false, false, onAbort);
    expect(armedCalls).toEqual([true, false]);
    act(() => shortcut());
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('disarming with handoff true does not publish false', () => {
    fakeBridge();
    const onAbort = render(true, false);
    render(false, true, onAbort);
    expect(armedCalls).toEqual([true]);
  });

  it('unmounting does not publish false when handoff is true', () => {
    fakeBridge();
    render(true, false);
    render(true, true);
    act(() => root.unmount());
    root = createRoot(host);
    expect(armedCalls).toEqual([true]);
  });

  it('unmounting while armed without handoff publishes false', () => {
    fakeBridge();
    render(true, false);
    act(() => root.unmount());
    root = createRoot(host);
    expect(armedCalls).toEqual([true, false]);
  });
});

describe('useLaunchAbortShortcut without a bridge', () => {
  it('without a bridge Escape and Ctrl+C trigger onAbort through the window listener while armed', () => {
    const onAbort = render(true, false);
    expect(key({ key: 'Escape' }).defaultPrevented).toBe(true);
    key({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).toHaveBeenCalledTimes(2);
  });

  it('ignores key repeat', () => {
    const onAbort = render(true, false);
    key({ key: 'Escape', repeat: true });
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('ignores Ctrl+C inside a text input', () => {
    const onAbort = render(true, false);
    const input = document.createElement('input');
    input.type = 'text';
    document.body.appendChild(input);
    key({ key: 'c', code: 'KeyC', ctrlKey: true }, input);
    input.remove();
    expect(onAbort).not.toHaveBeenCalled();
  });

  it('does nothing while not armed', () => {
    const onAbort = render(false, false);
    const event = key({ key: 'c', code: 'KeyC', ctrlKey: true });
    expect(onAbort).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });
});
