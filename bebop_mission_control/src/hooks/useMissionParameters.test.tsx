// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument } from '../lib/__fixtures__/missionDocument';
import { getPath, setPath } from '../lib/paths';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const bridge = {
  getParameters: vi.fn(),
  getParameterDefaults: vi.fn(),
  saveParameters: vi.fn(async () => ({ success: true })),
};
vi.mock('./useBridge', () => ({ useBridge: () => bridge }));

import { useMissionParameters } from './useMissionParameters';

let latest: ReturnType<typeof useMissionParameters>;
const Probe = () => {
  latest = useMissionParameters();
  return null;
};

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.localStorage.clear();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function mount(stored: unknown, defaults: unknown) {
  bridge.getParameters.mockResolvedValue(stored);
  bridge.getParameterDefaults.mockResolvedValue(defaults);
  await act(async () => {
    root.render(<Probe />);
  });
}

describe('useMissionParameters', () => {
  it('reports a corrupt store as an error and holds no document', async () => {
    await mount({ success: false, error: 'mission_config.json corrompido' }, { success: true, params: completeDocument() });
    expect(latest.status).toBe('error');
    expect(latest.error).toContain('corrompido');
    expect(latest.working).toBeNull();
  });

  it('takes its defaults from the mission', async () => {
    const defaults = completeDocument({ 'kinematics.target_altitude_m': 1.0 });
    await mount({ success: true, params: completeDocument() }, { success: true, params: defaults });
    expect(bridge.getParameterDefaults).toHaveBeenCalled();
    expect(latest.factory).toEqual(defaults);
  });

  it('applies the preset to the sheet fields only', async () => {
    const stored = completeDocument({ no_fly: true, 'calibration.samples': 40, 'lateral_pid.kp': 0.42 });
    await mount({ success: true, params: stored }, { success: true, params: completeDocument() });
    const preset = setPath(
      completeDocument({ no_fly: false, 'calibration.samples': 1, 'lateral_pid.kp': 9 }),
      'kinematics.target_altitude_m',
      3.1
    );
    window.localStorage.setItem('bmg.operator-preset.v2', JSON.stringify(preset));
    act(() => root.unmount());
    root = createRoot(host);
    await mount({ success: true, params: stored }, { success: true, params: completeDocument() });

    act(() => latest.applyPreset());
    expect(getPath(latest.working, 'kinematics.target_altitude_m')).toBe(3.1);
    expect(getPath(latest.working, 'no_fly')).toBe(true);
    expect(getPath(latest.working, 'calibration.samples')).toBe(40);
    expect(getPath(latest.working, 'lateral_pid.kp')).toBe(0.42);
  });

  it('restores the factory values of the sheet fields only', async () => {
    const stored = completeDocument({ no_fly: true, 'kinematics.target_altitude_m': 3.3, 'lateral_pid.kp': 0.77 });
    const defaults = completeDocument({ no_fly: false, 'kinematics.target_altitude_m': 1.0, 'lateral_pid.kp': 0.35 });
    await mount({ success: true, params: stored }, { success: true, params: defaults });
    act(() => latest.applyFactory());
    expect(getPath(latest.working, 'kinematics.target_altitude_m')).toBe(1.0);
    expect(getPath(latest.working, 'no_fly')).toBe(true);
    expect(getPath(latest.working, 'lateral_pid.kp')).toBe(0.77);
  });

  it('reports a failed save', async () => {
    await mount({ success: true, params: completeDocument() }, { success: true, params: completeDocument() });
    bridge.saveParameters.mockResolvedValueOnce({ success: false, error: 'EACCES' } as never);
    act(() => latest.edit('kinematics.target_altitude_m', 2.0));
    let saved: boolean | undefined;
    await act(async () => {
      saved = await latest.save();
    });
    expect(saved).toBe(false);
    expect(latest.status).toBe('error');
  });
});
