import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadMain } from './__fixtures__/mainHarness';

let main: ReturnType<typeof loadMain>;

beforeAll(() => {
  main = loadMain();
});

afterAll(() => {
  main.restore();
});

const handler = (name: string, ...args: unknown[]) => main.handlers[name]({}, ...args);

const gate = (doc: unknown, options?: Record<string, unknown>) => handler('bmg:test-resolve-battery-gate', doc, options);

describe('resolveBatteryGate', () => {
  it('reads the switch and a string threshold from the document', async () => {
    expect(await gate({ battery: { enabled: false, land_pct: '35' } })).toEqual({ enabled: false, thresholdPct: 35 });
  });

  it('reads the string "false" as off, never as on by truthiness', async () => {
    expect(await gate({ battery: { enabled: 'false', land_pct: 35 } })).toEqual({ enabled: false, thresholdPct: 35 });
  });

  it('defaults the switch on when the document has none, and keeps a 0 threshold', async () => {
    expect(await gate({ battery: { land_pct: 0 } })).toEqual({ enabled: true, thresholdPct: 0 });
    expect(await gate(null)).toEqual({ enabled: true, thresholdPct: 20 });
  });

  it('falls back to the options, and a malformed switch never turns the gate off', async () => {
    expect(await gate({}, { failsafeEnabled: false, failsafeThreshold: 30 })).toEqual({ enabled: false, thresholdPct: 30 });
    expect(await gate({ battery: { enabled: 'no' } })).toEqual({ enabled: true, thresholdPct: 20 });
  });
});

describe('bmg:set-battery-failsafe', () => {
  beforeEach(async () => {
    await handler('bmg:test-set-command-bridge-ready', true);
    await handler('bmg:test-command-lines');
  });

  it('forwards a valid switch to the running mission through the bridge', async () => {
    await handler('bmg:test-set-mission-running', true);
    expect(await handler('bmg:set-battery-failsafe', { enabled: false, thresholdPct: 25 })).toEqual({ success: true });
    expect(await handler('bmg:test-command-lines')).toEqual([{ op: 'battery', enabled: false, land_pct: 25 }]);
  });

  it('refuses invalid values and a missing mission', async () => {
    await handler('bmg:test-set-mission-running', true);
    for (const payload of [{ enabled: 'false', thresholdPct: 20 }, { enabled: true, thresholdPct: 120 }, { enabled: true }]) {
      expect((await handler('bmg:set-battery-failsafe', payload)) as { success: boolean }).toMatchObject({ success: false });
    }
    await handler('bmg:test-set-mission-running', false);
    expect((await handler('bmg:set-battery-failsafe', { enabled: true, thresholdPct: 20 })) as { success: boolean }).toMatchObject({
      success: false,
    });
    expect(await handler('bmg:test-command-lines')).toEqual([]);
  });
});

describe('bmg:nudge', () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-27T12:00:00Z'));
    await handler('bmg:test-set-command-bridge-ready', true);
    await handler('bmg:test-set-mission-running', true);
    await handler('bmg:test-command-lines');
  });

  it('saturates each axis to {-1, 0, 1} and drops NaN', async () => {
    await handler('bmg:test-nudge', { vx: 3, vy: Number.NaN, vyaw: -0.7 });
    expect(await handler('bmg:test-command-lines')).toEqual([{ op: 'nudge', vx: 1, vy: 0, vyaw: -1 }]);
    vi.useRealTimers();
  });

  it('is dropped without a mission', async () => {
    await handler('bmg:test-set-mission-running', false);
    expect(await handler('bmg:test-nudge', { vx: 1, vy: 0, vyaw: 0 })).toBe(false);
    expect(await handler('bmg:test-command-lines')).toEqual([]);
    vi.useRealTimers();
  });

  it('is throttled to 20 Hz, except the release, which always passes', async () => {
    vi.advanceTimersByTime(1000);
    for (let i = 0; i < 10; i += 1) {
      await handler('bmg:test-nudge', { vx: 1, vy: 0, vyaw: 0 });
      vi.advanceTimersByTime(10);
    }
    await handler('bmg:test-nudge', { vx: 0, vy: 0, vyaw: 0 });
    const lines = (await handler('bmg:test-command-lines')) as Array<Record<string, number>>;
    const pressed = lines.filter((line) => line.vx === 1);
    expect(pressed.length).toBeLessThanOrEqual(3);
    expect(pressed.length).toBeGreaterThanOrEqual(1);
    expect(lines[lines.length - 1]).toEqual({ op: 'nudge', vx: 0, vy: 0, vyaw: 0 });
    vi.useRealTimers();
  });
});
