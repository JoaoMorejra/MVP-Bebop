import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { finishLockState, type FinishLockInput } from './finishLock';
import { missionStateForExit } from './missionOutcome';

/**
 * The Finalizar lock replayed over a real bench run (8.3): the telemetry
 * frames and mission events recorded by `scripts/bench_rehearsal.py` in its
 * `complete` scenario, five stages under `--no-fly`.
 */
interface Frame {
  t: number;
  flying_state: number | null;
  nav_fresh: boolean | null;
  nav_source: 'aircraft' | 'simulator' | 'none' | null;
}
interface Trace {
  exit_code: number;
  exit_t: number;
  events: Array<{ t: number; kind: string; step?: number; key?: string }>;
  frames: Frame[];
}

const trace: Trace = JSON.parse(
  readFileSync(resolve(__dirname, '../../../test/fixtures/bench_rehearsal_trace.json'), 'utf-8')
);

function replay() {
  let benchFlyingState: number | null = null;
  let flyingState: number | null = null;
  let since = 0;
  return trace.frames.map((frame) => {
    const nowMs = frame.t * 1000;
    if (frame.nav_source === 'simulator' && frame.flying_state !== null) benchFlyingState = frame.flying_state;
    if (frame.flying_state !== flyingState) {
      flyingState = frame.flying_state;
      since = nowMs;
    }
    const exited = frame.t >= trace.exit_t;
    const input: FinishLockInput = {
      missionState: exited ? missionStateForExit(trace.exit_code) : 'running',
      processUp: !exited,
      exitCode: exited ? trace.exit_code : null,
      benchMode: true,
      flyingState,
      flyingStateSince: since,
      navFresh: Boolean(frame.nav_fresh),
      navSource: frame.nav_source ?? 'none',
      telemetryAgeSec: 0.2,
      finishing: false,
      benchFlyingState,
      exitedAt: exited ? trace.exit_t * 1000 : null,
    };
    return { t: frame.t, lock: finishLockState(input, nowMs) };
  });
}

const stageAt = (t: number): number => {
  let stage = 0;
  for (const event of trace.events) if (event.kind === 'step' && event.t <= t) stage = event.step ?? stage;
  return stage;
};

describe('Finalizar over the recorded bench rehearsal', () => {
  it('is a complete five-stage run', () => {
    expect(trace.exit_code).toBe(0);
    expect(trace.events.filter((e) => e.kind === 'step').map((e) => e.step)).toEqual([1, 2, 3, 4, 5]);
  });

  it('stays locked through every stage while the mission runs', () => {
    const during = replay().filter(({ t }) => t < trace.exit_t);
    expect(during.length).toBeGreaterThan(20);
    for (const { t, lock } of during) {
      expect(lock.enabled, `t=${t} stage ${stageAt(t)}`).toBe(false);
    }
    const stages = new Set(during.map(({ t }) => stageAt(t)));
    for (const stage of [1, 2, 3, 4, 5]) expect(stages.has(stage)).toBe(true);
  });

  it('opens once the run has exited with the airframe on the ground', () => {
    const after = replay().filter(({ t }) => t >= trace.exit_t);
    expect(after.length).toBeGreaterThan(0);
    for (const { lock } of after) {
      expect(lock.state).toBe('grounded');
      expect(lock.enabled).toBe(true);
    }
  });
});
