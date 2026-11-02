// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const bridge = {
  scanWifi: vi.fn(),
  ensureLink: vi.fn(),
  connectWifi: vi.fn(async () => ({ success: true })),
  stopDriver: vi.fn(async () => undefined),
  onLinkProgress: vi.fn(() => () => undefined),
  getLinkReadiness: vi.fn(() => new Promise(() => undefined)),
  checkDriverStatus: vi.fn(() => new Promise(() => undefined)),
};
vi.mock('./useBridge', () => ({ useBridge: () => bridge }));
vi.mock('../audio/cues', () => ({ cues: { play: () => undefined } }));

import { useLink } from './useLink';

let latest: ReturnType<typeof useLink>;
const Probe: React.FC<{ connected: boolean }> = ({ connected }) => {
  latest = useLink({ connected, driverRunning: false });
  return null;
};

let host: HTMLDivElement;
let root: Root;

const failed = { success: false, stage: 'ping', message: 'sem resposta', readiness: null };

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-27T12:00:00Z'));
  bridge.ensureLink.mockReset();
  bridge.ensureLink.mockResolvedValue(failed);
  bridge.scanWifi.mockReset();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

async function mount(ssid: string, connected = false) {
  bridge.scanWifi.mockResolvedValue({ networks: [{ ssid, signal: 70, active: true, isBebop: /^Bebop/.test(ssid) }], currentSsid: ssid });
  await act(async () => {
    root.render(<Probe connected={connected} />);
  });
  await act(async () => undefined);
}

async function rerender(connected: boolean) {
  await act(async () => {
    root.render(<Probe connected={connected} />);
  });
}

describe('useLink automatic bring-up', () => {
  it('starts the driver automatically when joined to a Bebop SSID even before the aircraft answers', async () => {
    await mount('Bebop2-104', false);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(1);
    expect(bridge.ensureLink).toHaveBeenCalledWith({});
  });

  it('does not auto start on a non Bebop SSID', async () => {
    await mount('Escritorio', false);
    expect(bridge.ensureLink).not.toHaveBeenCalled();
  });

  it('does not auto start after a manual stop until a new connect', async () => {
    await mount('Bebop2-104', false);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(1);
    await act(async () => {
      await latest.stopDriver();
    });
    vi.setSystemTime(Date.now() + 60_000);
    await rerender(true);
    await rerender(false);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(1);

    await act(async () => {
      await latest.connect('Bebop2-104');
    });
    expect(bridge.ensureLink).toHaveBeenCalledTimes(2);
    expect(bridge.ensureLink).toHaveBeenLastCalledWith({ ssid: 'Bebop2-104' });
    vi.setSystemTime(Date.now() + 60_000);
    await rerender(true);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(3);
  });

  it('respects the retry cooldown', async () => {
    await mount('Bebop2-104', false);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 5_000);
    await rerender(true);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 11_000);
    await rerender(false);
    expect(bridge.ensureLink).toHaveBeenCalledTimes(2);
  });
});
