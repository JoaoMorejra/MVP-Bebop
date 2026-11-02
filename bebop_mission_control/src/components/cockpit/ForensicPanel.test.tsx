// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ForensicPanel } from './ForensicPanel';
import { GRID_TEMPO, PHOTO_IN_MS } from './EvidenceGrid';
import { isAirborne } from '../../lib/flightState';
import { FINISH_HOLD_MS, FINISH_REASONS, type FinishLockResult } from '../../lib/finishLock';
import { TOPIC_LABEL, buildForensicReport, type Finding, type FindingTopic } from '../../lib/forensics';
import type { InspectionAssessment, RawEvidence } from '../../types/bmg';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const LOCKED: FinishLockResult = {
  state: 'no_mission',
  enabled: false,
  reason: FINISH_REASONS.noMission,
  requiresConfirm: false,
};
const GROUNDED: FinishLockResult = { state: 'grounded', enabled: true, reason: '', requiresConfirm: false };
const LINK_LOST: FinishLockResult = {
  state: 'link_lost',
  enabled: true,
  reason: FINISH_REASONS.linkLost,
  requiresConfirm: true,
};

/** Let the photograph come over the grid and the panel settle (real timers). */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, PHOTO_IN_MS + 40));
  });

function render(lock: FinishLockResult, onFinish = vi.fn()) {
  act(() =>
    root.render(
      <ForensicPanel
        latest={null}
        count={0}
        stage={0}
        missionOver={false}
        landed={false}
        report={null}
        reportRevealed={0}
        onFinish={onFinish}
        lock={lock}
      />
    )
  );
  const button = container.querySelector('button[data-finish-state]') as HTMLButtonElement;
  return { button, onFinish };
}

describe('Finalizar missão', () => {
  it.each([
    ['no_mission', LOCKED],
    ['in_flight', { state: 'in_flight', enabled: false, reason: FINISH_REASONS.inFlight, requiresConfirm: false }],
    ['await_ground', { state: 'await_ground', enabled: false, reason: FINISH_REASONS.awaitGround, requiresConfirm: false }],
    ['aborting', { state: 'aborting', enabled: false, reason: FINISH_REASONS.landing, requiresConfirm: false }],
  ] as const)('is not shown while it cannot finish (%s)', (_state, lock) => {
    const { button } = render(lock as FinishLockResult);
    expect(button).toBeNull();
    expect(container.textContent).not.toContain('Finalizar missão');
  });

  it('appears in the bottom-right corner once it can', () => {
    const { button } = render(GROUNDED);
    expect(button.className).toContain('bottom-3');
    expect(button.className).toContain('right-3');
  });

  it('ends the cycle once the aircraft is confirmed on the ground', () => {
    const { button, onFinish } = render(GROUNDED);
    expect(button.disabled).toBe(false);
    act(() => button.click());
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  describe('on a lost link', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const press = (button: HTMLButtonElement, type: string) =>
      act(() => {
        button.dispatchEvent(new Event(type, { bubbles: true }));
      });

    it('a click does nothing; only a press held for the full duration finishes', () => {
      const { button, onFinish } = render(LINK_LOST);
      expect(button.title).toBe('');
      expect(button.getAttribute('data-finish-reason')).toBe(FINISH_REASONS.linkLost);
      expect(button.textContent?.trim()).toBe('Segure para finalizar');
      act(() => button.click());
      expect(onFinish).not.toHaveBeenCalled();

      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS - 1));
      expect(onFinish).not.toHaveBeenCalled();
      act(() => vi.advanceTimersByTime(1));
      expect(onFinish).toHaveBeenCalledTimes(1);
    });

    it('releasing early cancels the hold', () => {
      const { button, onFinish } = render(LINK_LOST);
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS / 2));
      press(button, 'pointerup');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS));
      expect(onFinish).not.toHaveBeenCalled();
    });

    it('pointer-down repeated within 50 ms finishes once, on the last press held', () => {
      const { button, onFinish } = render(LINK_LOST);
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(25));
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(25));
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS * 3));
      expect(onFinish).toHaveBeenCalledTimes(1);
    });
  });
});

describe('isAirborne', () => {
  it('reads the canonical airborne set: rotors turning, usertakeoff excluded', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8].filter(isAirborne)).toEqual([1, 2, 3, 4, 7, 8]);
    expect(isAirborne(null)).toBe(false);
    expect(isAirborne(undefined)).toBe(false);
  });
});

describe('rotorsTurning', () => {
  it('turns the rotors only in the ARSDK states where they spin', async () => {
    const { rotorsTurning } = await import('../../lib/flightState');
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8].filter(rotorsTurning)).toEqual([1, 2, 3, 4, 7, 8]);
    expect(rotorsTurning(null)).toBe(false);
    expect(rotorsTurning(undefined)).toBe(false);
  });
});

describe('the report waiting for the victim assessment', () => {
  const LATEST = { filename: 'accident_raw_20261020_101500.png', url: 'file:///x.png', timestamp: 1 };

  function renderReport(props: { report: { topic: 'victim'; card: string; speech: string }[] | null; analyzing: boolean }) {
    act(() =>
      root.render(
        <ForensicPanel
          latest={LATEST}
          count={1}
          stage={5}
          missionOver
          landed
          report={props.report}
          reportRevealed={0}
          reportAnalyzing={props.analyzing}
          onFinish={vi.fn()}
          lock={GROUNDED}
        />
      )
    );
  }

  it('holds a placeholder victim card with no finding text while analyzing', async () => {
    renderReport({ report: null, analyzing: true });
    await settle();
    const card = container.querySelector('[data-finding-state="analyzing"]');
    expect(card?.textContent).toContain('Analisando imagem');
    expect(card?.textContent).toContain('Avaliação da vítima');
    expect(container.querySelector('ul')?.getAttribute('aria-busy')).toBe('true');
  });

  it('replaces the placeholder with the drawn findings', async () => {
    renderReport({
      report: [{ topic: 'victim', card: 'Cena sem vítimas aparentes', speech: 'Por fim, cena sem vítimas aparentes.' }],
      analyzing: false,
    });
    await settle();
    expect(container.querySelector('[data-finding-state="analyzing"]')).toBeNull();
    expect(container.textContent).toContain('Cena sem vítimas aparentes');
  });

  it('shows no placeholder before the aircraft is down', () => {
    act(() =>
      root.render(
        <ForensicPanel
          latest={LATEST}
          count={1}
          stage={5}
          missionOver={false}
          landed={false}
          report={null}
          reportRevealed={0}
          reportAnalyzing
          onFinish={vi.fn()}
          lock={LOCKED}
        />
      )
    );
    expect(container.querySelector('[data-finding-state="analyzing"]')).toBeNull();
  });
});

describe('the report inside the cockpit', () => {
  const LATEST: RawEvidence = {
    filename: 'accident_raw_20261020_113500.png',
    url: '/evidence/accident_raw_20261020_113500.png',
    timestamp: 0,
    occurrenceId: 'OC_20261020_143500',
  };

  function assessment(detected: boolean): InspectionAssessment {
    return {
      schema: 'bmg.inspection.v1',
      capture_stamp: '20261020_113500',
      status: 'complete',
      reason: null,
      victim_detected: detected,
      victim_count: detected ? 1 : 0,
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

  async function renderReport(report: Finding[], revealed: number) {
    await act(async () =>
      root.render(
        <ForensicPanel
          latest={LATEST}
          count={1}
          stage={5}
          missionOver
          landed
          report={report}
          reportRevealed={revealed}
          onFinish={vi.fn()}
          lock={GROUNDED}
        />
      )
    );
    await settle();
  }

  const cardTopics = (): FindingTopic[] =>
    Array.from(container.querySelectorAll('ul > li'))
      .map((item) => (Object.keys(TOPIC_LABEL) as FindingTopic[]).find((topic) => item.textContent?.includes(TOPIC_LABEL[topic])))
      .filter((topic): topic is FindingTopic => topic !== undefined);

  afterEach(() => {
    delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
  });

  it('offers no HTML report entry point after the last finding', async () => {
    const report = buildForensicReport({ storage: null });
    await renderReport(report, report.length);
    expect(container.querySelector('[data-testid="open-report"]')).toBeNull();
    expect(container.textContent).not.toMatch(/Visualizar Laudo Pericial/);
  });

  it('never asks the host for an HTML report', async () => {
    const removed = [['get', 'ReportUrl'].join(''), ['on', 'ReportReady'].join('')];
    const spies = removed.map(() => vi.fn(async () => null));
    (window as unknown as { bmgAPI: unknown }).bmgAPI = Object.fromEntries(
      removed.map((name, index) => [name, spies[index]])
    );
    const report = buildForensicReport({ storage: null });
    await renderReport(report, report.length);
    spies.forEach((spy) => expect(spy).not.toHaveBeenCalled());
  });

  it.each([
    ['B: victim first, detected', true, ['victim', 'vehicle']],
    ['A: vehicle first, not detected', false, ['vehicle', 'victim']],
  ] as const)('shows the four cards in the drawn order (%s)', async (_label, detected, lead) => {
    const report = buildForensicReport({ assessment: assessment(detected), storage: null });
    await renderReport(report, report.length);
    const topics = cardTopics();
    expect(topics).toHaveLength(4);
    expect(topics).toEqual(report.map((finding) => finding.topic));
    expect(topics.slice(0, 2)).toEqual(lead);
    expect(new Set(topics.slice(2))).toEqual(new Set(['samu', 'police']));
  });
});

describe('the evidence from the flight to touchdown', () => {
  const CAPTURE: RawEvidence = {
    filename: 'accident_raw_20261029_111530.png',
    url: '/evidence/accident_raw_20261029_111530.png',
    timestamp: 0,
  };
  const FINDINGS: Finding[] = [
    { topic: 'vehicle', card: 'Veículo sem danos estruturais aparentes', speech: 'Para começar, veículo sem danos.' },
    { topic: 'victim', card: 'Vítima consciente', speech: 'Além disso, vítima consciente.' },
  ];

  function renderPanel(props: {
    latest?: RawEvidence | null;
    landed?: boolean;
    stage?: number;
    report?: Finding[] | null;
    revealed?: number;
    announced?: number;
    readings?: Array<{ endsAt: number | null } | undefined>;
    lock?: FinishLockResult;
  }) {
    act(() =>
      root.render(
        <ForensicPanel
          latest={props.latest ?? null}
          count={props.latest ? 1 : 0}
          stage={props.stage ?? 2}
          missionOver={false}
          landed={props.landed ?? false}
          report={props.report ?? null}
          reportRevealed={props.revealed ?? 0}
          reportAnnounced={props.announced}
          reportReadings={props.readings}
          flightProgress={0.5}
          waitProgress={0.4}
          onFinish={vi.fn()}
          lock={props.lock ?? LOCKED}
        />
      )
    );
  }

  const grid = () => container.querySelector('[data-evidence-grid]');

  it('waits as a grid under the reticle, with only its caption and no header', () => {
    renderPanel({});
    expect(grid()?.getAttribute('data-evidence-grid')).toBe('waiting');
    expect(container.querySelector('.evgrid-reticle')).not.toBeNull();
    expect(container.textContent?.trim()).toBe('Aguardando evidência');
    expect(container.querySelector('header')).toBeNull();
    expect(container.querySelector('button[data-finish-state]')).toBeNull();
  });

  it('says the aircraft is over the target', () => {
    renderPanel({ stage: 4 });
    expect(grid()?.hasAttribute('data-inspecting')).toBe(true);
  });

  it('forms the photograph out of the grid, with no time of capture before touchdown', () => {
    renderPanel({});
    renderPanel({ latest: CAPTURE });
    expect(grid()?.getAttribute('data-evidence-grid')).toBe('revealing');
    expect(grid()?.hasAttribute('data-shooting')).toBe(true);
    const photo = container.querySelector('.evgrid-photo');
    expect(photo?.getAttribute('src')).toBe(CAPTURE.url);
    expect(photo?.className).not.toContain('evgrid-photo-in');
    expect(container.querySelector('[data-capture-stamp]')).toBeNull();
    expect(container.querySelector('[data-scan-sweep]')).toBeNull();
  });

  it('runs the grid tempo 4x faster from the start of the flight', () => {
    expect(GRID_TEMPO).toBe(12);
  });

  it('reveals the photograph immediately in flight after capture and runs a minimalist scan until touchdown', async () => {
    renderPanel({});
    renderPanel({ latest: CAPTURE, landed: false });
    await settle();
    expect(grid()?.getAttribute('data-evidence-grid')).toBe('revealed');
    expect(container.querySelector('.evgrid-photo')?.className).toContain('evgrid-photo-in');
    const scan = container.querySelector('[data-scan-sweep]');
    expect(scan).not.toBeNull();
    expect(scan?.textContent).toBe('');
    expect(container.querySelector('[data-capture-stamp]')).toBeNull();
    expect(container.querySelector('[data-evidence-frame]')?.className).toContain('h-full');

    renderPanel({ latest: CAPTURE, landed: true, report: FINDINGS, revealed: 1, announced: 1, lock: GROUNDED });
    expect(container.querySelector('[data-scan-sweep]')).toBeNull();
    expect(container.querySelector('[data-capture-stamp]')?.textContent).toMatch(/capturada em/);
    expect(container.querySelector('[data-evidence-frame]')?.className).toContain('h-[38%]');
  });

  it('is whole on touchdown, then tells the time of capture', async () => {
    renderPanel({ latest: CAPTURE });
    renderPanel({ latest: CAPTURE, landed: true, lock: GROUNDED });
    expect(container.querySelector('.evgrid-photo')?.className).toContain('evgrid-photo-in');
    expect(container.querySelector('[data-capture-stamp]')).toBeNull();
    await settle();
    expect(grid()?.getAttribute('data-evidence-grid')).toBe('revealed');
    expect(container.querySelector('[data-capture-stamp]')?.textContent).toMatch(/capturada em/);
  });

  it('shows the cards in their boxes, typing the white sentence to end with the voice', async () => {
    renderPanel({ latest: CAPTURE, landed: true, report: FINDINGS, revealed: 0, announced: 1, readings: [{ endsAt: null }], lock: GROUNDED });
    await settle();
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'Date', 'setTimeout', 'clearTimeout'] });
    try {
      const cards = () => Array.from(container.querySelectorAll('ul > li'));
      expect(cards()[0].className).toContain('opacity-100');
      expect(cards()[0].className).toContain('rounded-bezel');
      expect(cards()[1].className).toContain('opacity-0');
      expect(cards()[0].querySelector('[data-finding-badge="vehicle"] .finding-badge-diamond')).not.toBeNull();
      expect(cards()[0].textContent).toContain('Análise do veículo');
      expect(cards()[0].textContent).not.toMatch(/\b0\d\b/);
      const typed = () => cards()[0].querySelector('[data-typing] > span:nth-child(2)')?.textContent ?? FINDINGS[0].card;
      expect(typed(), 'waits for the voice').toBe('');
      expect(cards()[0].querySelector('.sr-only')?.textContent).toBe(FINDINGS[0].card);

      const text = FINDINGS[0].card;
      renderPanel({
        latest: CAPTURE,
        landed: true,
        report: FINDINGS,
        revealed: 0,
        announced: 1,
        readings: [{ endsAt: Date.now() + 2000 }],
        lock: GROUNDED,
      });
      const lengths: number[] = [];
      for (let i = 0; i < 125; i += 1) {
        act(() => vi.advanceTimersByTime(16));
        lengths.push(typed().length);
      }
      expect(lengths.every((value, index) => index === 0 || value >= lengths[index - 1])).toBe(true);
      expect(Math.max(...lengths.slice(1).map((value, index) => value - lengths[index]))).toBeLessThanOrEqual(2);
      expect(lengths[60]).toBeGreaterThan(text.length * 0.4);
      expect(lengths[60]).toBeLessThan(text.length * 0.6);
      act(() => vi.advanceTimersByTime(32));
      expect(cards()[0].querySelector('[data-typing]')).toBeNull();
      expect(cards()[0].textContent).toContain(text);
    } finally {
      vi.useRealTimers();
    }
  });
});
