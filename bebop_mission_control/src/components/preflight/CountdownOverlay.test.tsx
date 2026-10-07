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
  trim?: 'acked' | 'skipped' | null;
  launchAt?: number | null;
  onDone?: () => void;
}

function show({ remaining, clearance = false, trim = null, launchAt = null, onDone = vi.fn() }: Props) {
  act(() =>
    root.render(
      <CountdownOverlay
        seconds={10}
        remaining={remaining}
        clearance={clearance}
        trim={trim}
        launchAt={launchAt}
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
    expect(numeral()).toBeUndefined();
  });

  it('shows that it is preparing, and for how long, instead of a frozen number', () => {
    show({ remaining: null });
    const state = () => container.querySelector('[data-countdown-state]')?.getAttribute('data-countdown-state');
    const elapsed = () => container.querySelector('[data-preparing-elapsed]')?.textContent?.trim();
    expect(state()).toBe('preparing');
    expect(container.textContent).toContain('Preparando a aeronave');
    expect(elapsed()).toBe('0 s');
    act(() => vi.advanceTimersByTime(3_000));
    expect(elapsed()).toBe('3 s');
    show({ remaining: 10 });
    expect(state()).toBe('counting');
    expect(numeral()).toBe('10');
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
    show({ remaining: 5, trim: 'acked' });
    expect(stepState('imu')).toBe('done');
  });

  it('shows a bench trim without acknowledgement as skipped, not as spinning', () => {
    show({ remaining: 5, trim: 'skipped' });
    expect(stepState('imu')).toBe('skipped');
    expect(container.querySelector('[data-step="imu"]')?.textContent).toContain('sem confirmação');
  });

  it('counts from the click on its own clock when it knows the click instant', () => {
    show({ remaining: null, launchAt: Date.now() });
    expect(numeral()).toBe('10');
    act(() => vi.advanceTimersByTime(3_000));
    expect(numeral()).toBe('7');
    show({ remaining: 7, launchAt: Date.now() - 3_000 });
    expect(numeral()).toBe('7');
  });

  it('does not close on a zero the mission reported before its own clock ran out', () => {
    // Regression: a cold launch's mission read a deadline already behind it
    // and reported zero at once; the overlay closed on it with 10 s to go.
    const launchAt = Date.now();
    const onDone = show({ remaining: 0, clearance: true, launchAt });
    expect(onDone).not.toHaveBeenCalled();
    expect(numeral()).toBe('10');
    act(() => vi.advanceTimersByTime(9_000));
    expect(onDone).not.toHaveBeenCalled();
    expect(numeral()).toBe('1');
    act(() => vi.advanceTimersByTime(1_200));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('waits for the aircraft at zero instead of claiming a takeoff', () => {
    const launchAt = Date.now();
    const onDone = show({ remaining: null, launchAt });
    act(() => vi.advanceTimersByTime(10_500));
    expect(onDone).not.toHaveBeenCalled();
    expect(container.querySelector('[data-countdown-state]')?.getAttribute('data-countdown-state')).toBe('waiting');
    expect(container.textContent).toContain('Aguardando a aeronave');
    show({ remaining: 0, clearance: true, launchAt, onDone });
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});

