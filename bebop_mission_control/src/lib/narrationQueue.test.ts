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

describe('pruneEarlierStages', () => {
  it('discards pending lines belonging to earlier stages when stage advances', async () => {
    const queue = new NarrationQueue(async () => true);
    queue.enqueue('stage1_a', () => 'a', { stage: 1 });
    queue.enqueue('stage1_b', () => 'b', { stage: 1 });
    queue.enqueue('stage2_a', () => 'c', { stage: 2 });
    queue.enqueue('stage3_a', () => 'd', { stage: 3 });

    expect(queue.labels()).toEqual(['stage1_b', 'stage2_a', 'stage3_a']);
    queue.pruneEarlierStages(2);
    // stage 1 items dropped, stage 2 and 3 kept
    expect(queue.labels()).toEqual(['stage2_a', 'stage3_a']);

    queue.pruneEarlierStages(3);
    expect(queue.labels()).toEqual(['stage3_a']);
  });
});

describe('onStart', () => {
  it('onStart runs before speak is called and once per item', async () => {
    const events: string[] = [];
    const queue = new NarrationQueue(async (text) => {
      events.push(`speak:${text}`);
      return true;
    });
    queue.enqueue('a', () => 'um', { onStart: () => events.push('start:a') });
    queue.enqueue('b', () => 'dois', { onStart: () => events.push('start:b') });
    await vi.advanceTimersByTimeAsync(10);
    expect(events).toEqual(['start:a', 'speak:um', 'start:b', 'speak:dois']);
  });

  it('onStart is not called for an item cancelled before its turn', async () => {
    const started: string[] = [];
    let finish: (heard: boolean) => void = () => undefined;
    const queue = new NarrationQueue(
      () =>
        new Promise<boolean>((resolve) => {
          finish = resolve;
        })
    );
    queue.enqueue('a', () => 'um', { onStart: () => started.push('a') });
    queue.enqueue('b', () => 'dois', { onStart: () => started.push('b') });
    await vi.advanceTimersByTimeAsync(10);
    queue.reset();
    finish(true);
    await vi.advanceTimersByTimeAsync(10);
    expect(started).toEqual(['a']);
  });

  it('a throwing onStart does not stop the line', async () => {
    const spoken: string[] = [];
    const queue = new NarrationQueue(async (text) => {
      spoken.push(text);
      return true;
    });
    queue.enqueue('a', () => 'um', {
      onStart: () => {
        throw new Error('presentation');
      },
    });
    await vi.advanceTimersByTimeAsync(10);
    expect(spoken).toEqual(['um']);
  });

  it('items without onStart behave as before', async () => {
    const done: boolean[] = [];
    const queue = new NarrationQueue(async () => true);
    queue.enqueue('a', () => 'um', { onDone: (heard) => done.push(heard) });
    await vi.advanceTimersByTimeAsync(10);
    expect(done).toEqual([true]);
  });
});

describe('onAudio', () => {
  it('passes on when the voice begins to sound and for how long', async () => {
    const sounded: Array<[number, number | null]> = [];
    const queue = new NarrationQueue(
      (_text, _priority, onAudio) =>
        new Promise<boolean>((resolve) => {
          setTimeout(() => onAudio?.(2400), 150);
          setTimeout(() => resolve(true), 2550);
        })
    );
    const t0 = Date.now();
    queue.enqueue('a', () => 'um', { fallbackMs: 1800, onAudio: (ms) => sounded.push([Date.now() - t0, ms]) });
    await vi.advanceTimersByTimeAsync(3000);
    expect(sounded).toEqual([[150, 2400]]);
  });

  it('runs over the fallback beat when the line is not heard', async () => {
    const sounded: Array<[number, number | null]> = [];
    const queue = new NarrationQueue(async () => false);
    const t0 = Date.now();
    queue.enqueue('a', () => 'um', { fallbackMs: 1800, onAudio: (ms) => sounded.push([Date.now() - t0, ms]) });
    await vi.advanceTimersByTimeAsync(2000);
    expect(sounded).toEqual([[0, 1800]]);
  });

  it('is called once even if the voice reports twice', async () => {
    const sounded: Array<number | null> = [];
    const queue = new NarrationQueue(async (_text, _priority, onAudio) => {
      onAudio?.(900);
      onAudio?.(950);
      return true;
    });
    queue.enqueue('a', () => 'um', { onAudio: (ms) => sounded.push(ms) });
    await vi.advanceTimersByTimeAsync(10);
    expect(sounded).toEqual([900]);
  });
});
