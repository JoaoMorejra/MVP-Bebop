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
  const doc: Record<string, any> = {
    kinematics: {
      target_altitude_m: 1.6,
      altitude_ceiling_margin_m: 0.6,
      forward_cruise_velocity: 0.2,
      takeoff_stabilize_duration_sec: 2,
      hover_duration_sec: 7,
      countdown_sec: 0,
    },
    gimbal: { search_tilt_deg: -20, nadir_tilt_deg: -69 },
    timeouts: { search_timeout_sec: 30 },
    rtl: { timeout_sec: 60, max_speed: 0.1, arrival_radius_m: 0.2 },
    vision: { confidence_threshold: 0.5, model_path: 'yolov8n.pt' },
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
    params: {
      working: doc as Record<string, any>,
      committed: doc as Record<string, any>,
      status: 'ready' as string,
      dirty: false,
      save: vi.fn(async (): Promise<boolean> => true),
    },
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
vi.mock('./components/preflight/CountdownOverlay', () => ({ CountdownOverlay: hoisted.capture('countdown') }));
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
  hoisted.stable.params.working = hoisted.doc;
  hoisted.stable.params.committed = hoisted.doc;
  hoisted.stable.params.status = 'ready';
  hoisted.stable.params.dirty = false;
  hoisted.stable.params.save = vi.fn(async (): Promise<boolean> => true);
  hoisted.stable.mission.state = 'idle';
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

describe('a bench routine and its countdown', () => {
  it('has the station count the whole countdown from the click, the mission spawned at its end', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    window.history.replaceState(null, '', '/?screen=cockpit');
    await mount();
    const before = Date.now();
    await act(async () => {
      await call('cockpit', 'onRunStage', 3);
    });
    const options = hoisted.startBenchStage.mock.calls[0][0];
    expect(options.countdown).toBe(10);
    expect(Object.keys(options)).not.toContain('launchAtMs');
    expect(hoisted.props.countdown?.seconds).toBe(10);
    expect(Number(hoisted.props.countdown?.launchAt)).toBeGreaterThanOrEqual(before);
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

describe('the parameter document as the only source of a launch', () => {
  const sent = (options: Record<string, unknown> | undefined) => JSON.parse(String(options?.paramsJson));

  it('sends no per-field option and the countdown literally, zero included', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const options = hoisted.launch.mock.calls[0][0];
    expect(Object.keys(options).sort()).toEqual(['countdown', 'launchAtMs', 'noFly', 'paramsJson']);
    expect(options.countdown).toBe(0);
    expect(sent(options).kinematics.countdown_sec).toBe(0);
  });

  it('blocks a launch whose document lacks a required number, naming it', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, rtl: { ...hoisted.doc.rtl, max_speed: null } };
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(String(hoisted.props.preflight.launchError)).toContain('rtl.max_speed');
  });

  it('blocks a launch while the parameters are not ready', async () => {
    hoisted.stable.params.status = 'loading';
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(hoisted.props.preflight.launchError).toBeTruthy();
  });

  it('aborts the launch when saving the edits fails', async () => {
    hoisted.stable.params.dirty = true;
    hoisted.stable.params.save = vi.fn(async (): Promise<boolean> => false);
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.stable.params.save).toHaveBeenCalledTimes(1);
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(String(hoisted.props.preflight.launchError)).toMatch(/salvar/i);
  });

  it('saves the edits before a bench routine and stops if that fails', async () => {
    hoisted.stable.params.dirty = true;
    hoisted.stable.params.save = vi.fn(async (): Promise<boolean> => false);
    window.history.replaceState(null, '', '/?screen=cockpit');
    await mount();
    await act(async () => {
      await call('cockpit', 'onRunStage', 2);
    });
    expect(hoisted.stable.params.save).toHaveBeenCalledTimes(1);
    expect(hoisted.startBenchStage).not.toHaveBeenCalled();
  });

  it('flies the edited values on the second mission of a session', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const edited = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, target_altitude_m: 2.4 } };
    hoisted.stable.params.working = edited;
    hoisted.stable.params.committed = edited;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(2);
    expect(sent(hoisted.launch.mock.calls[0][0]).kinematics.target_altitude_m).toBe(1.6);
    expect(sent(hoisted.launch.mock.calls[1][0]).kinematics.target_altitude_m).toBe(2.4);
  });
});

describe('the countdown starts at the click', () => {
  it('opens before the mission process answers, counting from the click instant', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    let resolveLaunch: (value: { success: boolean }) => void = () => undefined;
    hoisted.launch.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveLaunch = resolve;
      })
    );
    await mount();
    const before = Date.now();
    let pending: Promise<unknown> | undefined;
    await act(async () => {
      pending = call('preflight', 'onLaunch') as Promise<unknown>;
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(hoisted.props.countdown, 'the overlay waited for the process').toBeDefined();
    const launchAt = Number(hoisted.props.countdown.launchAt);
    expect(launchAt).toBeGreaterThanOrEqual(before);
    expect(hoisted.launch.mock.calls[0][0].launchAtMs).toBe(launchAt);
    await act(async () => {
      resolveLaunch({ success: true });
      await pending;
    });
  });

  it('ignores the dial while its own launch is counting down', async () => {
    // Section 1.5 of docs/PROMPT_FIX_LANCAMENTO_CONTAGEM.md: the dial keeps the
    // keyboard focus under the overlay, and a second activation reached the
    // runtime's "already running" refusal, which closed the overlay of the
    // countdown still in progress.
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    hoisted.stable.mission.state = 'running';
    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    delete hoisted.props.countdown;
    await act(async () => {
      root.render(<App />);
    });
    expect(hoisted.props.countdown, 'the countdown in progress was closed').toBeDefined();
    expect(hoisted.props.preflight.launchError ?? null).toBeNull();
  });

  it('closes the overlay when the launch fails', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    hoisted.launch.mockImplementationOnce(async () => ({ success: false, error: 'spawn failed' }) as never);
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    delete hoisted.props.countdown;
    await act(async () => {
      root.render(<App />);
    });
    expect(hoisted.props.countdown).toBeUndefined();
    expect(String(hoisted.props.preflight.launchError)).toContain('spawn failed');
  });
});

