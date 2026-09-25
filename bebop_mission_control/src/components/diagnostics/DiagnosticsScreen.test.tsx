// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BmgAPI, TerminalDataEvent, TerminalExitEvent } from '../../types/bmg';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type OscHandler = (payload: string) => boolean;
const terminals: Array<{ osc: Map<number, OscHandler>; focused: number }> = [];

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    record = { osc: new Map<number, OscHandler>(), focused: 0 };
    parser = {
      registerOscHandler: (code: number, handler: OscHandler) => {
        this.record.osc.set(code, handler);
        return { dispose: () => undefined };
      },
    };
    constructor() {
      terminals.push(this.record);
    }
    loadAddon() {}
    open() {}
    write() {}
    focus() {
      this.record.focused += 1;
    }
    dispose() {}
    onData() {
      return { dispose: () => undefined };
    }
  },
}));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }));
vi.mock('@xterm/xterm/css/xterm.css', () => ({}));

const { DiagnosticsScreen } = await import('./DiagnosticsScreen');

let container: HTMLDivElement;
let root: Root;
let seq = 0;
let exitListeners: Array<(event: TerminalExitEvent) => void> = [];
const killed: string[] = [];

function installBridge() {
  seq = 0;
  exitListeners = [];
  killed.length = 0;
  const bridge: Partial<BmgAPI> = {
    terminalSpawn: async () => {
      seq += 1;
      return {
        success: true,
        id: `pty-${seq}`,
        pid: 1000 + seq,
        cols: 80,
        rows: 24,
        cwd: '/home/op/ros2_ws',
        shell: 'bash',
        user: 'op',
        host: 'gcs',
        home: '/home/op',
      };
    },
    terminalWrite: async () => ({ success: true }),
    terminalResize: async () => ({ success: true }),
    terminalKill: async (id: string) => {
      killed.push(id);
      return { success: true };
    },
    onTerminalData: (_listener: (event: TerminalDataEvent) => void) => () => undefined,
    onTerminalExit: (listener: (event: TerminalExitEvent) => void) => {
      exitListeners.push(listener);
      return () => {
        exitListeners = exitListeners.filter((l) => l !== listener);
      };
    },
  };
  (window as unknown as { bmgAPI: Partial<BmgAPI> }).bmgAPI = bridge;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function render(onClose = vi.fn()) {
  act(() =>
    root.render(
      <DiagnosticsScreen
        missionRunning={false}
        onLand={() => undefined}
        onClose={onClose}
      />
    )
  );
  await flush();
  return onClose;
}

const tabs = () => Array.from(container.querySelectorAll('[role="tab"]')) as HTMLButtonElement[];
const title = () => container.querySelector('[data-terminal-title]')!.textContent;
const button = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  terminals.length = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  installBridge();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe('DiagnosticsScreen shell tabs', () => {
  it('opens with one tab, titled user@host: cwd — shell, and no close control on it', async () => {
    await render();
    expect(tabs()).toHaveLength(1);
    expect(title()).toBe('op@gcs: ~/ros2_ws — bash');
    expect(tabs()[0].textContent).toContain('ros2_ws');
    expect(button('Fechar aba 1')).toBeNull();
  });

  it('draws only the terminal: no log pane, no log source selector', async () => {
    await render();
    expect(container.textContent).not.toContain('logs:');
    expect(container.textContent).not.toContain('sem linhas de log');
    expect(container.querySelectorAll('[aria-label="Terminal bash"]')).toHaveLength(1);
  });

  it('gives every new tab its own PTY and selects it', async () => {
    await render();
    act(() => button('Nova aba').click());
    await flush();
    act(() => button('Nova aba').click());
    await flush();

    expect(tabs()).toHaveLength(3);
    expect(seq).toBe(3);
    expect(tabs().map((t) => t.getAttribute('aria-selected'))).toEqual(['false', 'false', 'true']);
    act(() => tabs()[0].click());
    expect(tabs()[0].getAttribute('aria-selected')).toBe('true');
  });

  it('closes exactly the tab whose X was pressed, and kills only its PTY', async () => {
    await render();
    act(() => button('Nova aba').click());
    await flush();
    act(() => button('Nova aba').click());
    await flush();

    act(() => button('Fechar aba 2').click());
    await flush();
    expect(killed).toEqual(['pty-2']);
    expect(tabs()).toHaveLength(2);
  });

  it('never closes the last tab from its X', async () => {
    await render();
    act(() => button('Nova aba').click());
    await flush();
    act(() => button('Fechar aba 2').click());
    await flush();
    expect(tabs()).toHaveLength(1);
    expect(button('Fechar aba 1')).toBeNull();
  });

  it('drops a tab whose shell exits, and closes the terminal when the last one does', async () => {
    const onClose = await render();
    act(() => button('Nova aba').click());
    await flush();

    act(() => exitListeners.forEach((l) => l({ id: 'pty-1', exitCode: 0, signal: null })));
    expect(tabs()).toHaveLength(1);
    expect(onClose).not.toHaveBeenCalled();

    act(() => exitListeners.forEach((l) => l({ id: 'pty-2', exitCode: 0, signal: null })));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('follows the shell into a new directory through OSC 7', async () => {
    await render();
    act(() => {
      terminals[0].osc.get(7)!('file://gcs/opt/ros/jazzy');
    });
    expect(title()).toBe('op@gcs: /opt/ros/jazzy — bash');
    expect(tabs()[0].textContent).toContain('jazzy');
  });

  it('kills every PTY when the overlay closes', async () => {
    await render();
    act(() => button('Nova aba').click());
    await flush();
    act(() => root.render(<></>));
    await flush();
    expect(killed.sort()).toEqual(['pty-1', 'pty-2']);
  });
});
