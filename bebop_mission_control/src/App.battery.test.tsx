// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MilestoneEvent } from './types/bmg';
import { RTL_ACK_TIMEOUT_MS, RTL_STAGE } from './lib/batteryFailsafe';
import { BATTERY_LANDING_CALL } from './lib/batteryReturn';
import { getPath } from './lib/paths';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

interface TelemetryStub {
  connected: boolean;
  battery_known: boolean;
  battery_pct: number;
  flying_state: number | null;
}

/**
 * The critical-battery watcher lives in `App`. The hooks and screens are stood
 * in for, as in `App.launchParams.test.tsx`, so the test drives the real
 * watcher with telemetry and the mission's reported stage and reads what it
 * asks of the host.
 */
const hoisted = vi.hoisted(() => {
  const doc = {
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
      enabled: false,
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
    battery: { land_pct: 20 },
  };
  const props: Record<string, Record<string, unknown>> = {};
  const capture = (name: string) => (next: Record<string, unknown>) => {
    props[name] = next;
    return null;
  };
  const order: string[] = [];
  const milestoneListeners = new Set<(event: MilestoneEvent) => void>();
  const gotoStage = vi.fn(async (stage: number) => {
    order.push(`goto:${stage}`);
    return { success: true, stage };
  });
  const abortMission = vi.fn(async () => {
    order.push('land');
  });
  const setBatteryFailsafe = vi.fn(async (_enabled: boolean, _thresholdPct: number) => ({ success: true }));
  const missionAbort = vi.fn(async () => {
    order.push('land');
  });
  const launch = vi.fn(async (_options: Record<string, unknown>) => ({ success: true }));
  const preempt = vi.fn((label: string, _compose: () => string, _priority?: string) => {
    order.push(`say:${label}`);
  });
  const telemetry: TelemetryStub = { connected: false, battery_known: false, battery_pct: 0, flying_state: null };
  const stable = {
    bridge: {
      startBenchStage: async () => ({ success: true }),
      endMission: async () => undefined,
      gotoStage,
      abortMission,
      setBatteryFailsafe,
      onMissionReset: () => () => undefined,
      onMilestone: (cb: (event: MilestoneEvent) => void) => {
        milestoneListeners.add(cb);
        return () => {
          milestoneListeners.delete(cb);
        };
      },
    },
    telemetry: {
      telemetry,
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
      abort: missionAbort,
      reset: () => undefined,
      beginBenchStage: () => undefined,
      refuseBenchStage: () => undefined,
    },
    params: {
      working: doc as Record<string, unknown>,
      committed: doc as Record<string, unknown>,
      status: 'ready' as string,
      dirty: false,
      save: async (): Promise<boolean> => true,
    },
    stream: { fps: 0 },
    camera: { tilt: -20, available: false, set: () => undefined, reset: () => undefined },
    copilot: { say: async () => undefined, cancel: () => undefined, prepare: () => undefined },
    narration: { enqueue: vi.fn(), preempt, silence: () => undefined },
    forensic: { revealed: false, closing: false },
    voice: { level: 1, setVolume: () => undefined, toggleMute: () => undefined },
  };
  return { doc, props, capture, order, milestoneListeners, stable };
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
  useMissionCountdown: () => ({ remaining: null, clearance: false }),
}));

import { App } from './App';

let host: HTMLDivElement;
let root: Root;

const HOVERING = 2;

async function render(): Promise<void> {
  await act(async () => {
    root.render(<App />);
  });
}

function call(screen: string, prop: string, ...args: unknown[]): unknown {
  const fn = hoisted.props[screen]?.[prop];
  if (typeof fn !== 'function') throw new Error(`${screen}.${prop} was not rendered`);
  return (fn as (...a: unknown[]) => unknown)(...args);
}

/** Launch the plan without the return (`rtl.enabled` false) and enter the flight at `stage`. */
async function launchAndFly(stage: number): Promise<void> {
  await render();
  await act(async () => {
    await call('preflight', 'onLaunch');
  });
  expect(hoisted.stable.mission.launch).toHaveBeenCalledTimes(1);
  expect(getPath(JSON.parse(String(hoisted.stable.mission.launch.mock.calls[0][0].paramsJson)), 'rtl.enabled')).toBe(
    false
  );
  hoisted.stable.mission.state = 'running';
  hoisted.stable.mission.stage = stage;
  setTelemetry({ connected: true, battery_known: true, battery_pct: 60, flying_state: HOVERING });
  await render();
}

function setTelemetry(next: TelemetryStub): void {
  hoisted.stable.telemetry.telemetry = next;
}

/** A fresh reading at or under the default 20% threshold. */
async function crossThreshold(): Promise<void> {
  setTelemetry({ ...hoisted.stable.telemetry.telemetry, battery_pct: 18 });
  await render();
}

async function reportStage(stage: number): Promise<void> {
  hoisted.stable.mission.stage = stage;
  await render();
}

async function emitMilestone(key: string, payload: Record<string, unknown>): Promise<void> {
  await act(async () => {
    for (const listener of hoisted.milestoneListeners) {
      listener({ kind: 'milestone', key, payload, at: Date.now(), source: 'mission' });
    }
  });
}

async function elapse(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function landings(): number {
  return hoisted.stable.bridge.abortMission.mock.calls.length + hoisted.stable.mission.abort.mock.calls.length;
}

function stageRequests(): number[] {
  return hoisted.stable.bridge.gotoStage.mock.calls.map(([stage]) => stage);
}

const BATTERY_RETURN_PAYLOAD = { battery_pct: 18, threshold_pct: 20, from_stage: 4, mode: 'advanced' };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  hoisted.order.length = 0;
  hoisted.milestoneListeners.clear();
  hoisted.stable.bridge.gotoStage.mockClear();
  hoisted.stable.bridge.abortMission.mockClear();
  hoisted.stable.bridge.setBatteryFailsafe.mockClear();
  hoisted.stable.mission.abort.mockClear();
  hoisted.stable.mission.launch.mockClear();
  hoisted.stable.narration.preempt.mockClear();
  hoisted.stable.narration.enqueue.mockClear();
  hoisted.stable.mission.state = 'idle';
  hoisted.stable.mission.stage = 0;
  hoisted.stable.mission.refusal = null;
  setTelemetry({ connected: false, battery_known: false, battery_pct: 0, flying_state: null });
  for (const key of Object.keys(hoisted.props)) delete hoisted.props[key];
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe.each([1, 3])('critical battery in flight at stage %i with rtl.enabled false', (stage) => {
  it('asks the mission for stage 5 and neither lands nor speaks inside the window', async () => {
    await launchAndFly(stage);
    await crossThreshold();

    expect(stageRequests()).toEqual([RTL_STAGE]);
    await elapse(RTL_ACK_TIMEOUT_MS - 1);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it('without an acknowledgement asks a second time before any landing, and lands only after the second window', async () => {
    await launchAndFly(stage);
    await crossThreshold();

    await elapse(RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE, RTL_STAGE]);
    expect(landings()).toBe(0);

    await elapse(RTL_ACK_TIMEOUT_MS - 1);
    expect(landings()).toBe(0);

    await elapse(1);
    expect(landings()).toBe(1);
    expect(hoisted.stable.narration.preempt).toHaveBeenCalledTimes(1);
    expect(hoisted.stable.narration.preempt.mock.calls[0][1]()).toBe(BATTERY_LANDING_CALL);
    expect(hoisted.order).toEqual([`goto:${RTL_STAGE}`, `goto:${RTL_STAGE}`, 'say:battery.critical', 'land']);
  });

  it('with stage 5 reported never lands, never asks again and says nothing', async () => {
    await launchAndFly(stage);
    await crossThreshold();
    await reportStage(RTL_STAGE);

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it('with stage 5 reported only inside the second window still never lands', async () => {
    await launchAndFly(stage);
    await crossThreshold();
    await elapse(RTL_ACK_TIMEOUT_MS);
    await reportStage(RTL_STAGE);

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE, RTL_STAGE]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });
});

describe('the mission.battery_return milestone acknowledges the return', () => {
  it('holds the landing while the jump waits behind a blocking stage-4 capture', async () => {
    await launchAndFly(4);
    await crossThreshold();
    await emitMilestone('mission.battery_return', BATTERY_RETURN_PAYLOAD);

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it('received before the station crosses the threshold, leaves the return to the mission', async () => {
    await launchAndFly(4);
    await emitMilestone('mission.battery_return', BATTERY_RETURN_PAYLOAD);
    await crossThreshold();

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it('an unrelated milestone is not an acknowledgement', async () => {
    await launchAndFly(4);
    await crossThreshold();
    await emitMilestone('mission.capture_done', {});

    await elapse(2 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE, RTL_STAGE]);
    expect(landings()).toBe(1);
  });
});

describe('a single voice with both authorities firing', () => {
  it('the mission already at stage 5 when the station crosses: no request, no speech, no landing', async () => {
    await launchAndFly(3);
    await emitMilestone('mission.battery_return', { ...BATTERY_RETURN_PAYLOAD, from_stage: 3 });
    await reportStage(RTL_STAGE);
    await crossThreshold();

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
    expect(hoisted.stable.narration.enqueue.mock.calls.map(([label]) => label)).not.toContain('battery.critical');
  });
});

describe('the emergency landing stays the last resort', () => {
  it('lands at the end of the first window when the mission stopped running', async () => {
    await launchAndFly(3);
    await crossThreshold();
    hoisted.stable.mission.state = 'faulted';
    await render();

    await elapse(RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([RTL_STAGE]);
    expect(landings()).toBe(1);
    expect(hoisted.stable.narration.preempt.mock.calls[0][1]()).toBe(BATTERY_LANDING_CALL);
  });

  it('lands at once, with the emergency call, an airframe flown without a mission', async () => {
    await render();
    setTelemetry({ connected: true, battery_known: true, battery_pct: 60, flying_state: HOVERING });
    await render();
    await crossThreshold();

    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(1);
    expect(hoisted.stable.narration.preempt).toHaveBeenCalledTimes(1);
    expect(hoisted.stable.narration.preempt.mock.calls[0][1]()).toBe(BATTERY_LANDING_CALL);
  });
});

const LANDED = 0;

describe('on the ground the station does not act', () => {
  it('a reading under the threshold during the countdown asks nothing, lands nothing and says nothing', async () => {
    await launchAndFly(0);
    hoisted.stable.mission.state = 'arming';
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: LANDED });
    await render();
    await crossThreshold();

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it('stays armed on the ground and asks for the return once the aircraft is airborne', async () => {
    await launchAndFly(1);
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: LANDED });
    await render();
    await crossThreshold();
    expect(stageRequests()).toEqual([]);

    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: HOVERING });
    await render();
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, battery_pct: 17 });
    await render();

    expect(stageRequests()).toEqual([RTL_STAGE]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });
});

describe('a request the host cannot deliver while the mission runs', () => {
  it('gets one window before the emergency landing', async () => {
    hoisted.stable.bridge.gotoStage.mockImplementationOnce(async () => ({ success: false, stage: RTL_STAGE }));
    await launchAndFly(3);
    await crossThreshold();

    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
    await elapse(RTL_ACK_TIMEOUT_MS - 1);
    expect(landings()).toBe(0);

    await elapse(1);
    expect(landings()).toBe(1);
    expect(hoisted.stable.narration.preempt).toHaveBeenCalledTimes(1);
    expect(hoisted.stable.narration.preempt.mock.calls[0][1]()).toBe(BATTERY_LANDING_CALL);
  });

  it('is not landed when the mission acknowledges the return inside that window', async () => {
    hoisted.stable.bridge.gotoStage.mockImplementationOnce(async () => ({ success: false, stage: RTL_STAGE }));
    await launchAndFly(3);
    await crossThreshold();
    await emitMilestone('mission.battery_return', { ...BATTERY_RETURN_PAYLOAD, from_stage: 3 });

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });
});

describe('the acknowledgement belongs to one flight', () => {
  it('a battery_return of the previous flight does not hold back the next request', async () => {
    await launchAndFly(4);
    await emitMilestone('mission.battery_return', BATTERY_RETURN_PAYLOAD);
    hoisted.stable.mission.state = 'finished';
    setTelemetry({ connected: true, battery_known: true, battery_pct: 60, flying_state: LANDED });
    await render();
    hoisted.stable.mission.launch.mockClear();

    await launchAndFly(3);
    await crossThreshold();
    expect(stageRequests()).toEqual([RTL_STAGE]);
  });
});

describe('a mission that dies after acknowledging', () => {
  it('is landed at the window end while the aircraft is airborne', async () => {
    await launchAndFly(4);
    await crossThreshold();
    await emitMilestone('mission.battery_return', BATTERY_RETURN_PAYLOAD);
    hoisted.stable.mission.state = 'faulted';
    await render();

    await elapse(RTL_ACK_TIMEOUT_MS);
    expect(landings()).toBe(1);
    expect(hoisted.stable.narration.preempt.mock.calls[0][1]()).toBe(BATTERY_LANDING_CALL);
  });

  it('is left alone, without the emergency call, once the aircraft is down', async () => {
    await launchAndFly(4);
    await crossThreshold();
    await emitMilestone('mission.battery_return', BATTERY_RETURN_PAYLOAD);
    hoisted.stable.mission.state = 'finished';
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: LANDED });
    await render();

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });
});

describe('the station trigger flying states', () => {
  it.each([
    [7, 'motor_ramping'],
    [1, 'takingoff'],
  ])('flying_state %i (%s) with a reading under the threshold asks nothing', async (flyingState) => {
    await launchAndFly(1);
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: flyingState });
    await render();
    await crossThreshold();

    await elapse(4 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(0);
    expect(hoisted.stable.narration.preempt).not.toHaveBeenCalled();
  });

  it.each([
    [2, 'hovering'],
    [3, 'flying'],
  ])('flying_state %i (%s) with a reading under the threshold asks once', async (flyingState) => {
    await launchAndFly(2);
    setTelemetry({ ...hoisted.stable.telemetry.telemetry, flying_state: flyingState });
    await render();
    await crossThreshold();
    await render();

    expect(stageRequests()).toEqual([RTL_STAGE]);
    expect(landings()).toBe(0);
  });
});

describe('the battery failsafe switch', () => {
  const FAILSAFE_KEY = 'bmg.battery-failsafe.v1';

  it('switched off before the launch, the payload carries battery.enabled false', async () => {
    window.localStorage.setItem(FAILSAFE_KEY, JSON.stringify({ enabled: false, thresholdPct: 20 }));
    await render();
    await act(async () => {
      await call('preflight', 'onLaunch');
    });
    const sent = JSON.parse(String(hoisted.stable.mission.launch.mock.calls[0][0].paramsJson));
    expect(getPath(sent, 'battery.enabled')).toBe(false);
    expect(getPath(sent, 'battery.land_pct')).toBe(20);
  });

  it('switched off, a charge under the threshold in flight asks nothing', async () => {
    window.localStorage.setItem(FAILSAFE_KEY, JSON.stringify({ enabled: false, thresholdPct: 20 }));
    await launchAndFly(3);
    await crossThreshold();
    await elapse(2 * RTL_ACK_TIMEOUT_MS);
    expect(stageRequests()).toEqual([]);
    expect(landings()).toBe(0);
  });

  it('switched on, asks for the return exactly once', async () => {
    await launchAndFly(3);
    await crossThreshold();
    await crossThreshold();
    expect(stageRequests()).toEqual([RTL_STAGE]);
  });

  it('switching it off in flight is forwarded to the mission and cancels no return', async () => {
    await launchAndFly(3);
    await crossThreshold();
    expect(stageRequests()).toEqual([RTL_STAGE]);
    await act(async () => {
      await call('topbar', 'onFailsafeChange', { enabled: false, thresholdPct: 20 });
    });
    expect(hoisted.stable.bridge.setBatteryFailsafe).toHaveBeenCalledWith(false, 20);
    expect(landings()).toBe(0);
    expect(stageRequests()).toEqual([RTL_STAGE]);
  });
});
