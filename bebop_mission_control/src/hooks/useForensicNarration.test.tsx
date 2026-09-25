// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PHRASE_POOLS } from '../lib/copilotPhrases';
import type { Finding } from '../lib/forensics';
import { NarrationQueue } from '../lib/narrationQueue';
import { useForensicNarration } from './useCopilot';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const REPORT: Finding[] = [
  { topic: 'police', card: 'Sem necessidade de polícia', speech: 'Para começar, sem necessidade de polícia.' },
  { topic: 'vehicle', card: 'Veículo não danificado', speech: 'Além disso, veículo não danificado.' },
  { topic: 'victim', card: 'Estado do acidentado não é grave', speech: 'Na sequência, estado do acidentado não é grave.' },
  { topic: 'samu', card: 'Sem necessidade de Samu', speech: 'Por fim, sem necessidade de Samu.' },
];

/** A voice the test finishes line by line. */
function voice() {
  const spoken: string[] = [];
  const pending: Array<(ok: boolean) => void> = [];
  return {
    spoken,
    say: (text: string) =>
      new Promise<boolean>((resolve) => {
        spoken.push(text);
        pending.push(resolve);
      }),
    cancel: () => pending.splice(0).forEach((resolve) => resolve(false)),
    finish: () => pending.shift()?.(true),
  };
}

let container: HTMLDivElement;
let root: Root;
const seen = { revealed: 0, closing: null as string | null };

function Report({ queue, active, report }: { queue: NarrationQueue; active: boolean; report: Finding[] | null }) {
  const { revealed, closing } = useForensicNarration(queue, active, report);
  seen.revealed = revealed;
  seen.closing = closing;
  return null;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  // Pins the beat at its 1.8 s minimum and the phrase draws to the first variant.
  vi.spyOn(Math, 'random').mockReturnValue(0);
  window.localStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const tick = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

describe('useForensicNarration', () => {
  it('reveals each card only when its own sentence has been read, intro first and outro last', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () => root.render(<Report queue={queue} active report={REPORT} />));

    expect(PHRASE_POOLS['inspection.intro']).toContain(v.spoken[0]);
    await tick(5000);
    expect(seen.revealed).toBe(0);

    await act(async () => v.finish());
    await tick(0);
    for (let index = 0; index < REPORT.length; index++) {
      expect(v.spoken[v.spoken.length - 1]).toBe(REPORT[index].speech);
      await tick(5000);
      expect(seen.revealed, `card ${index + 1} must wait for its sentence`).toBe(index);
      await act(async () => v.finish());
      await tick(0);
      expect(seen.revealed).toBe(index + 1);
    }

    const outro = v.spoken[v.spoken.length - 1];
    expect(PHRASE_POOLS['inspection.outro']).toContain(outro);
    expect(seen.closing).toBe(outro);
  });

  it('keeps the two-second floor when the copilot answers at once', async () => {
    const queue = new NarrationQueue(async () => true, () => undefined);
    await act(async () => root.render(<Report queue={queue} active report={REPORT} />));

    await tick(1799);
    expect(seen.revealed).toBe(0);
    await tick(1800);
    expect(seen.revealed).toBe(0);
    await tick(1);
    expect(seen.revealed).toBe(1);
    await tick(1800 * 3);
    expect(seen.revealed).toBe(4);
  });

  it('queues behind the flight narration instead of talking over it', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    queue.enqueue('touchdown', () => 'Pouso seguro concluído com sucesso na base.');
    await act(async () => root.render(<Report queue={queue} active report={REPORT} />));

    expect(v.spoken).toEqual(['Pouso seguro concluído com sucesso na base.']);
    await act(async () => v.finish());
    await tick(0);
    expect(PHRASE_POOLS['inspection.intro']).toContain(v.spoken[1]);
  });

  it('drops its remaining lines when the report is dismissed, and nothing else', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () => root.render(<Report queue={queue} active report={REPORT} />));
    await act(async () => v.finish());
    await tick(2000);
    expect(v.spoken).toHaveLength(2);

    queue.enqueue('mission.landing', () => 'flight line');
    await act(async () => root.render(<Report queue={queue} active={false} report={REPORT} />));
    await tick(10);

    expect(seen.revealed).toBe(0);
    expect(v.spoken[v.spoken.length - 1]).toBe('flight line');
    expect(queue.labels()).toEqual([]);
  });
});
