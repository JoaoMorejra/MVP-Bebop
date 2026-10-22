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

function render(missionMode?: MissionMode, stageName = 'Retornando base', rtlEnabled?: boolean): void {
  act(() =>
    root.render(
      <StageBar
        stage={5}
        stageName={stageName}
        state="running"
        benchMode={false}
        benchStage={null}
        onRunStage={() => undefined}
        onGotoStage={() => undefined}
        pendingStage={null}
        voice={{ volume: 0.5, muted: false } as never}
        onVolume={() => undefined}
        onToggleMute={() => undefined}
        missionMode={missionMode}
        rtlEnabled={rtlEnabled}
      />
    )
  );
}

describe('StageBar stage 5 label by mission mode', () => {
  it('advanced with the return shows Retorno à base in pills and header', () => {
    render('advanced');
    expect(host.textContent).toContain('5 · Retorno à base');
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

describe('StageBar stage 5 label and plan chip by return plan', () => {
  it.each(['simple', 'advanced'] as const)('says Pouso no local and never the base without the return (%s)', (mode) => {
    render(mode, 'Retornando base', false);
    expect(host.textContent).toContain('5 · Pouso no local');
    expect(host.textContent).not.toMatch(/base|retorno/i);
    expect(host.querySelector('[data-plan-chip="end"]')?.textContent).toBe('Final: pouso no local');
  });

  it('shows the return plan chip with the return', () => {
    render('advanced', 'Retornando base', true);
    expect(host.querySelector('[data-plan-chip="end"]')?.textContent).toBe('Final: RTL + pouso de precisão');
  });
});
