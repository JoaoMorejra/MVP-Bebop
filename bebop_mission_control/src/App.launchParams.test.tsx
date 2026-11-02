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
      max_horizontal_speed: 0.3,
      takeoff_stabilize_duration_sec: 2,
      hover_duration_sec: 7,
      countdown_sec: 0,
    },
    gimbal: { search_tilt_deg: -20, nadir_tilt_deg: -69 },
    timeouts: { search_timeout_sec: 30 },
    rtl: {
      timeout_sec: 60,
      max_speed: 0.1,
      arrival_radius_m: 0.2,
      reverse_cruise_velocity: -0.2,
      target_aruco_id: 8,
      marker_dict: 'DICT_APRILTAG_36h11',
      tag_size: 0.2,
      centering_timeout_sec: 25,
    },
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
      telemetry: { connected: false, battery_known: false, battery_pct: 0 } as Record<string, any>,
      track: [],
      stale: false,
      resetTrack: () => undefined,
    },
    link: { networks: [], flightReady: true, driverRunning: true },
    mission: {
      state: 'idle',
      stage: 0,
      ran: false,
      refusal: null as { field: string; reason: string } | null,
      launch,
      abort: async () => undefined,
      reset: () => undefined,
      beginBenchStage: () => undefined,
      refuseBenchStage: () => undefined,
    },
    params: {
      working: doc as Record<string, any>,
      committed: doc as Record<string, any>,
      status: 'ready' as string,
      dirty: false,
      save: vi.fn(async (): Promise<boolean> => true),
      edit: vi.fn(),
      switchMode: vi.fn(async (_mode: string): Promise<boolean> => true),
    },
    stream: { fps: 0 },
    camera: { tilt: -20, available: false, set: () => undefined, reset: () => undefined },
    copilot: { say: async () => undefined, cancel: () => undefined, prepare: () => undefined },
    narration: { enqueue: vi.fn(), preempt: vi.fn() },
    forensic: { revealed: false, closing: false },
    voice: { level: 1, setVolume: () => undefined, toggleMute: () => undefined },
  };
  const countdown: { remaining: number | null; clearance: boolean; trim: 'acked' | 'skipped' | null } = {
    remaining: null,
    clearance: false,
    trim: null,
  };
  return { doc, props, capture, launch, startBenchStage, stable, countdown };
});

vi.mock('./components/preflight/PreflightScreen', () => ({ PreflightScreen: hoisted.capture('preflight') }));
vi.mock('./components/cockpit/CockpitScreen', () => ({ CockpitScreen: hoisted.capture('cockpit') }));
vi.mock('./components/shell/TopBar', () => ({ TopBar: hoisted.capture('topbar') }));

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
  useLinkLossNarration: () => undefined,
  useNarrationQueue: () => hoisted.stable.narration,
}));
vi.mock('./hooks/useVoiceLevel', () => ({ useVoiceLevel: () => hoisted.stable.voice }));
vi.mock('./hooks/useInspectionAssessment', () => ({ useInspectionAssessment: () => null }));
vi.mock('./hooks/useMissionCountdown', () => ({
  useMissionCountdown: () => hoisted.countdown,
}));

import { App } from './App';

let host: HTMLDivElement;
let root: Root;

async function mount(): Promise<void> {
  await act(async () => {
    root.render(<App />);
  });
}

/** The launch sequence is on: the top bar is frozen while it runs (D6). */
const counting = () => hoisted.props.topbar?.frozen === true;

function call(screen: string, prop: string, ...args: unknown[]): unknown {
  const fn = hoisted.props[screen]?.[prop];
  if (typeof fn !== 'function') throw new Error(`${screen}.${prop} was not rendered`);
  return (fn as (...a: unknown[]) => unknown)(...args);
}

async function setThreshold(thresholdPct: number): Promise<void> {
  const next: BatteryFailsafe = { enabled: true, thresholdPct };
  await act(async () => {
    call('topbar', 'onFailsafeChange', next);
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
  hoisted.stable.params.edit = vi.fn();
  hoisted.stable.params.switchMode = vi.fn(async (_mode: string): Promise<boolean> => true);
  hoisted.stable.mission.state = 'idle';
  hoisted.stable.mission.refusal = null;
  hoisted.stable.mission.abort = async () => undefined;
  hoisted.countdown.remaining = null;
  hoisted.countdown.clearance = false;
  hoisted.countdown.trim = null;
  hoisted.stable.mission.beginBenchStage = () => undefined;
  hoisted.stable.mission.refuseBenchStage = () => undefined;
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
      call('topbar', 'onNavigate', 'cockpit');
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
    expect(counting()).toBe(true);
    expect(before).toBeLessThanOrEqual(Date.now());
  });
});

describe('the flight plan at the click', () => {
  it('speaks the plan of the document sent, without the return', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, rtl: { ...hoisted.doc.rtl, enabled: false } };
    const enqueue = hoisted.stable.narration.enqueue as ReturnType<typeof vi.fn>;
    enqueue.mockClear();
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const call0 = enqueue.mock.calls.find(([label]) => label === 'plan');
    expect(call0).toBeDefined();
    const line = (call0![1] as () => string)();
    expect(line).toMatch(/^Plano de voo: /);
    expect(line).not.toMatch(/retorno|base|marcador/i);
  });
});

describe('a launch document the mission refuses (exit 78)', () => {
  it('cancels the countdown, stays on pre-flight and shows the reason and the field', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(counting()).toBe(true);
    expect(hoisted.props.preflight?.sequence).not.toBeNull();

    delete hoisted.props.cockpit;
    hoisted.stable.mission.refusal = {
      field: 'kinematics.target_altitude_m',
      reason: '0.50 m abaixo do mínimo de 1.30 m sobre o totem de 1.00 m',
    };
    await mount();
    await mount();

    expect(counting()).toBe(false);
    expect(hoisted.props.cockpit).toBeUndefined();
    expect(String(hoisted.props.preflight?.launchError)).toContain('Lançamento recusado: 0.50 m abaixo do mínimo');
    expect(hoisted.props.preflight?.refusedPath).toBe('kinematics.target_altitude_m');
  });
});

describe('launchParamsJson', () => {
  it('writes the threshold under battery.land_pct and keeps every other field', () => {
    const doc = { rtl: { timeout_sec: 60 }, battery: { other: 1 } };
    const sent = JSON.parse(launchParamsJson(doc, { enabled: true, thresholdPct: 15 }));

    expect(sent).toEqual({ rtl: { timeout_sec: 60 }, battery: { other: 1, land_pct: 15, enabled: true }, kinematics: { countdown_sec: 10 } });
    expect(doc.battery).toEqual({ other: 1 });
  });

  it('builds a document when there is none', () => {
    expect(JSON.parse(launchParamsJson(null, { enabled: true, thresholdPct: 20 }))).toEqual({ battery: { land_pct: 20, enabled: true }, kinematics: { countdown_sec: 10 } });
  });
});

describe('the parameter document as the only source of a launch', () => {
  const sent = (options: Record<string, unknown> | undefined) => JSON.parse(String(options?.paramsJson));

  it('sends no per-field option and the countdown fixed at 10 s, whatever the stored document says', async () => {
    const stored = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 3 } };
    hoisted.stable.params.working = stored;
    hoisted.stable.params.committed = stored;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const options = hoisted.launch.mock.calls[0][0];
    expect(Object.keys(options).sort()).toEqual(['countdown', 'launchAtMs', 'noFly', 'paramsJson']);
    expect(options.countdown).toBe(10);
    expect(sent(options).kinematics.countdown_sec).toBe(10);
    expect(counting()).toBe(true);
  });

  it('couples a document stored with an independent return cruise and saves it before launching', async () => {
    const stored = { ...hoisted.doc, rtl: { ...hoisted.doc.rtl, reverse_cruise_velocity: -0.1 } };
    hoisted.stable.params.working = stored;
    hoisted.stable.params.committed = stored;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.stable.params.save).toHaveBeenCalledTimes(1);
    expect(sent(hoisted.launch.mock.calls[0][0]).rtl.reverse_cruise_velocity).toBe(-0.2);
  });

  it('carries the advanced pad marker fields as edited', async () => {
    const edited = {
      ...hoisted.doc,
      rtl: { ...hoisted.doc.rtl, target_aruco_id: 12, marker_dict: 'DICT_4X4_50', tag_size: 0.16, centering_timeout_sec: 40 },
    };
    expect(JSON.parse(launchParamsJson(edited, { enabled: true, thresholdPct: 25 })).rtl).toMatchObject({
      target_aruco_id: 12,
      marker_dict: 'DICT_4X4_50',
      tag_size: 0.16,
    });
    hoisted.stable.params.working = edited;
    hoisted.stable.params.committed = edited;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(sent(hoisted.launch.mock.calls[0][0]).rtl).toMatchObject({
      target_aruco_id: 12,
      marker_dict: 'DICT_4X4_50',
      tag_size: 0.16,
      centering_timeout_sec: 40,
    });
  });

  it('blocks an inexecutable combination before saving it, naming the conflicts', async () => {
    const doc = {
      ...hoisted.doc,
      platform: { height_m: 1, venue_ceiling_agl_m: 1.9 },
      kinematics: { ...hoisted.doc.kinematics, target_altitude_m: 1.85, altitude_ceiling_margin_m: 0 },
    };
    hoisted.stable.params.working = doc;
    hoisted.stable.params.dirty = true;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(hoisted.stable.params.save).not.toHaveBeenCalled();
    const message = String(hoisted.props.preflight.launchError);
    expect(message).toContain('Cota colada no teto do local');
    // E04 reads kinematics.climb_deadband_m, which this document does not hold: skipped, not invented.
    expect(message).not.toContain('Margem do teto menor que a banda de chegada');
  });

  it('launches a combination that only warns', async () => {
    const doc = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 4 } };
    hoisted.stable.params.working = doc;
    hoisted.stable.params.committed = doc;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
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
    const edited = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, target_altitude_m: 1.9 } };
    hoisted.stable.params.working = edited;
    hoisted.stable.params.committed = edited;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(2);
    expect(sent(hoisted.launch.mock.calls[0][0]).kinematics.target_altitude_m).toBe(1.6);
    expect(sent(hoisted.launch.mock.calls[1][0]).kinematics.target_altitude_m).toBe(1.9);
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
    expect(counting(), 'the dial waited for the process').toBe(true);
    expect(hoisted.props.preflight.sequence).not.toBeNull();
    expect(Number(hoisted.launch.mock.calls[0][0].launchAtMs)).toBeGreaterThanOrEqual(before);
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
    await act(async () => {
      root.render(<App />);
    });
    expect(counting(), 'the countdown in progress was closed').toBe(true);
    expect(hoisted.props.preflight.launchError ?? null).toBeNull();
  });

  it('splits the dock back when the launch fails', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    hoisted.launch.mockImplementationOnce(async () => ({ success: false, error: 'spawn failed' }) as never);
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    await act(async () => {
      root.render(<App />);
    });
    expect(counting()).toBe(false);
    expect(String(hoisted.props.preflight.launchError)).toContain('spawn failed');
  });
});


describe('the mission mode in the launch payload', () => {
  const simple = () => ({
    ...hoisted.doc,
    kinematics: { ...hoisted.doc.kinematics, target_altitude_m: 0.9 },
    mission_mode: 'simple',
    yaw_pattern: { scan_vyaw: 0.15 },
  });

  it('sends the mission mode with the launch document', async () => {
    hoisted.stable.params.working = simple();
    hoisted.stable.params.committed = simple();
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    const received = hoisted.launch.mock.calls[0][0];
    expect(JSON.parse(String(received.paramsJson)).mission_mode).toBe('simple');
  });

  it('sends the mission mode with a bench routine', async () => {
    hoisted.stable.params.working = simple();
    hoisted.stable.params.committed = simple();
    window.history.replaceState(null, '', '/?screen=cockpit');
    await mount();
    await act(async () => {
      await call('cockpit', 'onRunStage', 2);
    });
    const received = hoisted.startBenchStage.mock.calls[0][0];
    expect(JSON.parse(String(received.paramsJson)).mission_mode).toBe('simple');
  });
});

describe('the mission mode selector lock', () => {
  const flush = async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  };

  it('locks from the click on the dial, and sends the document of the click', async () => {
    const clicked = { ...hoisted.doc, mission_mode: 'advanced', rtl: { ...hoisted.doc.rtl, enabled: false } };
    hoisted.stable.params.working = clicked;
    hoisted.stable.params.committed = hoisted.doc;
    hoisted.stable.params.dirty = true;
    let resolveSave: (saved: boolean) => void = () => undefined;
    hoisted.stable.params.save = vi.fn(
      () => new Promise<boolean>((resolve) => {
        resolveSave = resolve;
      })
    );
    let resolveLaunch: (value: { success: boolean; error?: string }) => void = () => undefined;
    hoisted.launch.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveLaunch = resolve;
      })
    );
    await mount();
    expect(hoisted.props.preflight.modeLocked).toBe(false);

    let pending: Promise<unknown> | undefined;
    await act(async () => {
      pending = call('preflight', 'onLaunch') as Promise<unknown>;
      await flush();
    });
    expect(hoisted.props.preflight.modeLocked, 'unlocked while the document was being saved').toBe(true);

    // An edit reaching the store after the click must not reach the flight.
    hoisted.stable.params.working = { ...clicked, mission_mode: 'simple', yaw_pattern: { scan_vyaw: 0.15 } };
    await act(async () => {
      root.render(<App />);
    });
    await act(async () => {
      resolveSave(true);
      await flush();
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    expect(hoisted.props.preflight.modeLocked, 'unlocked while the process was being spawned').toBe(true);
    const sentDoc = JSON.parse(String(hoisted.launch.mock.calls[0][0].paramsJson));
    expect(sentDoc.mission_mode).toBe('advanced');
    expect(sentDoc.rtl.enabled).toBe(false);

    await act(async () => {
      resolveLaunch({ success: false, error: 'spawn failed' });
      await pending;
    });
    expect(hoisted.props.preflight.modeLocked, 'still locked after the launch was refused').toBe(false);
  });

  it('unlocks when the launch is blocked before the spawn', async () => {
    hoisted.stable.params.status = 'loading';
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(hoisted.props.preflight.modeLocked).toBe(false);
  });

  it('unlocks when priming the launch phrases throws, and launches on the next click', async () => {
    const original = hoisted.stable.copilot.prepare;
    let failing = false;
    hoisted.stable.copilot.prepare = () => {
      if (failing) throw new Error('speech engine gone');
    };
    // The dial drops the launch promise (`void launch()`), so the throw
    // surfaces as an unhandled rejection; it is the expected outcome here.
    const runnerListeners = process.listeners('unhandledRejection');
    const rejected: unknown[] = [];
    process.removeAllListeners('unhandledRejection');
    process.on('unhandledRejection', (reason) => rejected.push(reason));
    try {
      await mount();
      failing = true;
      await act(async () => {
        call('preflight', 'onLaunch');
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(rejected.map((r) => (r as Error).message)).toEqual(['speech engine gone']);
      expect(hoisted.launch).not.toHaveBeenCalled();
      expect(hoisted.props.preflight.modeLocked, 'left locked by a throw before the launch').toBe(false);

      failing = false;
      await act(async () => {
        await call('preflight', 'onLaunch');
      });
      expect(hoisted.launch).toHaveBeenCalledTimes(1);
    } finally {
      hoisted.stable.copilot.prepare = original;
      process.removeAllListeners('unhandledRejection');
      for (const listener of runnerListeners) process.on('unhandledRejection', listener);
    }
  });

  it('stays locked through the countdown and unlocks when it is cancelled', async () => {
    hoisted.stable.params.working = { ...hoisted.doc, kinematics: { ...hoisted.doc.kinematics, countdown_sec: 10 } };
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(counting()).toBe(true);
    expect(hoisted.props.preflight.modeLocked).toBe(true);
    await act(async () => {
      call('preflight', 'onAbortLaunch');
      await flush();
    });
    expect(hoisted.props.preflight.modeLocked).toBe(false);
  });

  it('stays locked while the mission is arming or running, and unlocks once it is over', async () => {
    for (const [state, locked] of [
      ['arming', true],
      ['running', true],
      ['finished', false],
      ['aborted', false],
      ['idle', false],
    ] as const) {
      hoisted.stable.mission.state = state;
      await mount();
      expect(hoisted.props.preflight.modeLocked, state).toBe(locked);
    }
  });

  it('locks while a bench routine is being started', async () => {
    let resolveBench: (value: { success: boolean; error?: string }) => void = () => undefined;
    hoisted.startBenchStage.mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveBench = resolve;
      })
    );
    await mount();
    await act(async () => {
      call('topbar', 'onNavigate', 'cockpit');
    });
    let pending: Promise<unknown> | undefined;
    await act(async () => {
      pending = call('cockpit', 'onRunStage', 2) as Promise<unknown>;
      await flush();
    });
    await act(async () => {
      call('topbar', 'onNavigate', 'preflight');
    });
    expect(hoisted.startBenchStage).toHaveBeenCalledTimes(1);
    expect(hoisted.props.preflight.modeLocked).toBe(true);
    await act(async () => {
      resolveBench({ success: false, error: 'bench failed' });
      await pending;
    });
    expect(hoisted.props.preflight.modeLocked).toBe(false);
  });
});

describe('a refused bench routine', () => {
  /** The runtime's bench bookkeeping, as `useMissionRuntime` keeps it. */
  function statefulBench() {
    const mission = hoisted.stable.mission;
    mission.beginBenchStage = () => {
      mission.state = 'running';
    };
    const refuse = vi.fn((): undefined => {
      mission.state = 'faulted';
      return undefined;
    });
    mission.refuseBenchStage = refuse;
    return refuse;
  }

  async function runFromCockpitAndReturn(): Promise<void> {
    await mount();
    await act(async () => {
      call('topbar', 'onNavigate', 'cockpit');
    });
    await act(async () => {
      await call('cockpit', 'onRunStage', 2);
    });
    await act(async () => {
      call('topbar', 'onNavigate', 'preflight');
    });
  }

  it('releases the mode selector when the host refuses the routine', async () => {
    const refuse = statefulBench();
    hoisted.startBenchStage.mockImplementationOnce(async () => ({ success: false, error: 'bench failed' }));
    await runFromCockpitAndReturn();
    expect(refuse).toHaveBeenCalledTimes(1);
    expect(hoisted.props.preflight.modeLocked).toBe(false);
    expect(String(hoisted.props.preflight.launchError)).toContain('bench failed');
  });

  it('leaves a routine the host started running and locked', async () => {
    const refuse = statefulBench();
    await runFromCockpitAndReturn();
    expect(refuse).not.toHaveBeenCalled();
    expect(hoisted.stable.mission.state).toBe('running');
    expect(hoisted.props.preflight.modeLocked).toBe(true);
  });
});

describe('the flight mode selector', () => {
  /**
   * Stands in for the hook's edit and switch on the stable params object, in
   * call order, with the `no_fly` each switch would have persisted.
   */
  function recordModeCalls(start: Record<string, unknown>) {
    const log: Array<[string, unknown, unknown]> = [];
    hoisted.stable.params.working = { ...hoisted.doc, ...start };
    hoisted.stable.params.committed = hoisted.stable.params.working;
    hoisted.stable.params.edit = vi.fn((path: string, value: unknown) => {
      log.push(['edit', path, value]);
      hoisted.stable.params.working = { ...hoisted.stable.params.working, [path]: value };
    });
    hoisted.stable.params.switchMode = vi.fn(async (mode: string): Promise<boolean> => {
      log.push(['switch', mode, getPath(hoisted.stable.params.working, 'no_fly')]);
      hoisted.stable.params.working = { ...hoisted.stable.params.working, mission_mode: mode };
      return true;
    });
    return log;
  }

  async function select(mode: string): Promise<void> {
    await act(async () => {
      await call('preflight', 'onFlightModeChange', mode);
    });
  }

  it('selecting Teste sets no_fly true and keeps mission_mode', async () => {
    const log = recordModeCalls({ mission_mode: 'advanced', no_fly: false });
    await mount();
    expect(hoisted.props.preflight.flightMode).toBe('advanced');
    await select('test');
    expect(log).toEqual([['edit', 'no_fly', true]]);
    expect(hoisted.stable.params.switchMode).not.toHaveBeenCalled();
    expect(getPath(hoisted.stable.params.working, 'mission_mode')).toBe('advanced');
    expect(getPath(hoisted.stable.params.working, 'no_fly')).toBe(true);
  });

  it('selecting Simples from Teste switches mode before clearing no_fly', async () => {
    const log = recordModeCalls({ mission_mode: 'advanced', no_fly: true });
    await mount();
    expect(hoisted.props.preflight.flightMode).toBe('test');
    await select('simple');
    expect(log.map((entry) => entry[0])).toEqual(['switch', 'edit']);
    expect(log[0][1]).toBe('simple');
    expect(log[1]).toEqual(['edit', 'no_fly', false]);
    expect(getPath(hoisted.stable.params.working, 'no_fly')).toBe(false);
    expect(getPath(hoisted.stable.params.working, 'mission_mode')).toBe('simple');
  });

  it('selecting Avançado from Teste when mission_mode is already advanced only clears no_fly', async () => {
    const log = recordModeCalls({ mission_mode: 'advanced', no_fly: true });
    await mount();
    await select('advanced');
    expect(hoisted.stable.params.switchMode).not.toHaveBeenCalled();
    expect(log).toEqual([['edit', 'no_fly', false]]);
  });

  it('ignores a selection while the mode is locked', async () => {
    const log = recordModeCalls({ mission_mode: 'advanced', no_fly: false });
    hoisted.stable.mission.state = 'running';
    await mount();
    await select('test');
    expect(log).toEqual([]);
  });
});

describe('the launch sequence in pre-flight', () => {
  const press = async (init: KeyboardEventInit) => {
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }));
      await Promise.resolve();
    });
  };
  const rerender = async () => {
    await act(async () => {
      root.render(<App />);
    });
  };
  const pendingSave = () => {
    let release: (ok: boolean) => void = () => undefined;
    hoisted.stable.params.dirty = true;
    hoisted.stable.params.save = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          release = resolve;
        })
    );
    return (ok: boolean) => release(ok);
  };

  it('clicking Iniciar fuses the dock into Abortar Missão right away', async () => {
    const release = pendingSave();
    await mount();
    expect(hoisted.props.preflight.sequence).toBeNull();
    let pending: unknown;
    await act(async () => {
      pending = call('preflight', 'onLaunch');
      await Promise.resolve();
    });
    expect(hoisted.props.preflight.sequence).not.toBeNull();
    expect((hoisted.props.preflight.sequence as { stepId: string }).stepId).toBe('params');
    await act(async () => {
      release(true);
      await pending;
    });
  });

  it.each([
    ['Abortar Missão', null],
    ['Escape', { key: 'Escape' }],
    ['Ctrl+C', { key: 'c', code: 'KeyC', ctrlKey: true }],
  ] as const)('%s cancels the preparation and splits the dock back', async (_name, keyInit) => {
    const abort = vi.fn(async () => undefined);
    hoisted.stable.mission.abort = abort;
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.props.preflight.sequence).not.toBeNull();
    if (keyInit) await press(keyInit);
    else
      await act(async () => {
        await call('preflight', 'onAbortLaunch');
      });
    expect(abort).toHaveBeenCalledTimes(1);
    expect(hoisted.props.preflight.sequence).toBeNull();
    expect(counting()).toBe(false);
  });

  it('cancelling during the document commit prevents the spawn', async () => {
    const release = pendingSave();
    await mount();
    let pending: unknown;
    await act(async () => {
      pending = call('preflight', 'onLaunch');
      await Promise.resolve();
    });
    await press({ key: 'Escape' });
    await act(async () => {
      release(true);
      await pending;
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(counting()).toBe(false);
  });

  it('a double click launches one mission', async () => {
    await mount();
    await act(async () => {
      const first = call('preflight', 'onLaunch');
      const second = call('preflight', 'onLaunch');
      await Promise.all([first, second]);
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
  });

  it("after takeoff the last setCockpitAbortArmed call is true", async () => {
    const armed: boolean[] = [];
    const bridge = hoisted.stable.bridge as Record<string, unknown>;
    bridge.setCockpitAbortArmed = (value: boolean) => armed.push(value);
    bridge.onAbortShortcut = () => () => undefined;
    try {
      await mount();
      await act(async () => {
        await call('preflight', 'onLaunch');
      });
      expect(armed).toEqual([true]);
      // The mission reports its end of the countdown: the sequence hands over
      // to the cockpit, whose own abort publishes in the same commit.
      hoisted.stable.mission.state = 'faulted';
      await rerender();
      expect(hoisted.props.cockpit).toBeDefined();
      expect(armed[armed.length - 1]).toBe(true);
    } finally {
      delete bridge.setCockpitAbortArmed;
      delete bridge.onAbortShortcut;
    }
  });

  it('while the sequence is active the mode, parameters, tab switch, wifi, ros2 and battery controls are disabled and volume is not', async () => {
    await mount();
    expect(hoisted.props.topbar.frozen).toBe(false);
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.props.topbar.frozen).toBe(true);
    expect(hoisted.props.preflight.modeLocked).toBe(true);
    expect(hoisted.props.preflight.sequence).not.toBeNull();
    expect(typeof hoisted.props.topbar.onVolume).toBe('function');
  });

  it('a launch refused by the document shows the error in the dial and splits the dock', async () => {
    hoisted.stable.params.status = 'error';
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).not.toHaveBeenCalled();
    expect(hoisted.props.preflight.sequence).toBeNull();
    expect(String(hoisted.props.preflight.launchError)).toContain('Parâmetros indisponíveis');
    expect(hoisted.props.preflight.launchErrorShort).toBe('Falha ao iniciar');
  });

  it('the screen hands over to the cockpit when the sequence is done', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const now = Date.now();
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 11_000);
    try {
      hoisted.countdown.remaining = 0;
      hoisted.countdown.trim = 'acked';
      await rerender();
      await rerender();
      expect(hoisted.props.cockpit).toBeDefined();
      expect(counting()).toBe(false);
    } finally {
      clock.mockRestore();
    }
  });

  it("the 'Teste' mode runs the same sequence with the cyan dial", async () => {
    hoisted.stable.params.working = { ...hoisted.doc, no_fly: true };
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch.mock.calls[0][0].noFly).toBe(true);
    expect(hoisted.props.preflight.sequence).not.toBeNull();
  });

  it('aborting during countdown allows starting another mission immediately', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(1);
    await act(async () => {
      await call('preflight', 'onAbortLaunch');
    });
    expect(counting()).toBe(false);

    // Starting a new mission after aborting on the ground must succeed
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.launch).toHaveBeenCalledTimes(2);
  });

  it('airborne state immediately transitions to cockpit even during sequence', async () => {
    await mount();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    expect(hoisted.props.cockpit).toBeUndefined();

    // Aircraft reports airborne flying_state 1 (takingoff)
    hoisted.stable.telemetry.telemetry = {
      ...hoisted.stable.telemetry.telemetry,
      flying_state: 1,
    };
    await rerender();
    expect(hoisted.props.cockpit).toBeDefined();
    expect(counting()).toBe(false);
  });
});
