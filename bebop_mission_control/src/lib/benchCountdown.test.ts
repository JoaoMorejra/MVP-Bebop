import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deferBenchSpawn } from '../../electron/benchCountdown.cjs';
import type { MilestoneMessage } from '../../electron/milestones.cjs';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function run(seconds: number) {
  const keys: string[] = [];
  const spawn = vi.fn();
  const cancel = deferBenchSpawn(seconds, { emit: (m: MilestoneMessage) => keys.push(m.key), spawn });
  return { keys, spawn, cancel };
}

describe('deferBenchSpawn', () => {
  it('speaks the launch countdown and spawns the routine only at zero', () => {
    const { keys, spawn } = run(10);
    expect(keys).toEqual(['mission.start']);
    vi.advanceTimersByTime(7000);
    expect(keys).toEqual(['mission.start', 'mission.countdown_3']);
    expect(spawn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(3000);
    expect(spawn).toHaveBeenCalledTimes(1);
  });

  it('an abort during the countdown leaves no spawn and no countdown call behind', () => {
    const { keys, spawn, cancel } = run(10);
    vi.advanceTimersByTime(4000);
    expect(cancel()).toBe(true);
    vi.advanceTimersByTime(20000);
    expect(spawn).not.toHaveBeenCalled();
    expect(keys).toEqual(['mission.start']);
  });

  it('reports nothing to cancel once the routine has been spawned', () => {
    const { spawn, cancel } = run(2);
    vi.advanceTimersByTime(2000);
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(cancel()).toBe(false);
  });

  it('treats a bad countdown as zero', () => {
    const { spawn } = run(Number.NaN);
    vi.advanceTimersByTime(0);
    expect(spawn).toHaveBeenCalledTimes(1);
  });
});
