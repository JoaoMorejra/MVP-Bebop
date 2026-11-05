// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RawEvidence } from '../types/bmg';
import { INSPECTION_DELAY_MS, INSPECTION_FALLBACK_MS, useInspectionSchedule, type InspectionSchedule } from './useInspectionSchedule';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
const seen: { schedule: InspectionSchedule | null } = { schedule: null };

function Probe(props: { run: number | null; stage: number; capture: RawEvidence | null }) {
  seen.schedule = useInspectionSchedule(props.run, props.stage, props.capture);
  return null;
}

const render = (run: number | null, stage: number, capture: RawEvidence | null = null) =>
  act(() => root.render(<Probe run={run} stage={stage} capture={capture} />));
const advance = (ms: number) => act(() => vi.advanceTimersByTime(ms));
const captureNow = (): RawEvidence => ({ filename: 'accident_raw_20261105_101500.png', url: '/x.png', timestamp: Date.now() });

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  seen.schedule = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe('useInspectionSchedule', () => {
  it('has nothing to inspect with no run, or before Stage 4 with no capture', () => {
    render(null, 4);
    expect(seen.schedule).toEqual({ runToken: null, started: false });
    render(1, 3);
    advance(INSPECTION_FALLBACK_MS * 4);
    expect(seen.schedule).toEqual({ runToken: null, started: false });
  });

  it('starts the inspection 12 s after the capture reaches the station, in flight', () => {
    render(1, 4);
    advance(5000);
    render(1, 4, captureNow());
    advance(INSPECTION_DELAY_MS - 1);
    expect(seen.schedule?.started).toBe(false);
    advance(1);
    expect(seen.schedule).toEqual({ runToken: '1', started: true });
  });

  it('starts it from Stage 4 alone when no capture ever arrives (a failed capture, an abort in Stage 4)', () => {
    render(1, 4);
    expect(seen.schedule?.runToken).toBe('1');
    advance(INSPECTION_FALLBACK_MS - 1);
    expect(seen.schedule?.started).toBe(false);
    advance(1);
    expect(seen.schedule?.started).toBe(true);
  });

  it('a capture arriving late cannot postpone the Stage 4 deadline', () => {
    render(1, 4);
    advance(INSPECTION_FALLBACK_MS - 2000);
    render(1, 5, captureNow());
    advance(2000);
    expect(seen.schedule?.started).toBe(true);
  });

  it('holds once started, whatever the stage does after, and restarts with the next flight', () => {
    render(1, 4, captureNow());
    advance(INSPECTION_DELAY_MS);
    expect(seen.schedule?.started).toBe(true);
    render(1, 5, captureNow());
    expect(seen.schedule?.started).toBe(true);
    render(2, 0);
    expect(seen.schedule).toEqual({ runToken: null, started: false });
    render(2, 4);
    expect(seen.schedule?.started).toBe(false);
  });
});
