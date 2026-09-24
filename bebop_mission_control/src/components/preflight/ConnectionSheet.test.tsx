// @vitest-environment jsdom
import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { WifiNetwork } from '../../types/bmg';
import type { TelemetryView } from '../../types/mission';
import { BEBOP_SSID_RE, bebopNetworks, isBebopSsid } from '../../lib/wifi';
import { ConnectionSheet } from './ConnectionSheet';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const net = (ssid: string, extra: Partial<WifiNetwork> = {}): WifiNetwork => ({
  ssid,
  signal: 60,
  active: false,
  isBebop: /^Bebop2?[-_]/i.test(ssid),
  ...extra,
});

/** What `nmcli dev wifi list` returns in a lab full of other networks. */
const MIXED: WifiNetwork[] = [
  net('Bebop2-051234'),
  net('Bebop-9A1F'),
  net('Bebop_Lab'),
  net('Bebop2_Hangar'),
  net('bebop2-lowercase'),
  net('TECH4H-Corp', { secure: true }),
  net('BebopDrone-1'),
  net('MyBebop2-Clone'),
  net('Bebop2'),
  net('Bebop 2 Hotspot'),
  net('Parrot-Anafi-7731'),
  net('iPhone de Joao', { secure: true }),
];

const ALLOWED = ['Bebop2-051234', 'Bebop-9A1F', 'Bebop_Lab', 'Bebop2_Hangar', 'bebop2-lowercase'];

describe('Bebop SSID filter', () => {
  it('keeps only Bebop-*, Bebop2-*, Bebop_* and Bebop2_* networks', () => {
    expect(bebopNetworks(MIXED).map((n) => n.ssid)).toEqual(ALLOWED);
  });

  it('does not trust a stray isBebop flag on a network outside the pattern', () => {
    expect(bebopNetworks([net('TECH4H-Corp', { isBebop: true })])).toEqual([]);
  });

  it('keeps the active drone link main.cjs synthesizes when nmcli cannot name it', () => {
    // electron/main.cjs: a route to the Bebop with no SSID becomes
    // { ssid: 'Parrot Bebop 2', active: true, isBebop: true }.
    const synthesized = net('Parrot Bebop 2', { active: true, isBebop: true, signal: 85 });
    expect(bebopNetworks([synthesized, ...MIXED])[0]).toBe(synthesized);
    expect(bebopNetworks([{ ...synthesized, active: false }])).toEqual([]);
  });

  it('accepts exactly the prefixes, case-insensitively', () => {
    for (const ssid of ALLOWED) expect(isBebopSsid(ssid), ssid).toBe(true);
    for (const ssid of ['BebopDrone-1', 'MyBebop2-Clone', 'Bebop2', 'Bebop 2', '', ' Bebop2-x']) {
      expect(isBebopSsid(ssid), ssid).toBe(false);
    }
  });

  it('is the same pattern the main process tags networks with', () => {
    const main = readFileSync(resolve(__dirname, '../../../electron/main.cjs'), 'utf-8');
    const declared = /const BEBOP_SSID_RE = \/(.+)\/([a-z]*);/.exec(main);
    expect(declared).not.toBeNull();
    expect(declared![1]).toBe(BEBOP_SSID_RE.source);
    expect(declared![2]).toBe(BEBOP_SSID_RE.flags);
  });
});

describe('ConnectionSheet', () => {
  let container: HTMLDivElement;
  let root: Root;

  const telemetry = {
    connected: false,
    altitude: 0,
    battery_known: false,
    battery_pct: 0,
    battery_source: 'none',
    gps_fix: false,
    latitude: 0,
    longitude: 0,
    signal_source: 'none',
    wifi_signal_dbm: null,
  } as unknown as TelemetryView;

  const render = (networks: WifiNetwork[]) =>
    act(() =>
      root.render(
        <ConnectionSheet
          open
          onClose={() => undefined}
          telemetry={telemetry}
          networks={networks}
          currentSsid=""
          phase="idle"
          message={null}
          progress={[]}
          readiness={null}
          flightReady={false}
          driverRunning={false}
          onScan={() => undefined}
          onConnect={() => undefined}
          onStartDriver={() => undefined}
          onStopDriver={() => undefined}
        />
      )
    );

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

  it('lists only the Bebop networks out of a mixed scan', () => {
    render(MIXED);
    const listed = Array.from(container.querySelectorAll('li button .font-mono')).map(
      (node) => node.textContent
    );
    expect(listed).toEqual(ALLOWED);
    for (const other of ['TECH4H-Corp', 'BebopDrone-1', 'MyBebop2-Clone', 'iPhone de Joao']) {
      expect(container.textContent).not.toContain(other);
    }
  });

  it('says the list is filtered when no Bebop network is in range', () => {
    render(MIXED.filter((n) => !ALLOWED.includes(n.ssid)));
    expect(container.querySelectorAll('li button').length).toBe(0);
    expect(container.textContent).toContain('Nenhuma rede do Bebop por perto');
    expect(container.textContent).toContain('Bebop2-');
    expect(container.textContent).toContain('Outras redes não aparecem aqui');
  });
});
