// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ForensicPanel } from './ForensicPanel';
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
  it('is disabled, and says why, with nothing to finish', () => {
    const { button, onFinish } = render(LOCKED);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(button.title).toBe('Disponível após iniciar uma missão');
    act(() => button.click());
    expect(onFinish).not.toHaveBeenCalled();
  });

  it.each([
    ['in_flight', FINISH_REASONS.inFlight],
    ['await_ground', FINISH_REASONS.awaitGround],
    ['aborting', FINISH_REASONS.landing],
  ] as const)('locked in %s shows "%s" as its tooltip', (state, reason) => {
    const { button } = render({ state, enabled: false, reason, requiresConfirm: false });
    expect(button.disabled).toBe(true);
    expect(button.title).toBe(reason);
  });

  it('ends the cycle once the aircraft is confirmed on the ground', () => {
    const { button, onFinish } = render(GROUNDED);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-disabled')).toBe('false');
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
      expect(button.title).toBe(FINISH_REASONS.linkLost);
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

  it('holds a placeholder victim card with no finding text while analyzing', () => {
    renderReport({ report: null, analyzing: true });
    const card = container.querySelector('[data-finding-state="analyzing"]');
    expect(card?.textContent).toContain('Analisando imagem');
    expect(card?.textContent).toContain('Estado do acidentado');
    expect(container.querySelector('ul')?.getAttribute('aria-busy')).toBe('true');
    expect(container.textContent).toContain('analisando imagem');
  });

  it('replaces the placeholder with the drawn findings', () => {
    renderReport({
      report: [{ topic: 'victim', card: 'Cena sem vítimas aparentes', speech: 'Por fim, cena sem vítimas aparentes.' }],
      analyzing: false,
    });
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
