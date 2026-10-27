// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LINK_REARM_DWELL_MS, useLinkLossNarration } from './useCopilot';
import { LINK_LOSS_SENTENCES } from '../lib/copilotPhrases';
import { NarrationQueue } from '../lib/narrationQueue';
import type { AnnouncePriority } from '../types/bmg';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
let spoken: Array<{ text: string; priority: AnnouncePriority | undefined }> = [];
let queue: NarrationQueue;

const Probe: React.FC<{ linkLost: boolean; draw?: number }> = ({ linkLost, draw = 0 }) => {
  useLinkLossNarration(queue, linkLost, () => draw);
  return null;
};

async function show(linkLost: boolean, draw?: number) {
  await act(async () => {
    root.render(<Probe linkLost={linkLost} draw={draw} />);
    await Promise.resolve();
  });
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  spoken = [];
  queue = new NarrationQueue(async (text, priority) => {
    spoken.push({ text, priority });
    return true;
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

async function elapse(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
  });
}

describe('link-loss call', () => {
  it('says nothing while the link holds', async () => {
    await show(false);
    await show(false);
    expect(spoken).toEqual([]);
  });

  it('speaks once, at URGENT, on the edge of the loss', async () => {
    await show(false);
    await show(true);
    await show(true);
    await show(true, 0.9);
    expect(spoken).toEqual([{ text: LINK_LOSS_SENTENCES[0], priority: 'URGENT' }]);
  });

  it('re-arms once the link has held for the dwell, and speaks again on the next loss', async () => {
    vi.useFakeTimers();
    await show(false);
    await show(true, 0);
    await show(false);
    await elapse(LINK_REARM_DWELL_MS + 100);
    await show(false);
    await show(true, 0.5);
    expect(spoken.map((line) => line.text)).toEqual([LINK_LOSS_SENTENCES[0], LINK_LOSS_SENTENCES[1]]);
    expect(spoken.every((line) => line.priority === 'URGENT')).toBe(true);
  });

  it('stays silent on a flap shorter than the dwell, such as a telemetry bridge restart', async () => {
    vi.useFakeTimers();
    await show(false);
    await show(true, 0);
    await show(false);
    await elapse(LINK_REARM_DWELL_MS - 500);
    await show(true, 0.5);
    await show(false);
    await elapse(LINK_REARM_DWELL_MS - 500);
    await show(true, 0.99);
    expect(spoken.map((line) => line.text)).toEqual([LINK_LOSS_SENTENCES[0]]);
  });

  it('does not count a recovery cut short toward the dwell of the next one', async () => {
    vi.useFakeTimers();
    await show(true, 0);
    await show(false);
    await elapse(LINK_REARM_DWELL_MS - 500);
    await show(true);
    await show(false);
    await elapse(1000);
    await show(true, 0.5);
    expect(spoken).toHaveLength(1);
    await show(false);
    await elapse(LINK_REARM_DWELL_MS);
    await show(true, 0.5);
    expect(spoken.map((line) => line.text)).toEqual([LINK_LOSS_SENTENCES[0], LINK_LOSS_SENTENCES[1]]);
  });

  it('waits a few seconds of fresh telemetry before re-arming', () => {
    expect(LINK_REARM_DWELL_MS).toBeGreaterThanOrEqual(2000);
    expect(LINK_REARM_DWELL_MS).toBeLessThanOrEqual(5000);
  });

  it('speaks a loss already present when the station starts watching', async () => {
    await show(true, 0.99);
    expect(spoken).toEqual([{ text: LINK_LOSS_SENTENCES[2], priority: 'URGENT' }]);
  });
});
