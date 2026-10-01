// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MissionState, TelemetryView } from '../types/mission';
import type { FinishLockResult } from '../lib/finishLock';
import { useFinishLock } from './useFinishLock';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
let latest: FinishLockResult | null = null;

interface ProbeProps {
  missionState: MissionState;
  telemetry: Partial<TelemetryView>;
  benchMode?: boolean;
  finishing?: boolean;
}

const Probe: React.FC<ProbeProps> = ({ missionState, telemetry, benchMode = false, finishing = false }) => {
  latest = useFinishLock({
    missionState,
    exitCode: missionState === 'finished' ? 0 : null,
    benchMode,
    telemetry: telemetry as TelemetryView,
    finishing,
  });
  return null;
};

const aircraft = (flying: number | null): Partial<TelemetryView> => ({
  flying_state: flying,
  nav_fresh: flying !== null,
  nav_source: flying !== null ? 'aircraft' : 'none',
  at: Date.now(),
});

function show(props: ProbeProps) {
  act(() => root.render(<Probe {...props} />));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(1_000_000);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  latest = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('useFinishLock', () => {
  it('unlocks only after the aircraft has reported landed for the confirmation window after exit', () => {
    show({ missionState: 'running', telemetry: aircraft(2) });
    expect(latest?.state).toBe('in_flight');

    act(() => vi.advanceTimersByTime(1000));
    show({ missionState: 'running', telemetry: aircraft(0) });
    show({ missionState: 'finished', telemetry: aircraft(0) });
    expect(latest?.state).toBe('await_ground');

    act(() => vi.advanceTimersByTime(1000));
    show({ missionState: 'finished', telemetry: aircraft(0) });
    expect(latest?.state).toBe('await_ground');

    act(() => vi.advanceTimersByTime(1100));
    show({ missionState: 'finished', telemetry: aircraft(0) });
    expect(latest).toMatchObject({ state: 'grounded', enabled: true });
  });

  it('re-evaluates on its own clock, without a new telemetry frame', () => {
    show({ missionState: 'finished', telemetry: { ...aircraft(0), at: Date.now() } });
    expect(latest?.state).toBe('await_ground');
    // The frame goes stale while nothing re-renders the parent.
    act(() => vi.advanceTimersByTime(3000));
    expect(latest?.state).toBe('await_ground');
    expect(latest?.reason).toBe('Aguardando confirmação de pouso (flying_state)');
  });

  it('a bench run unlocks on exit when the simulator last reported landed', () => {
    const simulated = (flying: number): Partial<TelemetryView> => ({
      flying_state: flying,
      nav_fresh: true,
      nav_source: 'simulator',
      at: Date.now(),
    });
    show({ missionState: 'running', telemetry: simulated(2), benchMode: true });
    show({ missionState: 'running', telemetry: simulated(0), benchMode: true });
    // The simulator stops with the process; the bridge falls back to 'none'.
    show({ missionState: 'finished', telemetry: aircraft(null), benchMode: true });
    expect(latest).toMatchObject({ state: 'grounded', enabled: true });
  });

  it('forgets the previous bench run when a new mission starts', () => {
    const simulated = { flying_state: 0, nav_fresh: true, nav_source: 'simulator' as const, at: Date.now() };
    show({ missionState: 'running', telemetry: simulated, benchMode: true });
    show({ missionState: 'finished', telemetry: aircraft(null), benchMode: true });
    expect(latest?.state).toBe('grounded');
    show({ missionState: 'arming', telemetry: aircraft(null), benchMode: true });
    show({ missionState: 'faulted', telemetry: aircraft(null), benchMode: true });
    expect(latest?.state).toBe('await_ground');
  });

  it('is locked while finishing', () => {
    show({ missionState: 'finished', telemetry: aircraft(0), finishing: true });
    expect(latest?.state).toBe('finishing');
  });
});
