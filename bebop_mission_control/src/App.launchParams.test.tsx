// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BatteryFailsafe } from './types/bmg';
import { LAND_PCT_PATH, launchParamsJson } from './lib/batteryFailsafe';
import { getPath } from './lib/paths';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

/**
 * The launch and bench payloads are built inside `App`, from the flyout state
 * the operator last set. The hooks and screens are stood in for so the test
 * reaches the two real call sites and reads what they hand the host.
 */
const hoisted = vi.hoisted(() => {
  const doc = {
    kinematics: { target_altitude_m: 1.6, countdown_sec: 0 },
    network: { drone_ip: '192.168.42.1' },
    battery: { land_pct: 99 },
  };
  const props: Record<string, Record<string, unknown>> = {};
  const capture = (name: string) => (next: Record<string, unknown>) => {
    props[name] = next;
    return null;
  };
  const launch = vi.fn(async (_options: Record<string, unknown>) => ({ success: true }));
  const startBenchStage = vi.fn(async (_options: Record<string, unknown>) => ({ success: true }));
  // One instance per hook for the whole test, as a memoized hook would return:
  // a fresh object per render would recreate every callback that depends on
  // it and hide a callback that fails to depend on the flyout state.
  const stable = {
    bridge: {
      startBenchStage,
      endMission: async () => undefined,
      gotoStage: async () => ({ success: true }),
      abortMission: async () => undefined,
      onMissionReset: () => () => undefined,
    },
    telemetry: {
      telemetry: { connected: false, battery_known: false, battery_pct: 0 },
      track: [],
      stale: false,
      resetTrack: () => undefined,
    },
    link: { networks: [], flightReady: true, driverRunning: true },
    mission: {
      state: 'idle',
      stage: 0,
      ran: false,
      launch,
      abort: async () => undefined,
      reset: () => undefined,
      beginBenchStage: () => undefined,
    },
    params: { working: doc, committed: doc, dirty: false, save: async () => undefined },
    stream: { fps: 0 },
    camera: { tilt: -20, available: false, set: () => undefined, reset: () => undefined },
    copilot: { say: async () => undefined, cancel: () => undefined, prepare: () => undefined },
    narration: {},
    forensic: { revealed: false, closing: false },
    voice: { level: 1, setVolume: () => undefined, toggleMute: () => undefined },
  };
  return { doc, props, capture, launch, startBenchStage, stable };
});

vi.mock('./components/preflight/PreflightScreen', () => ({ PreflightScreen: hoisted.capture('preflight') }));
vi.mock('./components/cockpit/CockpitScreen', () => ({ CockpitScreen: hoisted.capture('cockpit') }));
vi.mock('./components/preflight/CountdownOverlay', () => ({ CountdownOverlay: () => null }));
vi.mock('./components/diagnostics/DiagnosticsScreen', () => ({ DiagnosticsScreen: () => null }));
vi.mock('./components/diagnostics/DiagnosticsOverlayHeader', () => ({
  DiagnosticsOverlayHeader: () => null,
}));
vi.mock('./components/shell/TabSwitch', () => ({ TabSwitch: hoisted.capture('tabs') }));

vi.mock('./hooks/useBridge', () => ({ useBridge: () => hoisted.stable.bridge }));
vi.mock('./hooks/useTelemetry', () => ({ useTelemetry: () => hoisted.stable.telemetry }));
vi.mock('./hooks/useLink', () => ({ useLink: () => hoisted.stable.link }));
vi.mock('./hooks/useMissionRuntime', () => ({ useMissionRuntime: () => hoisted.stable.mission }));
vi.mock('./hooks/useMissionParameters', () => ({
  useMissionParameters: () => hoisted.stable.params,
}));
vi.mock('./hooks/useStreamHealth', () => ({ useStreamHealth: () => hoisted.stable.stream }));
vi.mock('./hooks/useCameraTilt', () => ({ useCameraTilt: () => hoisted.stable.camera }));
vi.mock('./hooks/useCopilot', () => ({
  useCopilot: () => hoisted.stable.copilot,
  useFlightNarration: () => undefined,
  useForensicNarration: () => hoisted.stable.forensic,
  useNarrationQueue: () => hoisted.stable.narration,
}));
vi.mock('./hooks/useVoiceLevel', () => ({ useVoiceLevel: () => hoisted.stable.voice }));
vi.mock('./hooks/useMissionCountdown', () => ({
  useMissionCountdown: () => ({ remaining: null, clearance: false }),
}));

import { App } from './App';

let host: HTMLDivElement;
let root: Root;

async function mount(): Promise<void> {
  await act(async () => {
    root.render(<App />);
  });
}

function call(screen: string, prop: string, ...args: unknown[]): unknown {
  const fn = hoisted.props[screen]?.[prop];
  if (typeof fn !== 'function') throw new Error(`${screen}.${prop} was not rendered`);
  return (fn as (...a: unknown[]) => unknown)(...args);
}

async function setThreshold(thresholdPct: number): Promise<void> {
  const next: BatteryFailsafe = { enabled: true, thresholdPct };
  await act(async () => {
    call('preflight', 'onFailsafeChange', next);
  });
}

function landPctSent(options: Record<string, unknown> | undefined): unknown {
  return getPath(JSON.parse(String(options?.paramsJson)), LAND_PCT_PATH);
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  hoisted.launch.mockClear();
  hoisted.startBenchStage.mockClear();
  for (const key of Object.keys(hoisted.props)) delete hoisted.props[key];
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe('battery.land_pct in the launch payload', () => {
  it('carries the flyout threshold, replacing whatever the document held', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });

    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    const options = hoisted.launch.mock.calls[0][0];
    expect(landPctSent(options)).toBe(20);
    expect(getPath(JSON.parse(String(options.paramsJson)), 'kinematics.target_altitude_m')).toBe(1.6);
  });

  it('carries the value the operator moved the slider to before launching', async () => {
    await mount();
    await setThreshold(34);
    await act(async () => {
      await call('preflight', 'onLaunch');
    });

    expect(landPctSent(hoisted.launch.mock.calls[0][0])).toBe(34);
  });

  it('never writes the injected value back into the parameter document', async () => {
    await mount();
    await setThreshold(34);
    await act(async () => {
      await call('preflight', 'onLaunch');
    });

    expect(hoisted.doc.battery.land_pct).toBe(99);
  });
});

describe('battery.land_pct in the bench-stage payload', () => {
  it('carries the flyout threshold', async () => {
    window.history.replaceState(null, '', '/?screen=cockpit');
    await mount();
    await act(async () => {
      await call('cockpit', 'onRunStage', 2);
    });

    expect(hoisted.startBenchStage).toHaveBeenCalledTimes(1);
    expect(landPctSent(hoisted.startBenchStage.mock.calls[0][0])).toBe(20);
  });

  it('carries the value the operator moved the slider to before running the routine', async () => {
    await mount();
    await setThreshold(27);
    await act(async () => {
      call('tabs', 'onNavigate', 'cockpit');
    });
    await act(async () => {
      await call('cockpit', 'onRunStage', 5);
    });

    expect(landPctSent(hoisted.startBenchStage.mock.calls[0][0])).toBe(27);
  });
});

describe('launchParamsJson', () => {
  it('writes the threshold under battery.land_pct and keeps every other field', () => {
    const doc = { rtl: { timeout_sec: 60 }, battery: { other: 1 } };
    const sent = JSON.parse(launchParamsJson(doc, 15));

    expect(sent).toEqual({ rtl: { timeout_sec: 60 }, battery: { other: 1, land_pct: 15 } });
    expect(doc.battery).toEqual({ other: 1 });
  });

  it('builds a document when there is none', () => {
    expect(JSON.parse(launchParamsJson(null, 20))).toEqual({ battery: { land_pct: 20 } });
  });
});
