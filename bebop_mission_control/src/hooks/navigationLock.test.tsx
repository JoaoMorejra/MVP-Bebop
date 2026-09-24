// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { TabSwitch } from '../components/shell/TabSwitch';
import { preflightLocked } from '../lib/navigationLock';
import type { MissionState } from '../types/mission';
import type { MissionExitEvent, MissionStepEvent } from '../types/bmg';
import { useMissionRuntime } from './useMissionRuntime';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Listener<T> = (event: T) => void;

/** The part of the Electron bridge `useMissionRuntime` touches, driven by the test. */
function fakeBridge() {
  const steps: Listener<MissionStepEvent>[] = [];
  const exits: Listener<MissionExitEvent>[] = [];
  const launches: Array<(result: { success: boolean; message?: string }) => void> = [];
  const aborts: Array<() => void> = [];
  let processUp = false;
  const subscribe = <T,>(list: Listener<T>[]) => (listener: Listener<T>) => {
    list.push(listener);
    return () => list.splice(list.indexOf(listener), 1);
  };
  const noop = () => () => undefined;
  const api = {
    getLogHistory: async () => ({ mission: [], driver: [] }),
    getMissionStatus: async () => ({ running: false, pid: null, startedAt: null }),
    onMissionLog: noop,
    onDriverLog: noop,
    onRawEvidenceReady: noop,
    onStepChange: subscribe(steps),
    onMissionExit: subscribe(exits),
    startMission: () =>
      new Promise((resolve) => {
        // main.cjs refuses a second process while one exists.
        if (processUp) {
          resolve({ success: false, message: 'Uma missão já está em andamento.' });
          return;
        }
        launches.push((result) => {
          processUp = result.success;
          resolve(result);
        });
      }),
    // main.cjs sends the landing and then waits for the process to go down
    // (`stopMissionProcess`) before it answers.
    abortMission: () =>
      new Promise((resolve) => aborts.push(() => resolve({ success: true, missionKilled: true }))),
  };
  return {
    api,
    /** main.cjs answering the pending `bmg:start-mission`. */
    spawn: (success = true) => launches.shift()?.({ success }),
    step: (stepNumber: number) => steps.forEach((l) => l({ stepNumber, stepName: `stage ${stepNumber}` })),
    /** main.cjs answering the pending `bmg:abort-mission`. */
    abortAnswered: () => aborts.shift()?.(),
    exit: (code: number | null) => {
      processUp = false;
      exits.forEach((l) => l({ code, signal: null }));
    },
  };
}

interface Probe {
  runtime: ReturnType<typeof useMissionRuntime>;
}

function Station({
  probe,
  benchMode = false,
  benchStage = null,
}: {
  probe: Probe;
  benchMode?: boolean;
  benchStage?: number | null;
}) {
  const runtime = useMissionRuntime();
  probe.runtime = runtime;
  return (
    <TabSwitch
      screen="cockpit"
      onNavigate={() => undefined}
      live={runtime.state === 'running'}
      locked={preflightLocked(runtime.state, benchMode, benchStage)}
    />
  );
}

let container: HTMLDivElement;
let root: Root;
let bridge: ReturnType<typeof fakeBridge>;
const probe = {} as Probe;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  bridge = fakeBridge();
  (window as unknown as { bmgAPI: unknown }).bmgAPI = bridge.api;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

const homeTab = () =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Inicio'))!;
const homeAvailable = () => !homeTab().disabled;
const flush = () => act(async () => undefined);

async function mount(props: { benchMode?: boolean; benchStage?: number | null } = {}) {
  await act(async () => root.render(<Station probe={probe} {...props} />));
}

/**
 * The operator's click on "Iniciar Missão": the launch is in flight, not yet
 * answered. Wrapped in an object because an async function returning the bare
 * promise would itself wait for the launch to settle.
 */
async function clickLaunch(): Promise<{ settled: Promise<{ success: boolean }> }> {
  let settled!: Promise<{ success: boolean }>;
  await act(async () => {
    settled = probe.runtime.launch({ countdown: 10, noFly: false });
  });
  return { settled };
}

describe('pre-flight lock across a real flight', () => {
  it('locks from the launch click, through the countdown and the flight, and frees on landing', async () => {
    await mount();
    expect(homeAvailable()).toBe(true);

    const launch = await clickLaunch();
    expect(probe.runtime.state).toBe('arming');
    expect(homeAvailable()).toBe(false);

    // main.cjs has spawned the mission; the countdown overlay starts now.
    await act(async () => bridge.spawn(true));
    await launch.settled;
    expect(probe.runtime.state).toBe('running');
    expect(homeAvailable()).toBe(false);

    await act(async () => bridge.step(1));
    expect(homeAvailable()).toBe(false);
    await act(async () => bridge.step(3));
    await act(async () => bridge.step(5));
    expect(homeAvailable()).toBe(false);

    await act(async () => bridge.exit(0));
    expect(probe.runtime.state).toBe('finished');
    expect(homeAvailable()).toBe(true);
  });

  it('stays locked while an abort brings the aircraft down, and frees once the process is gone', async () => {
    await mount();
    const launch = await clickLaunch();
    await act(async () => bridge.spawn(true));
    await launch.settled;
    await act(async () => bridge.step(2));

    let aborting!: Promise<void>;
    await act(async () => {
      aborting = probe.runtime.abort();
    });
    expect(probe.runtime.state).toBe('aborting');
    expect(homeAvailable()).toBe(false);

    await act(async () => bridge.exit(130));
    await act(async () => bridge.abortAnswered());
    await act(async () => aborting);
    expect(probe.runtime.state).toBe('faulted');
    expect(homeAvailable()).toBe(true);
  });

  it('frees the tab when the mission never starts', async () => {
    await mount();
    const launch = await clickLaunch();
    expect(homeAvailable()).toBe(false);
    await act(async () => bridge.spawn(false));
    await launch.settled;
    expect(probe.runtime.state).toBe('faulted');
    expect(homeAvailable()).toBe(true);
  });

  it('a second launch click during the countdown neither unlocks nor faults the flight', async () => {
    await mount();
    const first = await clickLaunch();
    const second = await clickLaunch();
    await act(async () => bridge.spawn(true));
    await first.settled;
    const refused = await second.settled;

    expect(refused.success).toBe(false);
    expect(probe.runtime.state).toBe('running');
    expect(homeAvailable()).toBe(false);

    // And once the countdown is under way, a stray click is refused the same way.
    const third = await (await clickLaunch()).settled;
    await flush();
    expect(third.success).toBe(false);
    expect(probe.runtime.state).toBe('running');
    expect(homeAvailable()).toBe(false);
  });
});

describe('pre-flight lock on the bench', () => {
  it('never locks a bench mission', async () => {
    await mount({ benchMode: true });
    const launch = await clickLaunch();
    expect(homeAvailable()).toBe(true);
    await act(async () => bridge.spawn(true));
    await launch.settled;
    await act(async () => bridge.step(2));
    expect(homeAvailable()).toBe(true);
  });

  it('never locks a bench routine', async () => {
    await mount({ benchStage: 4 });
    await act(async () => probe.runtime.beginBenchStage());
    expect(homeAvailable()).toBe(true);
  });
});

describe('preflightLocked', () => {
  const states: MissionState[] = ['idle', 'arming', 'running', 'aborting', 'finished', 'faulted'];

  it('locks exactly while a real mission is arming, running or aborting', () => {
    expect(states.filter((state) => preflightLocked(state, false, null))).toEqual([
      'arming',
      'running',
      'aborting',
    ]);
  });

  it('never locks for the bench', () => {
    for (const state of states) {
      expect(preflightLocked(state, true, null)).toBe(false);
      expect(preflightLocked(state, false, 3)).toBe(false);
    }
  });
});
