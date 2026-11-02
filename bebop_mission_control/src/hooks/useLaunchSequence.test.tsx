// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LaunchView } from '../lib/launchSequence';
import { useLaunchSequence, type LaunchSequenceInput } from './useLaunchSequence';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let host: HTMLDivElement;
let root: Root;
let latest: LaunchView | null = null;
const T0 = Date.parse('2026-10-27T12:00:00Z');

const Probe: React.FC<{ input: LaunchSequenceInput }> = ({ input }) => {
  latest = useLaunchSequence(input);
  return null;
};

function input(overrides: Partial<LaunchSequenceInput> = {}): LaunchSequenceInput {
  return {
    active: true,
    clickedAt: T0,
    seconds: 10,
    paramsCommitted: false,
    linkReady: false,
    stageReached: false,
    countdown: { remaining: null, clearance: false, trim: null },
    speech: { startedAt: null, endedAt: null, heard: false },
    speechText: 'Decolagem autorizada.',
    onDone: vi.fn(),
    ...overrides,
  };
}

const render = (next: LaunchSequenceInput) => act(() => root.render(<Probe input={next} />));
const tick = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

const ready = {
  paramsCommitted: true,
  linkReady: true,
  stageReached: true,
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  window.localStorage.clear();
  latest = null;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe('useLaunchSequence', () => {
  it('returns null while inactive', () => {
    render(input({ active: false }));
    expect(latest).toBeNull();
    render(input({ clickedAt: null }));
    expect(latest).toBeNull();
  });

  it('starts at 0 with the params step active right after the click even when signals are already true', () => {
    render(input({ ...ready, countdown: { remaining: 10, clearance: false, trim: 'acked' } }));
    expect(latest).toMatchObject({ phase: 'loading', percent: 0, stepId: 'params', label: 'Parâmetros' });
  });

  it('progresses from 0% to 100% over 10 seconds without skipping any integer or checkpoint label', () => {
    const onDone = vi.fn();
    const seenPercents: number[] = [];
    const seenLabels: string[] = [];

    const record = () => {
      if (!latest) return;
      if (seenPercents[seenPercents.length - 1] !== latest.percent) {
        seenPercents.push(latest.percent);
      }
      const text = latest.message ?? latest.label;
      if (text && seenLabels[seenLabels.length - 1] !== text) {
        seenLabels.push(text);
      }
    };

    render(input({ ...ready, onDone, countdown: { remaining: 10, clearance: false, trim: 'acked' } }));
    record();

    // Advance through 0..7000 ms in 40 ms ticks
    for (let elapsed = 40; elapsed <= 7_000; elapsed += 40) {
      tick(40);
      if (elapsed === 7_000) {
        render(
          input({
            ...ready,
            onDone,
            countdown: { remaining: 3, clearance: true, trim: 'acked' },
            speech: { startedAt: T0 + 7_000, endedAt: null, heard: true },
          })
        );
      }
      record();
    }

    // Advance through 7000..10000 ms in 40 ms ticks
    for (let elapsed = 7_040; elapsed <= 10_000; elapsed += 40) {
      tick(40);
      if (elapsed === 9_500) {
        render(
          input({
            ...ready,
            onDone,
            countdown: { remaining: 1, clearance: true, trim: 'acked' },
            speech: { startedAt: T0 + 7_000, endedAt: T0 + 9_500, heard: true },
          })
        );
      }
      if (elapsed === 10_000) {
        render(
          input({
            ...ready,
            onDone,
            countdown: { remaining: 0, clearance: true, trim: 'acked' },
            speech: { startedAt: T0 + 7_000, endedAt: T0 + 9_500, heard: true },
          })
        );
      }
      record();
    }

    // Flush any remaining catch-up ticks to 100
    for (let i = 0; i < 10 && latest?.percent !== 100; i += 1) {
      tick(40);
      record();
    }

    expect(seenPercents[0]).toBe(0);
    expect(seenPercents[seenPercents.length - 1]).toBe(100);
    // Every integer from 0 to 100 must be visited in order (+1 per step)
    expect(seenPercents).toEqual(Array.from({ length: 101 }, (_, idx) => idx));
    expect(seenLabels).toEqual(['Parâmetros', 'Enlace', 'Gimbal', 'IMU', 'Solo', 'Visão', 'Tudo Pronto!']);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('reaches 90 and shows "Tudo Pronto!" at clearance with all steps done after the loading window', () => {
    vi.setSystemTime(T0 + 7_000);
    render(input({ ...ready, countdown: { remaining: 3, clearance: true, trim: 'acked' } }));
    expect(latest).toMatchObject({ phase: 'announcing', percent: 90, message: 'Tudo Pronto!' });
  });

  it('reaches 100 only when the speech ended, the backend countdown is zero, and at least 10 s elapsed', () => {
    const onDone = vi.fn();
    const base = { ...ready, onDone };
    vi.setSystemTime(T0 + 7_000);
    render(
      input({
        ...base,
        countdown: { remaining: 3, clearance: true, trim: 'acked' },
        speech: { startedAt: T0 + 7_000, endedAt: null, heard: false },
      })
    );
    vi.setSystemTime(T0 + 9_000);
    render(
      input({
        ...base,
        countdown: { remaining: 1, clearance: true, trim: 'acked' },
        speech: { startedAt: T0 + 7_000, endedAt: T0 + 9_000, heard: true },
      })
    );
    expect(latest!.percent).toBeLessThan(100);
    vi.setSystemTime(T0 + 10_000);
    render(
      input({
        ...base,
        countdown: { remaining: 0, clearance: true, trim: 'acked' },
        speech: { startedAt: T0 + 7_000, endedAt: T0 + 9_000, heard: true },
      })
    );
    expect(latest!.percent).toBe(100);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('holds at 99 when the zero arrives before the end of the line', () => {
    vi.setSystemTime(T0 + 10_000);
    render(
      input({
        ...ready,
        countdown: { remaining: 0, clearance: true, trim: 'acked' },
        speech: { startedAt: T0 + 9_500, endedAt: null, heard: true },
      })
    );
    expect(latest!.percent).toBe(99);
    expect(latest!.message).toBe('Tudo Pronto!');
  });

  it('calls onDone once at 100', () => {
    const onDone = vi.fn();
    vi.setSystemTime(T0 + 12_000);
    const done = input({
      ...ready,
      onDone,
      countdown: { remaining: 0, clearance: true, trim: 'acked' },
      speech: { startedAt: T0 + 7_000, endedAt: T0 + 9_000, heard: true },
    });
    render(done);
    tick(1000);
    render({ ...done });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('a zero reported before the 10 s minimum window expired is ignored', () => {
    const onDone = vi.fn();
    vi.setSystemTime(T0 + 2_000);
    render(input({ ...ready, seconds: 2, onDone, countdown: { remaining: 0, clearance: false, trim: 'acked' } }));
    expect(onDone).not.toHaveBeenCalled();
    expect(latest!.phase).toBe('loading');
    tick(8_500);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('an unheard speech progresses over the estimated duration', () => {
    vi.setSystemTime(T0 + 10_000);
    const unheard = input({
      ...ready,
      countdown: { remaining: 0, clearance: true, trim: 'acked' },
      speech: { startedAt: T0 + 10_000, endedAt: T0 + 10_000, heard: false },
    });
    render(unheard);
    expect(latest!.percent).toBeLessThan(100);
    tick(1_600);
    expect(latest!.percent).toBe(100);
  });

  it('percent never decreases if a signal flickers', () => {
    vi.setSystemTime(T0 + 5_000);
    render(input({ ...ready, countdown: { remaining: 7, clearance: false, trim: 'acked' } }));
    const high = latest!.percent;
    render(input({ ...ready, stageReached: false }));
    expect(latest!.percent).toBeGreaterThanOrEqual(high);
  });
});
