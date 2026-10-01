// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CountdownOverlay } from './CountdownOverlay';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

interface Props {
  remaining: number | null;
  clearance?: boolean;
  trimAcked?: boolean;
  onDone?: () => void;
}

function show({ remaining, clearance = false, trimAcked = false, onDone = vi.fn() }: Props) {
  act(() =>
    root.render(
      <CountdownOverlay
        seconds={10}
        remaining={remaining}
        clearance={clearance}
        trimAcked={trimAcked}
        stageReached
        linkReady
        onDone={onDone}
        onCancel={() => undefined}
      />
    )
  );
  return onDone;
}

const numeral = () => container.querySelector('[data-countdown-numeral]')?.textContent?.trim();
const stepState = (id: string) => container.querySelector(`[data-step="${id}"]`)?.getAttribute('data-state');

describe('CountdownOverlay driven by the mission', () => {
  it('does not count on its own before the mission reports its countdown', () => {
    const onDone = show({ remaining: null });
    act(() => vi.advanceTimersByTime(30_000));
    expect(onDone).not.toHaveBeenCalled();
    expect(numeral()).toBe('10');
    expect(container.textContent).toContain('Aguardando a missão');
  });

  it('shows the second the mission reported', () => {
    show({ remaining: 7 });
    expect(numeral()).toBe('7');
  });

  it('finishes when the mission reports zero', () => {
    const onDone = show({ remaining: 1 });
    expect(onDone).not.toHaveBeenCalled();
    show({ remaining: 0, clearance: true, onDone });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('marks the clearance done only once the mission called it', () => {
    show({ remaining: 4 });
    expect(stepState('clearance')).toBe('pending');
    show({ remaining: 3, clearance: true });
    expect(stepState('clearance')).toBe('done');
  });

  it('never marks the IMU trim done without its acknowledgement', () => {
    show({ remaining: 5 });
    expect(stepState('imu')).not.toBe('done');
    show({ remaining: 5, trimAcked: true });
    expect(stepState('imu')).toBe('done');
  });
});
