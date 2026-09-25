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

const Probe: React.FC = () => {
  latest = useTelemetry().telemetry;
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
