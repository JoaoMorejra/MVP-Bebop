// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MissionMode } from '../lib/missionMode';
import { useModeEcho, type LaunchPlan } from './useModeEcho';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Milestone = { kind: 'milestone' | 'alert'; key: string; payload: Record<string, unknown>; at: number; source: string };

let emit: (event: Milestone) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;

const Probe: React.FC<{
  requested: MissionMode | null;
  plan?: LaunchPlan | null;
  onMismatch: (reported: string, requested: string) => void;
}> = ({ requested, plan = null, onMismatch }) => {
  useModeEcho({ requested, plan, onMismatch });
  return null;
};

const planEvent = (payload: Record<string, unknown>): Milestone => ({
  kind: 'milestone',
  key: 'mission.mode',
  payload,
  at: Date.now(),
  source: 'mission',
});

const modeEvent = (mode: unknown): Milestone => ({
  kind: 'milestone',
  key: 'mission.mode',
  payload: { mode },
  at: Date.now(),
  source: 'mission',
});

async function mount(
  requested: MissionMode | null,
  onMismatch: (reported: string, requested: string) => void,
  plan: LaunchPlan | null = null
) {
  await act(async () => {
    root.render(<Probe requested={requested} plan={plan} onMismatch={onMismatch} />);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (listener: (event: Milestone) => void) => {
      emit = listener;
      return () => undefined;
    },
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

describe('useModeEcho', () => {
  it('stays quiet when the mission reports the requested mode', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch);
    await act(async () => emit(modeEvent('simple')));
    expect(onMismatch).not.toHaveBeenCalled();
  });

  it('reports a divergence once', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch);
    await act(async () => emit(modeEvent('advanced')));
    expect(onMismatch).toHaveBeenCalledTimes(1);
    expect(onMismatch).toHaveBeenCalledWith('advanced', 'simple');
  });

  it('does not repeat for two divergent events in a row', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch);
    await act(async () => emit(modeEvent('advanced')));
    await act(async () => emit(modeEvent('intermediate')));
    expect(onMismatch).toHaveBeenCalledTimes(1);
  });

  it('reports again in a second mission that requests the same mode', async () => {
    const onMismatch = vi.fn();
    await mount('advanced', onMismatch);
    await act(async () => emit(modeEvent('simple')));
    expect(onMismatch).toHaveBeenCalledTimes(1);
    await mount(null, onMismatch);
    await mount('advanced', onMismatch);
    await act(async () => emit(modeEvent('simple')));
    expect(onMismatch).toHaveBeenCalledTimes(2);
    expect(onMismatch).toHaveBeenLastCalledWith('simple', 'advanced');
  });

  it('never acts while no launch has been requested', async () => {
    const onMismatch = vi.fn();
    await mount(null, onMismatch);
    await act(async () => emit(modeEvent('advanced')));
    expect(onMismatch).not.toHaveBeenCalled();
  });

  it('never acts when the mission reports nothing', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch);
    expect(onMismatch).not.toHaveBeenCalled();
  });

  it('compares the return plan and the launch surface the mission echoes', async () => {
    const onMismatch = vi.fn();
    const plan: LaunchPlan = { rtlEnabled: false, takeoffSurface: 'platform' };
    await mount('advanced', onMismatch, plan);
    await act(async () => emit(planEvent({ mode: 'advanced', rtl_enabled: false, takeoff_surface: 'platform' })));
    expect(onMismatch).not.toHaveBeenCalled();
    await act(async () => emit(planEvent({ mode: 'advanced', rtl_enabled: true, takeoff_surface: 'platform' })));
    expect(onMismatch).toHaveBeenCalledWith('advanced, com RTL, totem', 'advanced, sem RTL, totem');
  });

  it('reports a launch surface the mission did not take', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch, { rtlEnabled: true, takeoffSurface: 'ground' });
    await act(async () => emit(planEvent({ mode: 'simple', rtl_enabled: true, takeoff_surface: 'platform' })));
    expect(onMismatch).toHaveBeenCalledWith('simple, com RTL, totem', 'simple, com RTL, solo');
  });

  it('takes a payload without the plan fields as agreeing on them', async () => {
    const onMismatch = vi.fn();
    await mount('simple', onMismatch, { rtlEnabled: false, takeoffSurface: 'ground' });
    await act(async () => emit(planEvent({ mode: 'simple' })));
    expect(onMismatch).not.toHaveBeenCalled();
  });
});
