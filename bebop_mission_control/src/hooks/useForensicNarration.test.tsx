// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PHRASE_POOLS } from '../lib/copilotPhrases';
import type { Finding, ForensicScript } from '../lib/forensics';
import { VICTIM_WORDINGS, scriptLines } from '../lib/forensics';
import { NarrationQueue } from '../lib/narrationQueue';
import type { InspectionAssessment, RawEvidence, VictimStatus } from '../types/bmg';
import { useForensicNarration } from './useCopilot';
import { ASSESSMENT_GROUND_WAIT_MS, useForensicReport, type ForensicReportState } from './useForensicReport';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const REPORT: Finding[] = [
  { topic: 'vehicle', card: 'Veículo não danificado', speech: 'Para começar, veículo não danificado.' },
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
const seen = { revealed: 0, closing: null as string | null };

function Report({ queue, active, report }: { queue: NarrationQueue; active: boolean; report: ForensicScript | null }) {
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

function assessmentFor(status: VictimStatus | 'unavailable'): InspectionAssessment {
  const detected = status === 'detected';
  return {
    schema: 'bmg.inspection.v1',
    capture_stamp: '20261020_101500',
    status: status === 'unavailable' ? 'unavailable' : 'complete',
    reason: status === 'unavailable' ? 'detector_error' : null,
    // A malformed unavailable payload claiming a victim must still read as unknown.
    victim_detected: detected || status === 'unavailable',
    victim_count: detected || status === 'unavailable' ? 1 : 0,
    victim_confidence: detected ? 0.62 : null,
    victim_boxes: detected ? [[100, 100, 160, 220]] : [],
    frame: [856, 480],
    inference_ms: 212.3,
    model: 'yolov8n.pt',
    imgsz: 640,
    conf: 0.35,
    completed_at_utc: '2026-10-20T13:15:01+00:00',
  };
}

const flow = { state: null as ForensicReportState | null, prepared: [] as string[] };

function Flow({
  queue,
  capture,
  assessment,
  grounded,
}: {
  queue: NarrationQueue;
  capture: RawEvidence | null;
  assessment: InspectionAssessment | null;
  grounded: boolean;
}) {
  const state = useForensicReport({
    capture,
    assessment,
    grounded,
    prepare: (text) => flow.prepared.push(text),
  });
  const { revealed } = useForensicNarration(queue, grounded && Boolean(capture), state.script);
  flow.state = state;
  seen.revealed = revealed;
  return null;
}

const victimCard = () => flow.state?.script?.findings.find((finding) => finding.topic === 'victim')?.card;

describe('useForensicReport', () => {
  beforeEach(() => {
    flow.state = null;
    flow.prepared = [];
  });

  it.each([
    ['detected', 'detected'],
    ['not_detected', 'not_detected'],
    ['unavailable', 'unknown'],
  ] as const)('draws on a %s assessment and reads the %s victim line', async (status, pool) => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () =>
      root.render(<Flow queue={queue} capture={CAPTURE} assessment={null} grounded={false} />)
    );
    expect(flow.state?.analyzing).toBe(true);
    expect(flow.prepared).toEqual([]);

    await act(async () =>
      root.render(<Flow queue={queue} capture={CAPTURE} assessment={assessmentFor(status)} grounded={false} />)
    );
    const script = flow.state?.script as ForensicScript;
    expect(flow.state?.analyzing).toBe(false);
    expect(script.victimStatus).toBe(pool);
    expect(VICTIM_WORDINGS[pool]).toContain(victimCard());
    expect(flow.prepared).toEqual(scriptLines(script));
    expect(v.spoken).toEqual([]);

    await act(async () =>
      root.render(<Flow queue={queue} capture={CAPTURE} assessment={assessmentFor(status)} grounded />)
    );
    expect(v.spoken).toEqual([script.intro]);
    for (const finding of script.findings) {
      await act(async () => v.finish());
      await tick(0);
      expect(v.spoken[v.spoken.length - 1]).toBe(finding.speech);
    }
  });

  it('reads nothing while the assessment is pending, then the neutral line once the wait runs out', async () => {
    const v = voice();
    const queue = new NarrationQueue(v.say, v.cancel);
    await act(async () => root.render(<Flow queue={queue} capture={CAPTURE} assessment={null} grounded />));

    await tick(ASSESSMENT_GROUND_WAIT_MS - 1);
    expect(flow.state?.analyzing).toBe(true);
    expect(flow.state?.script).toBeNull();
    expect(v.spoken).toEqual([]);

    await tick(1);
    const script = flow.state?.script as ForensicScript;
    expect(script.victimStatus).toBe('unknown');
    expect(script.reason).toBe('timeout');
    expect(VICTIM_WORDINGS.unknown).toContain(victimCard());
    expect(flow.prepared).toEqual(scriptLines(script));
    expect(v.spoken).toEqual([script.intro]);
  });

  it('does not start the wait while the aircraft is still flying', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Flow queue={queue} capture={CAPTURE} assessment={null} grounded={false} />));
    await tick(ASSESSMENT_GROUND_WAIT_MS * 3);
    expect(flow.state?.script).toBeNull();
    expect(flow.state?.analyzing).toBe(true);
  });

  it('a result arriving after the wait ran out does not redraw the report', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    await act(async () => root.render(<Flow queue={queue} capture={CAPTURE} assessment={null} grounded />));
    await tick(ASSESSMENT_GROUND_WAIT_MS);
    const drawn = flow.state?.script;
    await act(async () =>
      root.render(<Flow queue={queue} capture={CAPTURE} assessment={assessmentFor('detected')} grounded />)
    );
    expect(flow.state?.script).toBe(drawn);
    expect(flow.state?.script?.victimStatus).toBe('unknown');
  });

  it('alternates the opening topic from one flight to the next', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    const leads: string[] = [];
    for (let flight = 0; flight < 4; flight++) {
      const capture = { ...CAPTURE, timestamp: flight };
      await act(async () => root.render(<Flow queue={queue} capture={null} assessment={null} grounded={false} />));
      await act(async () =>
        root.render(<Flow queue={queue} capture={capture} assessment={assessmentFor('detected')} grounded={false} />)
      );
      leads.push(flow.state?.script?.findings[0].topic as string);
    }
    expect(leads[0]).not.toBe(leads[1]);
    expect(leads[1]).not.toBe(leads[2]);
    expect(leads[2]).not.toBe(leads[3]);
  });

  it('shows on the card exactly the words it speaks', async () => {
    const queue = new NarrationQueue(async () => false, () => undefined);
    for (const status of ['detected', 'not_detected', 'unavailable'] as const) {
      await act(async () => root.render(<Flow queue={queue} capture={null} assessment={null} grounded={false} />));
      await act(async () =>
        root.render(<Flow queue={queue} capture={CAPTURE} assessment={assessmentFor(status)} grounded={false} />)
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
