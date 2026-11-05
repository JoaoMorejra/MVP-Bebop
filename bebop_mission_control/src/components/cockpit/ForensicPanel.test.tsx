// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FINDING_BEAT_MS, ForensicPanel, findingSchedule } from './ForensicPanel';
import { GRID_TEMPO, PHOTO_IN_MS, REVEAL_MS, SCAN_FADE_MS, SCAN_MIN_PASS_MS, SHOT_MS, containRect, revealAmount } from './EvidenceGrid';
import { clearRevealed, recordRevealed } from '../../lib/evidenceReveal';
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
  clearRevealed();
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
        inspectionStarted={false}
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

  it('appears in the bottom-left corner once it can', () => {
    const { button } = render(GROUNDED);
    expect(button.className).toContain('bottom-3');
    expect(button.className).toContain('left-3');
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

describe('the inspection of every flight', () => {
  const FOUR: Finding[] = [
    { topic: 'vehicle', card: 'Veículo sem danos estruturais aparentes', speech: 'Para começar, veículo sem danos.' },
    { topic: 'victim', card: 'Cena sem vítimas aparentes', speech: 'Além disso, cena sem vítimas aparentes.' },
    { topic: 'samu', card: 'SAMU acionado', speech: 'O SAMU foi acionado.' },
    { topic: 'police', card: 'Polícia acionada', speech: 'Por fim, a polícia foi acionada.' },
  ];
  const CAPTURE: RawEvidence = { filename: 'accident_raw_20261105_101500.png', url: '/x.png', timestamp: 1 };
  const shownTopics = () =>
    Array.from(container.querySelectorAll('li.finding-shown'))
      .map((item) => (Object.keys(TOPIC_LABEL) as FindingTopic[]).find((topic) => item.textContent?.includes(TOPIC_LABEL[topic])))
      .filter((topic): topic is FindingTopic => topic !== undefined);

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** In flight: not landed, the mission still running, no voice, the finish locked. */
  function renderInFlight(props: { report: Finding[] | null; started: boolean; capture?: RawEvidence | null }) {
    act(() =>
      root.render(
        <ForensicPanel
          latest={props.capture === undefined ? CAPTURE : props.capture}
          count={1}
          stage={5}
          missionOver={false}
          landed={false}
          report={props.report}
          inspectionStarted={props.started}
          reportRevealed={0}
          onFinish={vi.fn()}
          lock={LOCKED}
        />
      )
    );
  }
  const advance = (ms: number) => act(() => vi.advanceTimersByTime(ms));
  /** The whole sequence: the last finding's start and its typing. */
  const sequenceMs = () => {
    const slots = findingSchedule(FOUR);
    return slots[slots.length - 1].startMs + slots[slots.length - 1].typeMs + 50;
  };

  it('shows no finding and no placeholder before the inspection starts', () => {
    renderInFlight({ report: FOUR, started: false });
    advance(30000);
    expect(container.querySelector('ul')).toBeNull();
    expect(container.querySelector('[data-finding-state="analyzing"]')).toBeNull();
  });

  it('brings in all four findings once it starts, in flight and with no voice', () => {
    renderInFlight({ report: FOUR, started: false });
    renderInFlight({ report: FOUR, started: true });
    expect(container.querySelector('[data-evidence-frame]')?.className).toContain('h-[38%]');
    advance(sequenceMs());
    expect(shownTopics()).toEqual(['vehicle', 'victim', 'samu', 'police']);
    expect(container.querySelector('[data-capture-stamp]')).not.toBeNull();
  });

  it('brings the findings in one at a time, each typed over its sentence, never all at once', () => {
    // A report of its own: its inspection starts now, as every flight's does.
    const report = FOUR.map((finding) => ({ ...finding }));
    renderInFlight({ report, started: true });
    const slots = findingSchedule(report);
    expect(slots[0].startMs).toBe(FINDING_BEAT_MS);
    advance(slots[0].startMs - 1);
    expect(shownTopics()).toHaveLength(0);
    advance(1);
    expect(shownTopics()).toEqual(['vehicle']);
    expect(container.querySelector('li.finding-shown [data-typing]')).not.toBeNull();
    for (let index = 1; index < report.length; index += 1) {
      advance(slots[index].startMs - slots[index - 1].startMs - 1);
      expect(shownTopics()).toHaveLength(index);
      advance(1);
      expect(shownTopics()).toHaveLength(index + 1);
      expect(slots[index].startMs).toBeGreaterThanOrEqual(slots[index - 1].startMs + slots[index - 1].typeMs + FINDING_BEAT_MS);
    }
  });

  it('shows no scrollbar beside the findings, puts Finalizar on the left and the closing line on the right', () => {
    act(() =>
      root.render(
        <ForensicPanel
          latest={CAPTURE}
          count={1}
          stage={5}
          missionOver
          landed
          report={FOUR}
          inspectionStarted
          reportRevealed={0}
          reportClosing="Relatório finalizado e disponível na estação."
          onFinish={vi.fn()}
          lock={GROUNDED}
        />
      )
    );
    advance(sequenceMs());
    const list = container.querySelector('ul[aria-live]') as HTMLElement;
    expect(list.className).toContain('scroll-none');
    expect(list.className).not.toContain('scroll-thin');
    const closing = Array.from(list.querySelectorAll('li')).find((item) => item.textContent?.includes('Relatório finalizado'));
    expect(closing?.className).toContain('text-right');
    const finish = container.querySelector('button[data-finish-state]') as HTMLElement;
    expect(finish.className).toContain('left-3');
    expect(finish.className).not.toContain('right-3');
  });

  it('brings in all four findings with no capture at all', () => {
    renderInFlight({ report: FOUR, started: true, capture: null });
    advance(sequenceMs());
    expect(shownTopics()).toHaveLength(4);
  });

  it('holds the victim placeholder in the instant before the report is drawn', () => {
    renderInFlight({ report: null, started: true });
    const card = container.querySelector('[data-finding-state="analyzing"]');
    expect(card?.textContent).toContain('Analisando imagem');
    expect(card?.textContent).toContain('Avaliação da vítima');
  });
});

describe('the report inside the cockpit', () => {
  beforeEach(() => {
    recordRevealed('accident_raw_20261020_113500.png|0', 0.5);
  });
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
          inspectionStarted
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
    started?: boolean;
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
          inspectionStarted={props.started ?? Boolean(props.report)}
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
    expect(container.querySelector('[data-scan-pass]')).toBeNull();
  });

  it('runs the grid tempo 4x faster from the start of the flight', () => {
    expect(GRID_TEMPO).toBe(12);
  });

  describe('the scan over the photograph', () => {
    const CLIENT_WIDTH = 400;
    // One act per 16 ms: effects scheduled by a state change must run before the next timer is armed.
    const frames = (ms: number) => {
      for (let elapsed = 0; elapsed < ms; elapsed += 16) act(() => vi.advanceTimersByTime(Math.min(16, ms - elapsed)));
    };
    const scanPass = () => container.querySelector<HTMLElement>('[data-scan-pass]');
    const scanX = () => parseFloat(scanPass()?.style.getPropertyValue('--scan-x') ?? 'NaN');
    /** Flight progress is the one input the scan follows. */
    const fly = (flight: number, extra: { landed?: boolean; missionOver?: boolean; lock?: FinishLockResult } = {}) =>
      act(() =>
        root.render(
          <ForensicPanel
            latest={inFlight}
            count={1}
            stage={5}
            missionOver={extra.missionOver ?? false}
            landed={extra.landed ?? false}
            report={null}
            inspectionStarted={false}
            reportRevealed={0}
            flightProgress={flight}
            waitProgress={0.4}
            onFinish={vi.fn()}
            lock={extra.lock ?? LOCKED}
          />
        )
      );
    /** Take the capture in flight and wait until the grid has become the photograph. */
    const revealed = (flight = 0.5) => {
      renderPanel({});
      fly(flight);
      frames(SHOT_MS + 10 + PHOTO_IN_MS + 60);
    };
    let widthDescriptor: PropertyDescriptor | undefined;
    /** Announced as the flight goes on: its inspection deadline is still ahead for the whole pass. */
    let inFlight: RawEvidence = CAPTURE;

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'Date', 'setTimeout', 'clearTimeout'] });
      inFlight = { ...CAPTURE, timestamp: Date.now() };
      widthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => CLIENT_WIDTH });
    });
    afterEach(() => {
      vi.useRealTimers();
      if (widthDescriptor) Object.defineProperty(HTMLElement.prototype, 'clientWidth', widthDescriptor);
      else delete (HTMLElement.prototype as unknown as { clientWidth?: number }).clientWidth;
    });

    it('does not exist while the grid is still becoming the photograph', () => {
      renderPanel({});
      fly(0.5);
      frames(200);
      expect(scanPass()).toBeNull();
      frames(SHOT_MS + 10 + PHOTO_IN_MS + 60);
      expect(grid()?.getAttribute('data-evidence-grid')).toBe('revealed');
      expect(scanPass()).not.toBeNull();
    });

    it('lives inside the grid, over the photograph, with a line and a trail and no text', () => {
      revealed();
      const pass = scanPass()!;
      expect(grid()?.contains(pass)).toBe(true);
      expect(container.querySelector('[data-evidence-frame]')?.contains(pass)).toBe(true);
      expect(pass.querySelectorAll('.evgrid-scan-line')).toHaveLength(1);
      expect(pass.querySelectorAll('.evgrid-scan-trail')).toHaveLength(1);
      expect(pass.textContent).toBe('');
      expect(container.querySelector('[data-scan-sweep]')).toBeNull();
      expect(container.querySelector('[data-capture-stamp]')).toBeNull();
    });

    it('goes left to right once, following the flight, and never back', () => {
      revealed(0.5);
      const seen: number[] = [];
      const sample = (ms: number) => {
        for (let i = 0; i < ms / 16; i += 1) {
          frames(16);
          seen.push(scanX());
        }
      };
      sample(400);
      expect(seen[seen.length - 1]).toBeLessThan(CLIENT_WIDTH * 0.1);
      fly(0.75);
      sample(6000);
      expect(seen[seen.length - 1]).toBeGreaterThan(CLIENT_WIDTH * 0.45);
      expect(seen[seen.length - 1]).toBeLessThan(CLIENT_WIDTH * 0.52);
      // A measured dip in the flight's progress does not pull the line back.
      fly(0.6);
      sample(1500);
      expect(seen.every((value, index) => index === 0 || value >= seen[index - 1])).toBe(true);
      // Smooth: no frame moves it by more than a few percent of the width.
      expect(Math.max(...seen.slice(1).map((value, index) => value - seen[index]))).toBeLessThan(CLIENT_WIDTH * 0.04);
    });

    it('runs out to the right edge at touchdown, fades, and only then lets the photograph rise', () => {
      revealed(0.5);
      frames(SCAN_MIN_PASS_MS + 400);
      fly(0.9, { landed: true, lock: GROUNDED });
      expect(container.querySelector('[data-capture-stamp]')).toBeNull();
      // About 0.4 s to run out to the edge, then the 0.2 s fade.
      frames(450);
      expect(scanX()).toBe(CLIENT_WIDTH);
      expect(scanPass()?.className).toContain('evgrid-scan-out');
      expect(container.querySelector('[data-capture-stamp]')).toBeNull();
      frames(SCAN_FADE_MS + 100);
      expect(scanPass()).toBeNull();
      expect(container.querySelector('[data-capture-stamp]')?.textContent).toMatch(/capturada em/);
    });

    it('is never shorter than its minimum pass when the aircraft lands first', () => {
      revealed(0.5);
      fly(0.99, { landed: true, lock: GROUNDED });
      frames(SCAN_MIN_PASS_MS / 2);
      // Half the minimum has passed, plus the few frames the pass had already run: never near the edge.
      expect(scanX()).toBeLessThanOrEqual(CLIENT_WIDTH * 0.6);
      expect(container.querySelector('[data-capture-stamp]')).toBeNull();
      frames(SCAN_MIN_PASS_MS);
      frames(SCAN_FADE_MS + 100);
      expect(scanPass()).toBeNull();
      expect(container.querySelector('[data-capture-stamp]')).not.toBeNull();
    });

    it('closes at once, with no minimum, when the flight was aborted', () => {
      revealed(0.5);
      fly(0.52, { missionOver: true });
      frames(450);
      expect(scanX()).toBe(CLIENT_WIDTH);
    });

    it('does not run a second pass when the cockpit is revisited after touchdown', () => {
      recordRevealed(`${inFlight.filename}|${inFlight.timestamp}`, 0.5);
      fly(0.9, { landed: true, lock: GROUNDED });
      frames(PHOTO_IN_MS + 100);
      expect(scanPass()).toBeNull();
      expect(container.querySelector('[data-capture-stamp]')).not.toBeNull();
    });
  });

  it('places the scan in the photograph box, not in the frame', () => {
    // 16:9 photograph in a square frame: bands above and below.
    expect(containRect({ w: 1600, h: 900 }, { w: 600, h: 600 })).toEqual({ left: 0, top: 131.25, width: 600, height: 337.5 });
    // The same photograph in a 16:9 frame: the whole frame.
    expect(containRect({ w: 1600, h: 900 }, { w: 800, h: 450 })).toEqual({ left: 0, top: 0, width: 800, height: 450 });
    // A vertical photograph in a wide frame: bands at the sides.
    const tall = containRect({ w: 900, h: 1600 }, { w: 800, h: 450 })!;
    expect(tall.top).toBe(0);
    expect(tall.height).toBe(450);
    expect(tall.width).toBeCloseTo(253.125, 6);
    expect(tall.left).toBeCloseTo((800 - 253.125) / 2, 6);
    expect(containRect(null, { w: 10, h: 10 })).toBeNull();
    expect(containRect({ w: 0, h: 10 }, { w: 10, h: 10 })).toBeNull();
  });

  it('reveals the photograph over REVEAL_MS without a jump', () => {
    expect(REVEAL_MS).toBeGreaterThanOrEqual(1500);
    expect(revealAmount(0)).toBe(0);
    expect(revealAmount(REVEAL_MS)).toBe(1);
    expect(revealAmount(REVEAL_MS * 2)).toBe(1);
    let previous = 0;
    let largest = 0;
    for (let ms = 16; ms <= REVEAL_MS; ms += 16) {
      const value = revealAmount(ms);
      expect(value).toBeGreaterThanOrEqual(previous);
      largest = Math.max(largest, value - previous);
      previous = value;
    }
    // At 60 frames a second no frame advances the reveal by more than 2 %.
    expect(largest).toBeLessThan(0.02);
  });

  it('is whole on touchdown, then tells the time of capture', async () => {
    recordRevealed('accident_raw_20261029_111530.png|0', 0.5);
    renderPanel({ latest: CAPTURE });
    renderPanel({ latest: CAPTURE, landed: true, lock: GROUNDED });
    expect(container.querySelector('.evgrid-photo')?.className).toContain('evgrid-photo-in');
    expect(container.querySelector('[data-capture-stamp]')).toBeNull();
    await settle();
    expect(grid()?.getAttribute('data-evidence-grid')).toBe('revealed');
    expect(container.querySelector('[data-capture-stamp]')?.textContent).toMatch(/capturada em/);
  });

  it('shows the cards in their boxes, typing the white sentence to end with the voice', async () => {
    recordRevealed('accident_raw_20261029_111530.png|0', 0.5);
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
