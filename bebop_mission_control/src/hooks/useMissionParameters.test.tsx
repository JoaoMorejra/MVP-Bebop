// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { completeDocument, validDocument } from '../lib/__fixtures__/missionDocument';
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

import { matchesPreset, useMissionParameters } from './useMissionParameters';

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
    window.localStorage.setItem('bmg.operator-preset.advanced', JSON.stringify(preset));
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
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    bridge.saveParameters.mockResolvedValueOnce({ success: false, error: 'EACCES' } as never);
    act(() => latest.edit('kinematics.target_altitude_m', 2.0));
    let saved: boolean | undefined;
    await act(async () => {
      saved = await latest.save();
    });
    expect(saved).toBe(false);
    expect(latest.status).toBe('error');
  });

  it('writes the slider speed to the forward and return cruise and lifts the ceiling', async () => {
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    act(() => latest.edit('kinematics.forward_cruise_velocity', 0.35));
    expect(getPath(latest.working, 'kinematics.forward_cruise_velocity')).toBe(0.35);
    expect(getPath(latest.working, 'rtl.reverse_cruise_velocity')).toBe(-0.35);
    expect(getPath(latest.working, 'kinematics.max_horizontal_speed')).toBeGreaterThanOrEqual(0.4);
    expect([...latest.changedPaths]).toEqual(['kinematics.forward_cruise_velocity']);

    await act(async () => {
      await latest.save();
    });
    const calls = bridge.saveParameters.mock.calls as unknown as unknown[][];
    const written = calls[calls.length - 1][0];
    expect(getPath(written, 'kinematics.forward_cruise_velocity')).toBe(0.35);
    expect(getPath(written, 'rtl.reverse_cruise_velocity')).toBe(-0.35);
    expect(getPath(written, 'kinematics.max_horizontal_speed')).toBeGreaterThanOrEqual(0.4);
  });

  it('couples a stored document with the old return cruise and shows it as a pending change', async () => {
    const stored = validDocument({ 'rtl.reverse_cruise_velocity': -0.1 });
    await mount({ success: true, params: stored }, { success: true, params: validDocument() });
    expect(getPath(latest.committed, 'rtl.reverse_cruise_velocity')).toBe(-0.1);
    expect(getPath(latest.working, 'rtl.reverse_cruise_velocity')).toBe(-0.2);
    expect(latest.dirty).toBe(true);
    expect([...latest.changedPaths]).toEqual(['kinematics.forward_cruise_velocity']);

    act(() => latest.discard());
    expect(getPath(latest.working, 'rtl.reverse_cruise_velocity')).toBe(-0.2);
  });

  it('restores the factory defaults with the return cruise coupled', async () => {
    const defaults = validDocument({ 'rtl.reverse_cruise_velocity': -0.1 });
    await mount({ success: true, params: validDocument({ 'kinematics.forward_cruise_velocity': 0.4, 'rtl.reverse_cruise_velocity': -0.4 }) }, { success: true, params: defaults });
    act(() => latest.applyFactory());
    expect(getPath(latest.working, 'kinematics.forward_cruise_velocity')).toBe(0.2);
    expect(getPath(latest.working, 'rtl.reverse_cruise_velocity')).toBe(-0.2);
  });

  it('counts an advanced pad edit as a change and keeps the pad out of the preset and the factory restore', async () => {
    const defaults = validDocument({ 'rtl.target_aruco_id': 3 });
    await mount({ success: true, params: validDocument() }, { success: true, params: defaults });
    act(() => latest.edit('rtl.target_aruco_id', 12));
    expect(latest.dirty).toBe(true);
    expect([...latest.changedPaths]).toEqual(['rtl.target_aruco_id']);
    act(() => latest.applyFactory());
    expect(getPath(latest.working, 'rtl.target_aruco_id')).toBe(12);
    act(() => latest.discard());
    expect(getPath(latest.working, 'rtl.target_aruco_id')).toBe(8);
    expect(latest.dirty).toBe(false);
  });

  it('refuses to save a combination with an error conflict, in the hook itself', async () => {
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    bridge.saveParameters.mockClear();
    act(() => latest.edit('kinematics.target_altitude_m', 0.1));
    act(() => latest.edit('kinematics.altitude_ceiling_margin_m', 0));
    expect(latest.conflicts.map((c) => c.id)).toEqual(expect.arrayContaining(['E03', 'E04']));
    expect(latest.saveAttempted).toBe(false);

    let saved: boolean | undefined;
    await act(async () => {
      saved = await latest.save();
    });
    expect(saved).toBe(false);
    expect(bridge.saveParameters).not.toHaveBeenCalled();
    expect(window.localStorage.getItem('bmg.working-parameters.advanced')).toBeNull();
    expect(latest.saveAttempted).toBe(true);
    expect(latest.status).toBe('ready');

    act(() => latest.discard());
    expect(latest.saveAttempted).toBe(false);
    expect(latest.conflicts).toEqual([]);
  });

  it('saves a combination that only warns, and clears the attempt', async () => {
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    bridge.saveParameters.mockClear();
    act(() => latest.edit('kinematics.countdown_sec', 4));
    expect(latest.conflicts.map((c) => [c.id, c.severity])).toEqual([['W10', 'warning']]);
    let saved: boolean | undefined;
    await act(async () => {
      saved = await latest.save();
    });
    expect(saved).toBe(true);
    expect(bridge.saveParameters).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem('bmg.working-parameters.advanced')).not.toBeNull();
    expect(latest.saveAttempted).toBe(false);
  });

  it('counts the centering window as an advanced change and saves it', async () => {
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    bridge.saveParameters.mockClear();
    act(() => latest.edit('rtl.centering_timeout_sec', 40));
    expect([...latest.changedPaths]).toEqual(['rtl.centering_timeout_sec']);
    await act(async () => {
      await latest.save();
    });
    const calls = bridge.saveParameters.mock.calls as unknown as unknown[][];
    expect(getPath(calls[calls.length - 1][0], 'rtl.centering_timeout_sec')).toBe(40);
  });

  it('backfills the turn rate of an older store without reading it as a change', async () => {
    const stored = setPath(validDocument(), 'yaw_pattern', undefined);
    await mount({ success: true, params: stored }, { success: true, params: validDocument() });
    expect(getPath(latest.committed, 'yaw_pattern.scan_vyaw')).toBe(0.15);
    expect(getPath(latest.working, 'yaw_pattern.scan_vyaw')).toBe(0.15);
    expect(latest.dirty).toBe(false);
  });

  it('keeps a preset saved before the turn rate existed active', async () => {
    const preset = setPath(validDocument(), 'yaw_pattern', undefined);
    expect(matchesPreset(validDocument(), preset)).toBe(true);
    expect(matchesPreset(validDocument({ 'kinematics.target_altitude_m': 2.5 }), preset)).toBe(false);
    window.localStorage.setItem('bmg.operator-preset.advanced', JSON.stringify(preset));
    await mount({ success: true, params: validDocument() }, { success: true, params: validDocument() });
    expect(getPath(latest.working, 'yaw_pattern.scan_vyaw')).toBe(0.15);
    expect(latest.isPresetActive).toBe(true);
  });

  it('backfills the turn rate when the defaults arrive before the store', async () => {
    let serve: (value: unknown) => void = () => undefined;
    bridge.getParameters.mockReturnValue(new Promise((resolve) => (serve = resolve)));
    bridge.getParameterDefaults.mockResolvedValue({ success: true, params: validDocument() });
    await act(async () => {
      root.render(<Probe />);
    });
    expect(latest.factory).not.toBeNull();
    await act(async () => {
      serve({ success: true, params: setPath(validDocument(), 'yaw_pattern', undefined) });
    });
    expect(getPath(latest.committed, 'yaw_pattern.scan_vyaw')).toBe(0.15);
    expect(getPath(latest.working, 'yaw_pattern.scan_vyaw')).toBe(0.15);
    expect(latest.dirty).toBe(false);
  });
});

const saveParameters = bridge.saveParameters;

async function mountReady(overrides: Record<string, unknown> = {}) {
  await mount({ success: true, params: validDocument(overrides) }, { success: true, params: validDocument() });
  return {
    get current() {
      return latest;
    },
  };
}

describe('per-mode parameters', () => {
  it('keeps the working values of each mode apart', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    await act(() => hook.current.switchMode('simple'));
    expect(hook.current.missionMode).toBe('simple');
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(1); // squeezed copy
    act(() => hook.current.edit('kinematics.target_altitude_m', 0.7));
    await act(() => hook.current.save());

    await act(() => hook.current.switchMode('advanced'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(2.4);
    await act(() => hook.current.switchMode('simple'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(0.7);
  });

  it('never lets a save in one mode overwrite another mode\'s stored document', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    await act(() => hook.current.switchMode('intermediate'));
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.1));
    await act(() => hook.current.save());
    const advancedStored = JSON.parse(window.localStorage.getItem('bmg.working-parameters.advanced') ?? '{}');
    expect(getPath(advancedStored, 'kinematics.target_altitude_m')).toBe(2.4);
  });

  it('keeps one favourite per mode', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    act(() => hook.current.saveAsPreset());
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.preset).toBeNull();
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.3));
    act(() => hook.current.saveAsPreset());
    await act(() => hook.current.switchMode('advanced'));
    act(() => hook.current.edit('kinematics.target_altitude_m', 1.1));
    act(() => hook.current.applyPreset());
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(window.localStorage.getItem('bmg.operator-preset.intermediate')).toContain('3.3');
  });

  it('reports the favourite as active only in its own mode', async () => {
    const hook = await mountReady({ mission_mode: 'advanced' });
    act(() => hook.current.saveAsPreset());
    expect(hook.current.isPresetActive).toBe(true);
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.isPresetActive).toBe(false);
  });

  it('writes the active document to the store on a switch, leaving the sheet clean', async () => {
    const hook = await mountReady({ mission_mode: 'advanced' });
    await act(() => hook.current.switchMode('intermediate'));
    expect(hook.current.dirty).toBe(false);
    expect(saveParameters).toHaveBeenLastCalledWith(expect.objectContaining({ mission_mode: 'intermediate' }));
  });

  it('stashes unsaved edits of the mode being left', async () => {
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    act(() => hook.current.edit('kinematics.target_altitude_m', 3.7));
    await act(() => hook.current.switchMode('intermediate'));
    await act(() => hook.current.switchMode('advanced'));
    expect(getPath(hook.current.working, 'kinematics.target_altitude_m')).toBe(3.7);
  });

  it('migrates the pre-modes document and favourite into the advanced mode', async () => {
    window.localStorage.setItem('bmg.operator-preset.v2', JSON.stringify(setPath(completeDocument(), 'kinematics.target_altitude_m', 1.9)));
    const hook = await mountReady({ mission_mode: 'advanced' });
    expect(hook.current.preset).not.toBeNull();
    expect(getPath(hook.current.preset, 'kinematics.target_altitude_m')).toBe(1.9);
  });

  it('ignores a second switch issued while the first is still saving', async () => {
    const hook = await mountReady({ mission_mode: 'advanced' });
    saveParameters.mockClear();
    let release: (value: { success: boolean }) => void = () => undefined;
    saveParameters.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }) as never);
    let first: Promise<boolean> = Promise.resolve(false);
    let second: Promise<boolean> = Promise.resolve(true);
    await act(async () => {
      first = hook.current.switchMode('simple');
      second = hook.current.switchMode('intermediate');
      await Promise.resolve();
    });
    expect(await second).toBe(false);
    await act(async () => {
      release({ success: true });
      await first;
    });
    expect(await first).toBe(true);
    expect(saveParameters).toHaveBeenCalledTimes(1);
    expect(hook.current.missionMode).toBe('simple');
  });

  it('opens with the values it has when the storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const hook = await mountReady({ mission_mode: 'advanced', 'kinematics.target_altitude_m': 2.4 });
    expect(hook.current.status).toBe('ready');
    await act(() => hook.current.switchMode('simple'));
    expect(hook.current.missionMode).toBe('simple');
  });
});
