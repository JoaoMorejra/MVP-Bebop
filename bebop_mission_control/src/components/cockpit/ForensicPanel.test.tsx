// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ForensicPanel } from './ForensicPanel';
import { isAirborne } from '../../lib/flightState';
import { FINISH_HOLD_MS, FINISH_REASONS, type FinishLockResult } from '../../lib/finishLock';

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

const LOCKED: FinishLockResult = {
  state: 'no_mission',
  enabled: false,
  reason: FINISH_REASONS.noMission,
  requiresConfirm: false,
};
const GROUNDED: FinishLockResult = { state: 'grounded', enabled: true, reason: '', requiresConfirm: false };
const LINK_LOST: FinishLockResult = {
  state: 'link_lost',
  enabled: true,
  reason: FINISH_REASONS.linkLost,
  requiresConfirm: true,
};

function render(lock: FinishLockResult, onFinish = vi.fn()) {
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
        onFinish={onFinish}
        lock={lock}
      />
    )
  );
  const button = container.querySelector('button[data-finish-state]') as HTMLButtonElement;
  return { button, onFinish };
}

describe('Finalizar missão', () => {
  it('is disabled, and says why, with nothing to finish', () => {
    const { button, onFinish } = render(LOCKED);
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('aria-disabled')).toBe('true');
    expect(button.title).toBe('Disponível após iniciar uma missão');
    act(() => button.click());
    expect(onFinish).not.toHaveBeenCalled();
  });

  it.each([
    ['in_flight', FINISH_REASONS.inFlight],
    ['await_ground', FINISH_REASONS.awaitGround],
    ['aborting', FINISH_REASONS.landing],
  ] as const)('locked in %s shows "%s" as its tooltip', (state, reason) => {
    const { button } = render({ state, enabled: false, reason, requiresConfirm: false });
    expect(button.disabled).toBe(true);
    expect(button.title).toBe(reason);
  });

  it('ends the cycle once the aircraft is confirmed on the ground', () => {
    const { button, onFinish } = render(GROUNDED);
    expect(button.disabled).toBe(false);
    expect(button.getAttribute('aria-disabled')).toBe('false');
    act(() => button.click());
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  describe('on a lost link', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    const press = (button: HTMLButtonElement, type: string) =>
      act(() => {
        button.dispatchEvent(new Event(type, { bubbles: true }));
      });

    it('a click does nothing; only a press held for the full duration finishes', () => {
      const { button, onFinish } = render(LINK_LOST);
      expect(button.title).toBe(FINISH_REASONS.linkLost);
      expect(button.textContent?.trim()).toBe('Segure para finalizar');
      act(() => button.click());
      expect(onFinish).not.toHaveBeenCalled();

      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS - 1));
      expect(onFinish).not.toHaveBeenCalled();
      act(() => vi.advanceTimersByTime(1));
      expect(onFinish).toHaveBeenCalledTimes(1);
    });

    it('releasing early cancels the hold', () => {
      const { button, onFinish } = render(LINK_LOST);
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS / 2));
      press(button, 'pointerup');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS));
      expect(onFinish).not.toHaveBeenCalled();
    });

    it('pointer-down repeated within 50 ms finishes once, on the last press held', () => {
      const { button, onFinish } = render(LINK_LOST);
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(25));
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(25));
      press(button, 'pointerdown');
      act(() => vi.advanceTimersByTime(FINISH_HOLD_MS * 3));
      expect(onFinish).toHaveBeenCalledTimes(1);
    });
  });
});

describe('isAirborne', () => {
  it('reads the canonical airborne set: rotors turning, usertakeoff excluded', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8].filter(isAirborne)).toEqual([1, 2, 3, 4, 7, 8]);
    expect(isAirborne(null)).toBe(false);
    expect(isAirborne(undefined)).toBe(false);
  });
});

describe('rotorsTurning', () => {
  it('turns the rotors only in the ARSDK states where they spin', async () => {
    const { rotorsTurning } = await import('../../lib/flightState');
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8].filter(rotorsTurning)).toEqual([1, 2, 3, 4, 7, 8]);
    expect(rotorsTurning(null)).toBe(false);
    expect(rotorsTurning(undefined)).toBe(false);
  });
});
