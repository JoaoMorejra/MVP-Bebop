import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NarrationQueue } from './narrationQueue';

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('fallback pacing', () => {
  it('holds the fallback beat only for a line the copilot did not speak', async () => {
    const done: number[] = [];
    const queue = new NarrationQueue(async () => false);
    queue.enqueue('a', () => 'um', { fallbackMs: 1800, onDone: () => done.push(Date.now()) });
    queue.enqueue('b', () => 'dois', { fallbackMs: 1800, onDone: () => done.push(Date.now()) });
    const t0 = Date.now();
    await vi.advanceTimersByTimeAsync(1799);
    expect(done).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toEqual([t0 + 1800]);
    await vi.advanceTimersByTimeAsync(1800);
    expect(done).toEqual([t0 + 1800, t0 + 3600]);
  });

  it('adds nothing after a line that was heard', async () => {
    const done: number[] = [];
    const queue = new NarrationQueue(
      (text) => new Promise<boolean>((resolve) => setTimeout(() => resolve(true), text === 'um' ? 700 : 500))
    );
    const t0 = Date.now();
    queue.enqueue('a', () => 'um', { fallbackMs: 1800, onDone: () => done.push(Date.now() - t0) });
    queue.enqueue('b', () => 'dois', { fallbackMs: 1800, onDone: () => done.push(Date.now() - t0) });
    await vi.advanceTimersByTimeAsync(5000);
    expect(done).toEqual([700, 1200]);
  });

  it('a cut line does not wait out its fallback', async () => {
    const done: string[] = [];
    const queue = new NarrationQueue(async () => false);
    queue.enqueue('report', () => 'achado', { group: 'forensic', fallbackMs: 1800, onDone: () => done.push('report') });
    await vi.advanceTimersByTimeAsync(10);
    queue.resetGroup('forensic');
    queue.enqueue('next', () => 'seguinte', { onDone: () => done.push('next') });
    await vi.advanceTimersByTimeAsync(10);
    expect(done).toEqual(['next']);
  });
});

describe('silence', () => {
  it('drops every pending line, alerts included, and cuts the one playing', async () => {
    const spoken: string[] = [];
    const interrupt = vi.fn();
    let finish: (heard: boolean) => void = () => undefined;
    const queue = new NarrationQueue(
      (text) => {
        spoken.push(text);
        return new Promise<boolean>((resolve) => {
          finish = resolve;
        });
      },
      interrupt
    );
    queue.enqueue('a', () => 'um');
    queue.enqueue('b', () => 'dois');
    queue.preempt('alert-1', () => 'alerta um');
    queue.preempt('alert-2', () => 'alerta dois');
    await vi.advanceTimersByTimeAsync(0);
    const before = interrupt.mock.calls.length;
    const heardBefore = [...spoken];
    queue.silence();
    expect(interrupt.mock.calls.length).toBe(before + 1);
    expect(queue.pending).toBe(0);
    finish(false);
    await vi.advanceTimersByTimeAsync(100);
    expect(spoken).toEqual(heardBefore);
  });
});
