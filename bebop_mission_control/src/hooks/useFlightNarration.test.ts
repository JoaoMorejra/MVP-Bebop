import { afterEach, describe, expect, it, vi } from 'vitest';
import { NarrationQueue } from '../lib/narrationQueue';
import { isMilestoneKey, phraseForMilestone, type StorageLike } from '../lib/copilotPhrases';
import { createMilestoneParser, scheduleScriptMilestones } from '../../electron/milestones.cjs';
import trace from '../lib/__fixtures__/earlyDetectionTrace.json';

/** A `say` whose every call stays pending until the test finishes it. */
function controlledVoice() {
  const spoken: string[] = [];
  const pending: Array<(ok: boolean) => void> = [];
  const say = (text: string) =>
    new Promise<boolean>((resolve) => {
      spoken.push(text);
      pending.push(resolve);
    });
  /** Finish the line currently being spoken and let the queue react. */
  const finish = async (ok = true) => {
    const resolve = pending.shift();
    if (!resolve) throw new Error('nothing is being spoken');
    resolve(ok);
    await flush();
  };
  return { say, spoken, finish, inFlight: () => pending.length };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe('NarrationQueue', () => {
  it('speaks one line at a time, in arrival order, each only after the previous is heard', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);

    queue.enqueue('mission.start', () => 'start');
    queue.enqueue('mission.countdown_3', () => 'countdown');
    await flush();
    expect(voice.spoken).toEqual(['start']);
    expect(voice.inFlight()).toBe(1);

    // The aircraft outruns the voice: takeoff, scan and an early detection all
    // arrive while the first line is still being read.
    queue.enqueue('mission.takeoff', () => 'takeoff');
    queue.enqueue('mission.scan_start', () => 'scan');
    queue.enqueue('mission.target_found', () => 'target');
    queue.enqueue('mission.approaching', () => 'approach');
    await flush();
    expect(voice.spoken).toEqual(['start']);

    const order = ['countdown', 'takeoff', 'scan', 'target', 'approach'];
    for (let step = 0; step < order.length; step++) {
      await voice.finish();
      expect(voice.spoken).toEqual(['start', ...order.slice(0, step + 1)]);
      expect(voice.inFlight()).toBe(1);
    }
    await voice.finish();
    expect(queue.busy).toBe(false);
    expect(queue.pending).toBe(0);
  });

  it('never skips a line when the voice reports it was not heard', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);
    queue.enqueue('a', () => 'a');
    queue.enqueue('b', () => 'b');
    await flush();
    await voice.finish(false);
    expect(voice.spoken).toEqual(['a', 'b']);
  });

  it('survives a voice that throws and one that rejects', async () => {
    const spoken: string[] = [];
    let calls = 0;
    const queue = new NarrationQueue(async (text) => {
      spoken.push(text);
      calls += 1;
      if (calls === 1) throw new Error('bridge gone');
      if (calls === 2) return Promise.reject(new Error('ipc closed'));
      return true;
    });
    queue.enqueue('a', () => 'a');
    queue.enqueue('b', () => 'b');
    queue.enqueue('c', () => 'c');
    await flush();
    await flush();
    expect(spoken).toEqual(['a', 'b', 'c']);
  });

  it('skips an item whose line cannot be composed, and keeps going', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);
    queue.enqueue('broken', () => {
      throw new Error('no pool');
    });
    queue.enqueue('empty', () => '   ');
    queue.enqueue('ok', () => 'ok');
    await flush();
    expect(voice.spoken).toEqual(['ok']);
  });

  it('composes each line when it is reached, not when it is queued', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);
    let altitude = 1;
    queue.enqueue('first', () => 'first');
    queue.enqueue('takeoff', () => `alt ${altitude}`);
    await flush();
    altitude = 2;
    await voice.finish();
    expect(voice.spoken).toEqual(['first', 'alt 2']);
  });

  it('drops what is pending on reset, and resumes with what is queued after it', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);
    queue.enqueue('old-1', () => 'old-1');
    queue.enqueue('old-2', () => 'old-2');
    queue.enqueue('old-3', () => 'old-3');
    await flush();

    queue.reset();
    queue.enqueue('new-1', () => 'new-1');
    await voice.finish();

    expect(voice.spoken).toEqual(['old-1', 'new-1']);
    expect(queue.labels()).toEqual([]);
  });

  it('lists the labels still waiting, in order', async () => {
    const voice = controlledVoice();
    const queue = new NarrationQueue(voice.say);
    queue.enqueue('a', () => 'a');
    queue.enqueue('b', () => 'b');
    queue.enqueue('c', () => 'c');
    await flush();
    expect(queue.labels()).toEqual(['b', 'c']);
  });
});

describe('NarrationQueue preemption', () => {
  it('cuts the current line, drops the backlog and speaks the alert next, at its priority', async () => {
    const spoken: Array<{ text: string; priority?: string }> = [];
    const pending: Array<(ok: boolean) => void> = [];
    let interrupted = 0;
    const queue = new NarrationQueue(
      (text, priority) =>
        new Promise<boolean>((resolve) => {
          spoken.push({ text, priority });
          pending.push(resolve);
        }),
      () => {
        interrupted += 1;
        // Copilot.cancel settles every pending line as not heard.
        pending.splice(0).forEach((resolve) => resolve(false));
      }
    );

    queue.enqueue('mission.scan_start', () => 'scan');
    queue.enqueue('mission.target_found', () => 'target');
    queue.enqueue('mission.approaching', () => 'approach');
    await flush();
    expect(spoken.map((line) => line.text)).toEqual(['scan']);

    queue.preempt('mission.failsafe', () => 'Alerta de voo: odometria perdida.', 'URGENT');
    await flush();

    expect(interrupted).toBe(1);
    expect(spoken).toEqual([
      { text: 'scan', priority: 'NORMAL' },
      { text: 'Alerta de voo: odometria perdida.', priority: 'URGENT' },
    ]);
    expect(queue.labels()).toEqual([]);

    // Narration after the alert resumes normally behind it.
    queue.enqueue('mission.landing', () => 'landing');
    pending.shift()!(true);
    await flush();
    expect(spoken.map((line) => line.text)).toEqual(['scan', 'Alerta de voo: odometria perdida.', 'landing']);
  });

  it('speaks an alert at once when nothing is playing, without interrupting anything', async () => {
    const spoken: string[] = [];
    let interrupted = 0;
    const queue = new NarrationQueue(
      async (text) => {
        spoken.push(text);
        return true;
      },
      () => {
        interrupted += 1;
      }
    );
    queue.preempt('mission.abort', () => 'Missão abortada, pousando drone', 'URGENT');
    await flush();
    expect(spoken).toEqual(['Missão abortada, pousando drone']);
    expect(interrupted).toBe(0);
  });

  it('never cuts one alert with another, and keeps them in order ahead of narration', async () => {
    const spoken: string[] = [];
    const pending: Array<(ok: boolean) => void> = [];
    let interrupted = 0;
    const queue = new NarrationQueue(
      (text) =>
        new Promise<boolean>((resolve) => {
          spoken.push(text);
          pending.push(resolve);
        }),
      () => {
        interrupted += 1;
        pending.splice(0).forEach((resolve) => resolve(false));
      }
    );
    queue.preempt('mission.step_failed', () => 'first', 'URGENT');
    await flush();
    queue.enqueue('mission.landing', () => 'landing');
    queue.preempt('mission.abort', () => 'second', 'URGENT');
    await flush();

    expect(interrupted).toBe(0);
    expect(spoken).toEqual(['first']);
    expect(queue.labels()).toEqual(['mission.abort']);

    pending.shift()!(true);
    await flush();
    expect(spoken).toEqual(['first', 'second']);
  });
});

describe('phraseForMilestone', () => {
  const noStorage: { storage: StorageLike | null; random: () => number } = {
    storage: null,
    random: () => 0,
  };

  it('prefers the altitude the mission reports over the configured fallback', () => {
    const line = phraseForMilestone('mission.takeoff', { altitude_m: 1.4 }, 3, noStorage);
    expect(line).toContain('1,4 metro');
  });

  it('falls back to the configured altitude when the payload has none', () => {
    expect(phraseForMilestone('mission.takeoff', {}, 2.5, noStorage)).toContain('2,5 metros');
  });

  it('places the target from the payload geometry', () => {
    const line = phraseForMilestone(
      'mission.target_found',
      { forward_m: 2.7, bearing_deg: -12 },
      1,
      noStorage
    );
    expect(line).toContain('a 2,7 metros à frente, à esquerda');
  });

  it('never leaves a placeholder in the spoken line', () => {
    for (const key of ['mission.takeoff', 'mission.target_found', 'mission.landing'] as const) {
      expect(phraseForMilestone(key, {}, Number.NaN, noStorage)).not.toMatch(/\{[a-z_]+\}/);
    }
  });
});

describe('early detection on the bench, replayed', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A synthesized line takes about this long to be read aloud. */
  const LINE_MS = 2500;

  it('speaks every earlier call in full, in order, before the detection that overtook them', async () => {
    vi.useFakeTimers();
    const spoken: Array<{ key: string; start: number; end: number }> = [];
    const arrived = new Map<string, number>();
    let speaking: string | null = null;

    const voice = (text: string) =>
      new Promise<boolean>((resolve) => {
        const start = Date.now();
        const key = speaking ?? text;
        setTimeout(() => {
          spoken.push({ key, start, end: Date.now() });
          resolve(true);
        }, LINE_MS);
      });
    const queue = new NarrationQueue(voice);
    const heard = new Set<string>();
    const onMilestone = (message: { key: string; payload: Record<string, unknown> }) => {
      if (!isMilestoneKey(message.key) || heard.has(message.key)) return;
      const key = message.key;
      heard.add(key);
      arrived.set(key, Date.now());
      queue.enqueue(key, () => {
        speaking = key;
        return phraseForMilestone(key, message.payload, 1, { storage: null });
      });
    };

    const t0 = Date.now();
    // The recorded bench run launched with no countdown, as bench runs do.
    scheduleScriptMilestones(0, onMilestone);
    const parser = createMilestoneParser(onMilestone);
    for (const { t_ms, line } of trace.lines) {
      setTimeout(() => parser.push(`${line}\n`), t_ms);
    }
    await vi.advanceTimersByTimeAsync(30_000);

    const order = [
      'mission.start',
      'mission.countdown_3',
      'mission.takeoff',
      'mission.scan_start',
      'mission.target_found',
      'mission.approaching',
    ];
    expect(spoken.map((line) => line.key)).toEqual(order);
    for (let index = 1; index < spoken.length; index++) {
      expect(spoken[index].start).toBeGreaterThanOrEqual(spoken[index - 1].end);
    }

    // The scenario is real: the detection arrived while the voice was still
    // several lines behind, and was held rather than spoken over them.
    const scan = spoken.find((line) => line.key === 'mission.scan_start')!;
    const target = spoken.find((line) => line.key === 'mission.target_found')!;
    expect(arrived.get('mission.target_found')!).toBeLessThan(scan.end);
    expect(target.start).toBeGreaterThanOrEqual(scan.end);
    expect(arrived.get('mission.target_found')! - t0).toBeLessThan(LINE_MS * 2);
  });
});
