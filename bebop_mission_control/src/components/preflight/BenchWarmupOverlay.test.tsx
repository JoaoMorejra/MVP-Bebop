// @vitest-environment jsdom
import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BENCH_WARMUP_MS, BenchWarmupOverlay } from './BenchWarmupOverlay';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

const overlay = () => container.querySelector('[role="dialog"]');

/** The same mount/unmount contract App.tsx gives it. */
function Host({ onDone }: { onDone: () => void }) {
  const [warming, setWarming] = useState(true);
  return warming ? (
    <BenchWarmupOverlay
      onDone={() => {
        setWarming(false);
        onDone();
      }}
    />
  ) : (
    <p data-testid="cockpit">cockpit</p>
  );
}

describe('BenchWarmupOverlay', () => {
  it('lasts ten seconds', () => {
    expect(BENCH_WARMUP_MS).toBe(10_000);
  });

  it('shows the pipeline loading message while it runs', () => {
    act(() => root.render(<BenchWarmupOverlay onDone={() => undefined} />));
    expect(overlay()).not.toBeNull();
    expect(container.textContent).toContain('Carregando pipeline de vídeo e pesos YOLO');
  });

  it('holds the cockpit back for the full ten seconds, then unmounts', () => {
    const onDone = vi.fn();
    act(() => root.render(<Host onDone={onDone} />));

    act(() => vi.advanceTimersByTime(BENCH_WARMUP_MS - 1));
    expect(overlay()).not.toBeNull();
    expect(container.querySelector('[data-testid="cockpit"]')).toBeNull();
    expect(onDone).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(1));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(overlay()).toBeNull();
    expect(container.querySelector('[data-testid="cockpit"]')).not.toBeNull();
  });

  it('reports completion once, however long it stays mounted', () => {
    const onDone = vi.fn();
    act(() => root.render(<BenchWarmupOverlay onDone={onDone} />));
    act(() => vi.advanceTimersByTime(BENCH_WARMUP_MS * 3));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('does not restart when the parent re-renders with a new callback', () => {
    const onDone = vi.fn();
    act(() => root.render(<BenchWarmupOverlay onDone={() => onDone('first')} />));
    act(() => vi.advanceTimersByTime(6000));
    act(() => root.render(<BenchWarmupOverlay onDone={() => onDone('second')} />));
    act(() => vi.advanceTimersByTime(4000));
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith('second');
  });

  it('never fires after it has been unmounted early', () => {
    const onDone = vi.fn();
    act(() => root.render(<BenchWarmupOverlay onDone={onDone} />));
    act(() => vi.advanceTimersByTime(3000));
    act(() => root.render(<p>gone</p>));
    act(() => vi.advanceTimersByTime(BENCH_WARMUP_MS));
    expect(onDone).not.toHaveBeenCalled();
  });

  it('advances its progress indicator with elapsed time', () => {
    act(() => root.render(<BenchWarmupOverlay onDone={() => undefined} />));
    const bar = () => container.querySelector('[role="progressbar"]');
    expect(bar()?.getAttribute('aria-valuenow')).toBe('0');
    act(() => vi.advanceTimersByTime(5000));
    expect(Number(bar()?.getAttribute('aria-valuenow'))).toBeGreaterThanOrEqual(49);
    expect(Number(bar()?.getAttribute('aria-valuenow'))).toBeLessThanOrEqual(51);
  });
});
