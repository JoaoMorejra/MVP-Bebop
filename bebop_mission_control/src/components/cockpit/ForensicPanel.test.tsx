// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ForensicPanel } from './ForensicPanel';
import { isAirborne } from '../../lib/flightState';
import { FINISH_HOLD_MS, FINISH_REASONS, type FinishLockResult } from '../../lib/finishLock';
import type { Finding } from '../../lib/forensics';
import type { RawEvidence, ReportReadyEvent } from '../../types/bmg';

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

describe('Visualizar Laudo Pericial', () => {
  const REPORT: Finding[] = (['police', 'samu', 'victim', 'vehicle'] as const).map((topic) => ({
    topic,
    card: topic,
    speech: topic,
  }));
  const LATEST: RawEvidence = {
    filename: 'accident_raw_20261020_113500.png',
    url: '/evidence/accident_raw_20261020_113500.png',
    timestamp: 0,
    occurrenceId: 'OC_20261020_143500',
  };
  const REPORT_URL = '/evidence/relatorio_OC_20261020_143500.html';

  let reportListener: ((event: ReportReadyEvent) => void) | null;
  let getReportUrl: Mock<[string?], Promise<string | null>>;

  beforeEach(() => {
    reportListener = null;
    getReportUrl = vi.fn<[string?], Promise<string | null>>(async () => null);
    (window as unknown as { bmgAPI: unknown }).bmgAPI = {
      getReportUrl,
      onReportReady: (cb: (event: ReportReadyEvent) => void) => {
        reportListener = cb;
        return () => {
          reportListener = null;
        };
      },
    };
  });

  afterEach(() => {
    delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
  });

  async function renderReport(revealed: number, latest: RawEvidence = LATEST) {
    await act(async () =>
      root.render(
        <ForensicPanel
          latest={latest}
          count={1}
          stage={5}
          missionOver
          landed
          report={REPORT}
          reportRevealed={revealed}
          onFinish={vi.fn()}
          lock={GROUNDED}
        />
      )
    );
    return container.querySelector('[data-testid="open-report"]') as HTMLButtonElement | null;
  }

  it('stays hidden until the copilot has read all four findings', async () => {
    expect(await renderReport(3)).toBeNull();
    expect(await renderReport(4)).not.toBeNull();
  });

  it('asks the host for this occurrence and waits while the report is not on disk', async () => {
    const button = await renderReport(4);
    expect(getReportUrl).toHaveBeenCalledWith('OC_20261020_143500');
    act(() => button!.click());
    expect(container.querySelector('[data-testid="report-occurrence-id"]')?.textContent).toBe(
      'OC_20261020_143500'
    );
    expect(container.querySelector('[data-testid="report-waiting"]')).not.toBeNull();
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('shows the report once announced, in a frame that cannot reach the bridge', async () => {
    const button = await renderReport(4);
    act(() => button!.click());
    act(() => reportListener!({ filename: 'relatorio_OC_20261020_143500.html', url: REPORT_URL, occurrenceId: 'OC_20261020_143500' }));
    const frame = container.querySelector('iframe') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe(REPORT_URL);
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
  });

  it('ignores a polled report from another occurrence', async () => {
    getReportUrl.mockResolvedValue('/evidence/relatorio_OC_20260101_000000.html');
    const button = await renderReport(4);
    act(() => button!.click());
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('ignores an announced report for another occurrence', async () => {
    const button = await renderReport(4);
    act(() => button!.click());
    act(() =>
      reportListener!({
        filename: 'relatorio_OC_20260101_000000.html',
        url: '/evidence/relatorio_OC_20260101_000000.html',
        occurrenceId: 'OC_20260101_000000',
      })
    );
    expect(container.querySelector('iframe')).toBeNull();
  });

  it('opens only inside the cockpit and expands within the window', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const button = await renderReport(4);
    act(() => button!.click());
    act(() => reportListener!({ filename: 'relatorio_OC_20261020_143500.html', url: REPORT_URL }));
    const buttons = Array.from(container.querySelectorAll('[role="dialog"] button'));
    expect(buttons.some((b) => /nova aba/i.test(b.getAttribute('aria-label') ?? ''))).toBe(false);

    const expand = container.querySelector('[data-testid="report-expand"]') as HTMLButtonElement;
    act(() => expand.click());
    expect(container.querySelector('[data-report-size]')?.getAttribute('data-report-size')).toBe('expanded');
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe(REPORT_URL);
    expect(open).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it('follows the report name recorded in the inspection package', async () => {
    const latest: RawEvidence = {
      ...LATEST,
      occurrenceId: undefined,
      inferencePackage: {
        schema: 't4h.drone-inspection/1',
        open_conversation: { data: {} },
        message: { type: 'image', image: null, caption: '{}' },
        caption: {},
        pending: [{ campo: 'occurrence_id', motivo: 'ausente' }],
        ready: false,
        expected_report: 'relatorio_inspecao_sem_identificador.html',
        position: { source: 'site', site: 'Transamerica Expo Center' },
      },
    };
    const button = await renderReport(4, latest);
    expect(getReportUrl).toHaveBeenCalledWith(undefined);
    act(() => button!.click());
    expect(container.querySelector('[data-testid="report-occurrence-id"]')?.textContent).toBe('sem identificador');
    const url = '/evidence/relatorio_inspecao_sem_identificador.html';
    act(() => reportListener!({ filename: 'relatorio_inspecao_sem_identificador.html', url }));
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe(url);
  });

  it('closes on Escape', async () => {
    const button = await renderReport(4);
    act(() => button!.click());
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });
});
