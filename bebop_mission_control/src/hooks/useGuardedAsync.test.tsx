// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useGuardedAsync } from './useGuardedAsync';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;
let hook: ReturnType<typeof useGuardedAsync> | null = null;

const Probe: React.FC<{ fn: () => Promise<void> }> = ({ fn }) => {
  hook = useGuardedAsync(fn);
  return null;
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  hook = null;
});

describe('useGuardedAsync', () => {
  it('a double click runs the body once, before React has rendered the first', async () => {
    let release: () => void = () => undefined;
    const fn = vi.fn(() => new Promise<void>((resolve) => { release = resolve; }));
    act(() => root.render(<Probe fn={fn} />));

    const run = hook!.run;
    void run();
    void run();
    expect(fn).toHaveBeenCalledTimes(1);

    await act(async () => {
      await Promise.resolve();
    });
    expect(hook!.busy).toBe(true);

    await act(async () => {
      release();
      await Promise.resolve();
    });
    expect(hook!.busy).toBe(false);

    await act(async () => {
      const again = hook!.run();
      release();
      await again;
    });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('a failed run releases the guard', async () => {
    const fn = vi.fn(async () => {
      throw new Error('boom');
    });
    act(() => root.render(<Probe fn={fn} />));
    await act(async () => {
      await hook!.run().catch(() => undefined);
    });
    expect(hook!.busy).toBe(false);
    await act(async () => {
      await hook!.run().catch(() => undefined);
    });
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
