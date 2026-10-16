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

function render(missionMode?: MissionMode, stageName = 'Retornando base'): void {
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
      />
    )
  );
}

describe('StageBar stage 5 label by mission mode', () => {
  it('intermediate shows Pouso no local in pills and header', () => {
    render('intermediate');
    expect(host.textContent).toContain('Pouso no local');
    expect(host.textContent).not.toContain('Retornando base');
    expect(host.textContent).toContain('5 · Pouso no local');
  });

  it('simple shows Pouso na base', () => {
    render('simple');
    expect(host.textContent).toContain('Pouso na base');
    expect(host.textContent).not.toContain('Retornando base');
  });

  it('defaults to Retornando base', () => {
    render(undefined);
    expect(host.textContent).toContain('Retornando base');
  });
});
