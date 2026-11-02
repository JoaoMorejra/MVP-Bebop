// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { StageBar } from './StageBar';
import type { MissionMode } from '../../lib/missionMode';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(missionMode?: MissionMode, rtlEnabled?: boolean): void {
  act(() =>
    root.render(
      <StageBar
        stage={5}
        state="running"
        benchMode={false}
        benchStage={null}
        onRunStage={() => undefined}
        onGotoStage={() => undefined}
        pendingStage={null}
        missionMode={missionMode}
        rtlEnabled={rtlEnabled}
      />
    )
  );
}

describe('StageBar stage 5 label by mission mode', () => {
  it('advanced with the return shows Retorno à base in the pills', () => {
    render('advanced');
    expect(host.textContent).toContain('Retorno à base');
    expect(host.textContent).not.toContain('Retornando base');
  });

  it('simple shows Pouso na base', () => {
    render('simple');
    expect(host.textContent).toContain('Pouso na base');
    expect(host.textContent).not.toContain('Retornando base');
  });

  it('defaults to the advanced profile with the return', () => {
    render(undefined);
    expect(host.textContent).toContain('Retorno à base');
  });
});

describe('StageBar stage 5 label by return plan', () => {
  it.each(['simple', 'advanced'] as const)('says Pouso no local and never the base without the return (%s)', (mode) => {
    render(mode, false);
    expect(host.textContent).toContain('Pouso no local');
    expect(host.textContent).not.toMatch(/base|retorno/i);
  });
});

describe('StageBar opens on the flight stages', () => {
  it('has no state header and no plan chip above the stages', () => {
    render('advanced', true);
    expect(host.querySelector('header')).toBeNull();
    expect(host.querySelector('[data-plan-chip]')).toBeNull();
    expect(host.textContent).not.toContain('Aguardando missão');
    expect(host.textContent).not.toContain('Missão autônoma');
    expect(host.firstElementChild?.tagName).toBe('NAV');
  });
});

describe('StageBar without the voice control', () => {
  it('stage bar has no voice control', () => {
    render('advanced');
    expect(host.querySelector('[aria-label="Voz do copiloto"]')).toBeNull();
    expect(host.querySelector('input[type="range"]')).toBeNull();
  });
});

describe('StageBar bench countdown', () => {
  function renderBench(benchCountdown: number | null) {
    act(() =>
      root.render(
        <StageBar
          stage={0}
            state="idle"
          benchMode
          benchStage={2}
          onRunStage={() => undefined}
          onGotoStage={() => undefined}
          pendingStage={null}
          benchCountdown={benchCountdown}
        />
      )
    );
  }
  const pill = (step: number) => host.querySelectorAll('nav li')[step - 1] as HTMLElement;

  it('stage bar shows the remaining countdown seconds inside the running bench stage pill', () => {
    renderBench(7);
    expect(pill(2).querySelector('[data-bench-countdown]')?.textContent).toBe('7');
    expect(pill(3).querySelector('[data-bench-countdown]')).toBeNull();
  });

  it('no countdown text when benchCountdown is null', () => {
    renderBench(null);
    expect(host.querySelector('[data-bench-countdown]')).toBeNull();
    renderBench(0);
    expect(host.querySelector('[data-bench-countdown]')).toBeNull();
  });

  it('the numeral has tabular figures and is aria-hidden with an sr-only equivalent', () => {
    renderBench(5);
    const numeral = pill(2).querySelector('[data-bench-countdown]')!;
    expect(numeral.className).toContain('tnum');
    expect(numeral.getAttribute('aria-hidden')).toBe('true');
    expect(pill(2).querySelector('.sr-only')?.textContent).toBe('5 segundos para iniciar');
  });
});

describe('StageBar fill', () => {
  const renderFill = (fills: [number, number, number, number, number], cursor: number, state: 'running' | 'aborted' = 'running') =>
    act(() =>
      root.render(
        <StageBar
          stage={cursor}
          state={state}
          benchMode={false}
          benchStage={null}
          onRunStage={() => undefined}
          onGotoStage={() => undefined}
          pendingStage={null}
          fill={{ fills, cursor }}
        />
      )
    );
  const pill = (step: number) => host.querySelector(`[data-stage="${step}"]`) as HTMLElement;

  it('fills each pill to its stage progress, done behind the cursor, active on it, empty ahead', () => {
    renderFill([1, 0.42, 0, 0, 0], 2);
    expect(pill(1).getAttribute('data-state')).toBe('done');
    expect(pill(1).getAttribute('data-fill')).toBe('100');
    expect(pill(2).getAttribute('data-state')).toBe('active');
    expect(pill(2).getAttribute('data-fill')).toBe('42');
    expect((pill(2).querySelector('[data-stage-fill]') as HTMLElement).style.width).toBe('42%');
    expect(pill(3).getAttribute('data-fill')).toBe('0');
  });

  it('draws the label a second time, dark and clipped to the fill', () => {
    renderFill([1, 0.42, 0, 0, 0], 2);
    const on = pill(2).querySelector('.stage-face-on') as HTMLElement;
    expect(on.getAttribute('aria-hidden')).toBe('true');
    expect(on.style.clipPath).toBe('inset(0 58% 0 0)');
  });

  it('turns the stage that stopped ember and holds its fill', () => {
    renderFill([1, 0.3, 0, 0, 0], 2, 'aborted');
    expect(pill(2).getAttribute('data-state')).toBe('halted');
    expect(pill(2).querySelector('[data-stage-fill]')!.className).toContain('bg-ember');
  });

  it('has no progress rule under the pills', () => {
    renderFill([1, 0.42, 0, 0, 0], 2);
    expect(host.querySelector('nav')!.lastElementChild!.tagName).toBe('OL');
  });
});
