// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PHRASE_POOLS } from '../lib/copilotPhrases';
import type { Finding, ForensicScript } from '../lib/forensics';
import { VICTIM_WORDINGS, scriptLines } from '../lib/forensics';
import { NarrationQueue } from '../lib/narrationQueue';
import type { RawEvidence } from '../types/bmg';
import { TYPE_FINISH_MS, useForensicNarration, type FindingReading } from './useCopilot';
import { SPEECH_MS_KEY, estimateSpeechMs } from '../lib/speechTiming';
import { useForensicReport, type ForensicReportState } from './useForensicReport';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const REPORT: Finding[] = [
  {
    topic: 'vehicle',
    card: 'Veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via',
    speech: 'Para começar, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via.',
  },
  {
    topic: 'victim',
    card: 'Vítima consciente e sem ferimentos aparentes',
    speech: 'Além disso, vítima consciente e sem ferimentos aparentes.',
  },
  { topic: 'police', card: 'Sem necessidade de polícia', speech: 'Na sequência, sem necessidade de polícia.' },
  { topic: 'samu', card: 'Sem necessidade de Samu', speech: 'Por fim, sem necessidade de Samu.' },
];

const SCRIPT: ForensicScript = {
  captureStamp: '20261020_101500',
  victimStatus: 'detected',
  reason: null,
  intro: PHRASE_POOLS['inspection.intro'][0],
  findings: REPORT,
  outro: PHRASE_POOLS['inspection.outro'][0],
};

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
const seen = {
  revealed: 0,
  announced: 0,
  readings: [] as ReadonlyArray<FindingReading | undefined>,
  closing: null as string | null,
};

function Report({ queue, active, report }: { queue: NarrationQueue; active: boolean; report: ForensicScript | null }) {
  const { revealed, announced, readings, closing } = useForensicNarration(queue, active, report);
  seen.revealed = revealed;
  seen.announced = announced;
  seen.readings = readings;
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
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));

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

  it('shows each card as its sentence starts and ends its text when the voice stops', async () => {
    const pending: Array<{ resolve: (ok: boolean) => void; onAudio?: (ms: number | null) => void }> = [];
    const spoken: string[] = [];
    const queue = new NarrationQueue(
      (text, _priority, onAudio) =>
        new Promise<boolean>((resolve) => {
          spoken.push(text);
          pending.push({ resolve, onAudio });
        })
    );
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));
    await act(async () => pending.shift()?.resolve(true));
    await tick(0);

    // Handed to the voice: the card is up, its text paces on the expected speech length.
    expect(spoken[spoken.length - 1]).toBe(REPORT[0].speech);
    expect(seen.announced).toBe(1);
    expect(seen.revealed).toBe(0);
    expect(seen.readings[0]?.endsAt).toBe(Date.now() + estimateSpeechMs(REPORT[0].speech));

    // The voice starts and says how long: the text is due when it stops.
    await tick(200);
    const startedAt = Date.now();
    await act(async () => pending[0].onAudio?.(2600));
    expect(seen.readings[0]?.endsAt).toBe(startedAt + 2600);

    await tick(2600);
    await act(async () => pending.shift()?.resolve(true));
    await tick(0);
    expect(seen.revealed).toBe(1);
    expect(seen.readings[0]?.endsAt, 'a voice on time leaves the pace alone').toBe(startedAt + 2600);
    expect(seen.announced).toBe(2);

    // A voice that stops early: what is left eases in over TYPE_FINISH_MS.
    await act(async () => pending[0].onAudio?.(5000));
    await tick(1000);
    await act(async () => pending.shift()?.resolve(true));
    await tick(0);
    expect(seen.readings[1]?.endsAt).toBe(Date.now() + TYPE_FINISH_MS);

    // Heard sentences are measured from the moment they began to sound.
    const store = JSON.parse(window.localStorage.getItem(SPEECH_MS_KEY) ?? '{}') as Record<string, number>;
    expect(store[REPORT[0].speech]).toBe(2600);
    expect(store[REPORT[1].speech]).toBe(1000);
  });

  it('paces an unknown length on the last measurement of that sentence', async () => {
    const pending: Array<{ resolve: (ok: boolean) => void; onAudio?: (ms: number | null) => void }> = [];
    window.localStorage.setItem(SPEECH_MS_KEY, JSON.stringify({ [REPORT[0].speech]: 3100 }));
    const queue = new NarrationQueue(
      (_text, _priority, onAudio) => new Promise<boolean>((resolve) => pending.push({ resolve, onAudio }))
    );
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));
    await act(async () => pending.shift()?.resolve(true));
    await tick(0);
    await act(async () => pending[0].onAudio?.(null));
    expect(seen.readings[0]?.endsAt).toBe(Date.now() + 3100);
  });

  it('types a silent report over its fallback beat', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));
    await tick(1800);
    expect(seen.announced).toBe(1);
    expect(seen.readings[0]?.endsAt).toBe(Date.now() + 1800);
  });

  it('keeps the two-second floor when the copilot cannot speak', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));

    await tick(1799);
    expect(seen.revealed).toBe(0);
    await tick(1800);
    expect(seen.revealed).toBe(0);
    await tick(1);
    expect(seen.revealed).toBe(1);
    await tick(1800 * 3);
    expect(seen.revealed).toBe(4);
  });

  it('pads nothing after a finding the copilot actually read', async () => {
    const queue = new NarrationQueue(
      () => new Promise<boolean>((resolve) => setTimeout(() => resolve(true), 900)),
      () => undefined
    );
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));
    // Intro 900 ms, then each finding 900 ms: no 1.8 s floor on heard lines.
    await tick(900 * 2);
    expect(seen.revealed).toBe(1);
    await tick(900 * 3);
    expect(seen.revealed).toBe(4);
  });

  it('queues behind the flight narration instead of talking over it', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    queue.enqueue('touchdown', () => 'Pouso seguro concluído com sucesso na base.');
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));

    expect(v.spoken).toEqual(['Pouso seguro concluído com sucesso na base.']);
    await act(async () => v.finish());
    await tick(0);
    expect(PHRASE_POOLS['inspection.intro']).toContain(v.spoken[1]);
  });

  it('drops its remaining lines when the report is dismissed, and nothing else', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () => root.render(<Report queue={queue} active report={SCRIPT} />));
    await act(async () => v.finish());
    await tick(2000);
    expect(v.spoken).toHaveLength(2);

    queue.enqueue('mission.landing', () => 'flight line');
    await act(async () => root.render(<Report queue={queue} active={false} report={SCRIPT} />));
    await tick(10);

    expect(seen.revealed).toBe(0);
    expect(v.spoken[v.spoken.length - 1]).toBe('flight line');
    expect(queue.labels()).toEqual([]);
  });
});

// ------------------------------------------------ the report of one capture

const CAPTURE: RawEvidence = {
  filename: 'accident_raw_20261020_101500.png',
  url: 'file:///out/accident_raw_20261020_101500.png',
  timestamp: 1,
};

const flow = { state: null as ForensicReportState | null, prepared: [] as string[] };

function Flow({
  queue,
  runToken,
  victimDetection,
  grounded,
}: {
  queue: NarrationQueue;
  runToken: string | null;
  victimDetection: boolean;
  grounded: boolean;
}) {
  const state = useForensicReport({
    runToken,
    victimDetection,
    capture: runToken === null ? null : CAPTURE,
    prepare: (text) => flow.prepared.push(text),
  });
  const { revealed } = useForensicNarration(queue, grounded && runToken !== null, state.script);
  flow.state = state;
  seen.revealed = revealed;
  return null;
}

const victimCard = () => flow.state?.script?.findings.find((finding) => finding.topic === 'victim')?.card;
const topics = () => (flow.state?.script?.findings ?? []).map((finding) => finding.topic);

describe('useForensicReport', () => {
  beforeEach(() => {
    flow.state = null;
    flow.prepared = [];
  });

  it('draws nothing until there is a flight to inspect', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Flow queue={queue} runToken={null} victimDetection grounded={false} />));
    expect(flow.state?.script).toBeNull();
    expect(flow.prepared).toEqual([]);
  });

  it('switch on: a victim, conscious and unhurt, read first, every flight, with no detector', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    for (let flight = 0; flight < 4; flight++) {
      await act(async () => root.render(<Flow queue={queue} runToken={`run-${flight}`} victimDetection grounded={false} />));
      const script = flow.state?.script as ForensicScript;
      expect(script.findings).toHaveLength(4);
      expect(script.victimStatus).toBe('detected');
      expect(VICTIM_WORDINGS.detected).toContain(victimCard());
      expect(topics().slice(0, 2)).toEqual(['victim', 'vehicle']);
      expect(new Set(topics())).toEqual(new Set(['victim', 'vehicle', 'samu', 'police']));
    }
  });

  it('switch off: the vehicle first, then no visible victim, every flight', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    for (let flight = 0; flight < 4; flight++) {
      await act(async () =>
        root.render(<Flow queue={queue} runToken={`run-${flight}`} victimDetection={false} grounded={false} />)
      );
      const script = flow.state?.script as ForensicScript;
      expect(script.findings).toHaveLength(4);
      expect(script.victimStatus).toBe('not_detected');
      expect(script.reason).toBe('victim_detection_disabled');
      expect(VICTIM_WORDINGS.not_detected).toContain(victimCard());
      expect(topics().slice(0, 2)).toEqual(['vehicle', 'victim']);
    }
  });

  it('draws once per flight and hands all six lines to the copilot', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Flow queue={queue} runToken="run-a" victimDetection grounded={false} />));
    const drawn = flow.state?.script as ForensicScript;
    expect(flow.prepared).toEqual(scriptLines(drawn));
    await act(async () => root.render(<Flow queue={queue} runToken="run-a" victimDetection={false} grounded={false} />));
    expect(flow.state?.script).toBe(drawn);
    await act(async () => root.render(<Flow queue={queue} runToken="run-b" victimDetection grounded={false} />));
    expect(flow.state?.script).not.toBe(drawn);
  });

  it('reads it only on confirmed ground, intro first, each finding in turn', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () => root.render(<Flow queue={queue} runToken="run-a" victimDetection grounded={false} />));
    expect(v.spoken).toEqual([]);
    const script = flow.state?.script as ForensicScript;
    await act(async () => root.render(<Flow queue={queue} runToken="run-a" victimDetection grounded />));
    expect(v.spoken).toEqual([script.intro]);
    for (const finding of script.findings) {
      await act(async () => v.finish());
      await tick(0);
      expect(v.spoken[v.spoken.length - 1]).toBe(finding.speech);
    }
  });

  it('alternates SAMU and police from one flight to the next', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    const tails: string[] = [];
    for (let flight = 0; flight < 4; flight++) {
      await act(async () => root.render(<Flow queue={queue} runToken={`run-${flight}`} victimDetection grounded={false} />));
      tails.push(topics().slice(2).join(','));
    }
    for (let flight = 1; flight < 4; flight++) expect(tails[flight]).not.toBe(tails[flight - 1]);
  });

  it('shows on the card exactly the words it speaks', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    for (const victim of [true, false]) {
      await act(async () =>
        root.render(<Flow queue={queue} runToken={`run-${victim}`} victimDetection={victim} grounded={false} />)
      );
      for (const finding of flow.state?.script?.findings ?? []) {
        const decapitalised = /^.[A-Z]/.test(finding.card)
          ? finding.card
          : finding.card.charAt(0).toLowerCase() + finding.card.slice(1);
        expect(finding.speech.endsWith(` ${decapitalised}.`)).toBe(true);
      }
    }
  });

  it('never states anything about the victim in the neutral pool', () => {
    const fold = (text: string) =>
      text
        .normalize('NFKD')
        .replace(/[̀-ͯ]/g, '')
        .toLowerCase();
    const stems = ['conscien', 'ferim', 'lesã', 'nenhum', 'sem vítimas', 'ausência', 'identificad', 'visível', 'detect'];
    for (const line of VICTIM_WORDINGS.unknown) {
      for (const stem of stems) expect(fold(line)).not.toContain(fold(stem));
    }
  });
});
