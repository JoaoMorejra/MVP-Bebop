// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ForensicPanel } from './ForensicPanel';
import { isAirborne } from '../../lib/flightState';

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

function render(canFinish: boolean, onFinish = vi.fn()) {
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
        onOpenLibrary={() => undefined}
        onFinish={onFinish}
        canFinish={canFinish}
      />
    )
  );
  const button = Array.from(container.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === 'Finalizar missão'
  ) as HTMLButtonElement;
  return { button, onFinish };
}

describe('Finalizar missão', () => {
  it('is disabled, and says why, with nothing to finish', () => {
    const { button, onFinish } = render(false);
    expect(button.disabled).toBe(true);
    expect(button.title).toBe('Disponível após iniciar uma missão');
    act(() => button.click());
    expect(onFinish).not.toHaveBeenCalled();
  });

  it('ends the cycle when there is one', () => {
    const { button, onFinish } = render(true);
    expect(button.disabled).toBe(false);
    act(() => button.click());
    expect(onFinish).toHaveBeenCalledTimes(1);
  });
});

describe('isAirborne', () => {
  it('reads the ARSDK flying states that can land', () => {
    expect([0, 1, 2, 3, 4, 5, 6].filter(isAirborne)).toEqual([1, 2, 3, 6]);
    expect(isAirborne(null)).toBe(false);
    expect(isAirborne(undefined)).toBe(false);
  });
});
