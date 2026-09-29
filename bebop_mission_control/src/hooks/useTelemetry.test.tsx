// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { BmgTelemetry } from '../types/bmg';
import type { TelemetryView } from '../types/mission';
import { useTelemetry } from './useTelemetry';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let push: (data: BmgTelemetry) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;
let latest: TelemetryView | null = null;
let latestHook: ReturnType<typeof useTelemetry> | null = null;

const Probe: React.FC = () => {
  latestHook = useTelemetry();
  latest = latestHook.telemetry;
  return null;
};

const frame = (extra: Partial<BmgTelemetry>): BmgTelemetry => ({
  connected: true,
  driver_running: true,
  battery_pct: 63,
  wifi_ssid: 'Bebop2-A035633',
  wifi_signal_dbm: -41,
  speed: 0.4,
  altitude: 1.8,
  flight_time_sec: 42,
  heading: 90,
  latitude: -22.42,
  longitude: -45.45,
  battery_known: true,
  data_fresh: true,
  ...extra,
});

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    getTelemetry: () => new Promise(() => undefined),
    onTelemetryUpdate: (listener: (data: BmgTelemetry) => void) => {
      push = listener;
      return () => undefined;
    },
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<Probe />));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

describe('useTelemetry flight readouts', () => {
  it('passes every flight field through while the driver publishes', () => {
    act(() => push(frame({})));
    expect(latest?.data_fresh).toBe(true);
    expect([latest?.altitude, latest?.speed, latest?.wifi_signal_dbm]).toEqual([1.8, 0.4, -41]);
  });

  it('blanks them when the aircraft answers pings but the driver has stopped', () => {
    act(() => push(frame({ data_fresh: false, driver_running: false })));
    expect(latest?.connected).toBe(true);
    expect(latest?.data_fresh).toBe(false);
    expect([latest?.altitude, latest?.speed, latest?.heading, latest?.wifi_signal_dbm]).toEqual([0, 0, 0, -100]);
  });

  it('blanks flight time and position too once the aircraft is unreachable', () => {
    act(() => push(frame({ connected: false })));
    expect(latest?.connected).toBe(false);
    expect([latest?.flight_time_sec, latest?.latitude, latest?.longitude]).toEqual([0, 0, 0]);
    expect(latest?.battery_known).toBe(false);
  });

  it('reads an older bridge without data_fresh through driver_running', () => {
    act(() => push(frame({ data_fresh: undefined, driver_running: false })));
    expect(latest?.data_fresh).toBe(false);
    expect(latest?.altitude).toBe(0);
  });
});

describe('useTelemetry benchtop simulation', () => {
  const bench = (extra: Partial<BmgTelemetry>): BmgTelemetry =>
    frame({
      connected: false,
      driver_running: false,
      data_fresh: false,
      battery_known: false,
      simulated: true,
      nav_fresh: true,
      nav_source: 'simulator',
      altitude: 1.8,
      speed: 0.2,
      flight_time_sec: 12,
      east_m: 0,
      north_m: 0,
      ...extra,
    });

  it('shows the simulated airframe without claiming a link or a driver', () => {
    act(() => push(bench({})));
    expect(latest?.simulated).toBe(true);
    expect(latest?.nav_fresh).toBe(true);
    expect([latest?.altitude, latest?.speed, latest?.flight_time_sec]).toEqual([1.8, 0.2, 12]);
    expect(latest?.connected).toBe(false);
    expect(latest?.driver_running).toBe(false);
    expect(latest?.data_fresh).toBe(false);
    expect(latest?.battery_known).toBe(false);
    expect(latestHook?.stale).toBe(false);
  });

  it('draws the simulated trail on the map', () => {
    act(() => push(bench({ east_m: 0, north_m: 0 })));
    act(() => push(bench({ east_m: 0.5, north_m: 1.5 })));
    const track = latestHook?.track ?? [];
    expect(track.length).toBe(2);
    expect([track[1].x, track[1].y]).toEqual([0.5, 1.5]);
  });

  it('blanks navigation again once the simulation stops', () => {
    act(() => push(bench({})));
    act(() => push(frame({ connected: false, driver_running: false, data_fresh: false, simulated: false, nav_fresh: false })));
    expect(latest?.nav_fresh).toBe(false);
    expect([latest?.altitude, latest?.speed, latest?.flight_time_sec]).toEqual([0, 0, 0]);
    expect(latestHook?.stale).toBe(true);
  });
});
