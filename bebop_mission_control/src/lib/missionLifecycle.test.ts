import { EventEmitter } from 'node:events';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LAND_PUB,
  STOP_PUB,
  createBridgeGate,
  createShutdownSequence,
  publishStopThenLand,
  signalOnce,
  singleFlight,
} from '../../electron/missionLifecycle.cjs';

class FakeChild extends EventEmitter {
  signals: string[] = [];
  kill(signal: string) {
    this.signals.push(signal);
    return true;
  }
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('signalOnce', () => {
  it('sends SIGINT exactly once however many times it is asked', async () => {
    const child = new FakeChild();
    const first = signalOnce(child, { grace: 1200 });
    const second = signalOnce(child, { grace: 900 });
    const third = signalOnce(child, { grace: 900 });

    expect(second).toBe(first);
    expect(third).toBe(first);
    expect(child.signals).toEqual(['SIGINT']);

    child.emit('close', 3, null);
    await expect(first).resolves.toBe(true);
  });

  it('a second request after the first settled still does not re-signal the same child', async () => {
    const child = new FakeChild();
    const first = signalOnce(child, { grace: 1200 });
    child.emit('close', 0, null);
    await first;

    const again = signalOnce(child, { grace: 1200 });
    expect(again).toBe(first);
    expect(child.signals).toEqual(['SIGINT']);
  });

  it('escalates to SIGKILL once after the grace and never repeats SIGINT', async () => {
    const child = new FakeChild();
    const log = vi.fn();
    const pending = signalOnce(child, { grace: 1200, log });

    vi.advanceTimersByTime(1199);
    expect(child.signals).toEqual(['SIGINT']);
    vi.advanceTimersByTime(1);
    expect(child.signals).toEqual(['SIGINT', 'SIGKILL']);
    expect(log).toHaveBeenCalledTimes(1);

    child.emit('close', null, 'SIGKILL');
    await expect(pending).resolves.toBe(true);
    vi.advanceTimersByTime(5000);
    expect(child.signals).toEqual(['SIGINT', 'SIGKILL']);
  });

  it('settles on the backstop when the close event never arrives', async () => {
    const child = new FakeChild();
    const onSettle = vi.fn();
    const pending = signalOnce(child, { grace: 100, onSettle });
    vi.advanceTimersByTime(100 + 1500);
    await expect(pending).resolves.toBe(true);
    expect(onSettle).toHaveBeenCalledTimes(1);
  });

  it('a child whose kill throws settles immediately', async () => {
    const child = new FakeChild();
    child.kill = () => {
      throw new Error('ESRCH');
    };
    const onSettle = vi.fn();
    await expect(signalOnce(child, { grace: 100, onSettle })).resolves.toBe(true);
    expect(onSettle).toHaveBeenCalledTimes(1);
  });
});

describe('singleFlight', () => {
  it('returns the pending promise to concurrent callers and runs the body once', async () => {
    let release: (value: string) => void = () => undefined;
    const body = vi.fn(() => new Promise<string>((resolve) => { release = resolve; }));
    const guarded = singleFlight(body);

    const a = guarded();
    const b = guarded();
    const c = guarded();
    expect(b).toBe(a);
    expect(c).toBe(a);

    await vi.advanceTimersByTimeAsync(0);
    release('done');
    await expect(a).resolves.toBe('done');
    expect(body).toHaveBeenCalledTimes(1);
  });

  it('runs again once the previous call has settled', async () => {
    const body = vi.fn(async () => 'ok');
    const guarded = singleFlight(body);
    await guarded();
    await guarded();
    expect(body).toHaveBeenCalledTimes(2);
  });

  it('a rejected call releases the guard', async () => {
    const body = vi.fn(async () => {
      throw new Error('boom');
    });
    const guarded = singleFlight(body);
    await expect(guarded()).rejects.toThrow('boom');
    await expect(guarded()).rejects.toThrow('boom');
    expect(body).toHaveBeenCalledTimes(2);
  });
});

describe('land backup commands', () => {
  it('never publish once with -w 0, which lost one land in three live', () => {
    for (const command of [LAND_PUB, STOP_PUB]) {
      expect(command).not.toMatch(/--once|-1\b|-w 0/);
      expect(command).toMatch(/-w 1\b/);
      expect(command).toMatch(/-t 10\b/);
      expect(command).toMatch(/-r 20\b/);
      expect(command).toMatch(/--max-wait-time-secs \d+/);
    }
    expect(LAND_PUB).toContain('/bebop/land std_msgs/msg/Empty');
    expect(STOP_PUB).toContain('/bebop/cmd_vel geometry_msgs/msg/Twist');
  });

  it('runs stop to completion before land, and land is always the last command', async () => {
    const started: string[] = [];
    const callbacks: Array<(err: Error | null) => void> = [];
    const exec = vi.fn((command: string, _opts: unknown, done: (err: Error | null) => void) => {
      started.push(command);
      callbacks.push(done);
    });

    const result = publishStopThenLand(exec, { ROS_DOMAIN_ID: '14' }, 20000);
    expect(started).toEqual([STOP_PUB]);

    callbacks[0](null);
    await vi.advanceTimersByTimeAsync(0);
    expect(started).toEqual([STOP_PUB, LAND_PUB]);

    callbacks[1](null);
    await expect(result).resolves.toEqual({ stop: null, land: null });
  });

  it('a failed stop still publishes land', async () => {
    const started: string[] = [];
    const exec = vi.fn((command: string, _opts: unknown, done: (err: Error | null) => void) => {
      started.push(command);
      done(command === STOP_PUB ? new Error('no subscriber') : null);
    });
    const result = await publishStopThenLand(exec, {}, 20000);
    expect(started).toEqual([STOP_PUB, LAND_PUB]);
    expect(result.stop).toBeInstanceOf(Error);
    expect(result.land).toBeNull();
  });
});

describe('createBridgeGate', () => {
  it('a bridge is usable only after it has announced ready', () => {
    const gate = createBridgeGate();
    const bridge = new FakeChild();
    expect(gate.isReady(bridge)).toBe(false);
    gate.markReady(bridge);
    expect(gate.isReady(bridge)).toBe(true);
  });

  it('a restarted bridge is not ready on the strength of its predecessor', () => {
    const gate = createBridgeGate();
    const old = new FakeChild();
    gate.markReady(old);
    const restarted = new FakeChild();
    expect(gate.isReady(restarted)).toBe(false);
    expect(gate.isReady(null)).toBe(false);
  });
});

describe('createShutdownSequence', () => {
  function harness(airborneAfterLandMs: number | null) {
    const order: string[] = [];
    let airborne = airborneAfterLandMs !== null;
    const sequence = createShutdownSequence({
      stopMission: async () => {
        order.push('stop-mission');
      },
      isAirborne: () => airborne,
      commandLand: () => {
        order.push('land');
        if (airborneAfterLandMs !== null && Number.isFinite(airborneAfterLandMs)) {
          setTimeout(() => {
            airborne = false;
          }, airborneAfterLandMs);
        }
      },
      stopServices: () => {
        order.push(airborne ? 'stop-services-airborne' : 'stop-services');
      },
      groundWaitMs: 10000,
      pollMs: 200,
    });
    return { order, sequence };
  }

  it('drains the mission before anything else and stops services last', async () => {
    const { order, sequence } = harness(null);
    await expect(sequence()).resolves.toEqual({ landCommanded: false, grounded: true });
    expect(order).toEqual(['stop-mission', 'stop-services']);
  });

  it('lands an airborne aircraft through the bridge and waits for ground before the driver goes', async () => {
    const { order, sequence } = harness(3000);
    const done = sequence();
    await vi.advanceTimersByTimeAsync(0);
    expect(order).toEqual(['stop-mission', 'land']);
    await vi.advanceTimersByTimeAsync(2999);
    expect(order).toEqual(['stop-mission', 'land']);
    await vi.advanceTimersByTimeAsync(400);
    await expect(done).resolves.toEqual({ landCommanded: true, grounded: true });
    expect(order).toEqual(['stop-mission', 'land', 'stop-services']);
  });

  it('gives up on ground after the ceiling and still tears down', async () => {
    const { order, sequence } = harness(Number.POSITIVE_INFINITY);
    const done = sequence();
    await vi.advanceTimersByTimeAsync(10000 + 200);
    await expect(done).resolves.toEqual({ landCommanded: true, grounded: false });
    expect(order).toEqual(['stop-mission', 'land', 'stop-services-airborne']);
  });

  it('runs once however many quit paths ask for it', async () => {
    const { order, sequence } = harness(null);
    const a = sequence();
    const b = sequence();
    expect(b).toBe(a);
    await a;
    await sequence();
    expect(order).toEqual(['stop-mission', 'stop-services']);
  });
});
