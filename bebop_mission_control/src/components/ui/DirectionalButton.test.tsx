// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DirectionalButton, nearestEdgePoint } from './DirectionalButton';
import { ExactValueField, REJECT_HOLD_MS, SCRUB_PX_PER_STEP, rejectionReason, scrubValue } from './HybridSlider';

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
  vi.useRealTimers();
});

const Pointer: typeof MouseEvent = (window as unknown as { PointerEvent?: typeof MouseEvent }).PointerEvent ?? MouseEvent;
const BOX = { left: 0, top: 0, width: 200, height: 40 };

describe('nearestEdgePoint', () => {
  it('enters from the nearest edge, at the pointer position along it', () => {
    expect(nearestEdgePoint(BOX, 2, 20)).toEqual(['0%', '50.0%']);
    expect(nearestEdgePoint(BOX, 198, 10)).toEqual(['100%', '25.0%']);
    expect(nearestEdgePoint(BOX, 50, 1)).toEqual(['25.0%', '0%']);
    expect(nearestEdgePoint(BOX, 150, 39)).toEqual(['75.0%', '100%']);
  });

  it('falls back to the centre for an empty box', () => {
    expect(nearestEdgePoint({ left: 0, top: 0, width: 0, height: 0 }, 5, 5)).toEqual(['50%', '50%']);
  });
});

describe('DirectionalButton', () => {
  it('aims the fill at the entry edge and fills while hovered', () => {
    act(() => root.render(<DirectionalButton>Aruco</DirectionalButton>));
    const button = host.querySelector('button')!;
    button.getBoundingClientRect = () => BOX as DOMRect;
    act(() => {
      button.dispatchEvent(new Pointer('pointerover', { bubbles: true, clientX: 2, clientY: 20 }));
    });
    expect(button.style.getPropertyValue('--edge-x')).toBe('0%');
    expect(button.dataset.hover).toBe('true');
    act(() => {
      button.dispatchEvent(new Pointer('pointerout', { bubbles: true, clientX: 198, clientY: 20, relatedTarget: document.body }));
    });
    expect(button.style.getPropertyValue('--edge-x')).toBe('100%');
    expect(button.dataset.hover).toBeUndefined();
    expect(button.textContent?.trim()).toBe('Aruco');
  });
});

describe('ExactValueField rejection', () => {
  it('names the reason of a corrected or refused figure', () => {
    expect(rejectionReason('9', 0.1, 3, 2, 'm')).toBe('Máximo 3,00 m');
    expect(rejectionReason('0', 0.1, 3, 2, 'm')).toBe('Mínimo 0,10 m');
    expect(rejectionReason('abc', 0.1, 3, 2, 'm')).toBe('Valor inválido');
    expect(rejectionReason('1,5', 0.1, 3, 2, 'm')).toBeNull();
  });

  it('shakes, marks the field and reports the reason, then clears the mark', () => {
    vi.useFakeTimers();
    const onReject = vi.fn();
    const onCommit = vi.fn();
    act(() =>
      root.render(
        <ExactValueField value={1} min={0.1} max={3} precision={2} unit="m" label="Altitude" variant="display" onCommit={onCommit} onReject={onReject} />
      )
    );
    const input = host.querySelector('input')!;
    const shell = host.querySelector('label')!;
    act(() => input.focus());
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, '9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.blur());
    expect(onCommit).toHaveBeenCalledWith(3);
    expect(onReject).toHaveBeenCalledWith('Máximo 3,00 m');
    expect(shell.classList.contains('is-shaking')).toBe(true);
    expect(shell.hasAttribute('data-error')).toBe(true);
    act(() => {
      vi.advanceTimersByTime(REJECT_HOLD_MS + 10);
    });
    expect(shell.hasAttribute('data-error')).toBe(false);
  });
});

describe('ExactValueField scrubbing', () => {
  it('moves one step every few pixels, clamped to the range', () => {
    expect(scrubValue(8, SCRUB_PX_PER_STEP * 5, 1, 0, 586, 0)).toBe(13);
    expect(scrubValue(8, -SCRUB_PX_PER_STEP * 20, 1, 0, 586, 0)).toBe(0);
    expect(scrubValue(0.2, SCRUB_PX_PER_STEP * 3, 0.005, 0.05, 1, 3)).toBe(0.215);
    expect(scrubValue(8, 1, 1, 0, 586, 0)).toBe(8);
  });

  it('drags the figure sideways and lifts it while it moves; a still press opens typing', () => {
    const onCommit = vi.fn();
    act(() =>
      root.render(
        <ExactValueField value={8} min={0} max={586} precision={0} label="ID" variant="display" scrubStep={1} onCommit={onCommit} />
      )
    );
    const shell = host.querySelector('label')!;
    const input = host.querySelector('input')!;
    expect(shell.hasAttribute('data-scrub')).toBe(true);
    act(() => {
      shell.dispatchEvent(new Pointer('pointerdown', { bubbles: true, clientX: 100, button: 0 }));
    });
    act(() => {
      shell.dispatchEvent(new Pointer('pointermove', { bubbles: true, clientX: 100 + SCRUB_PX_PER_STEP * 4 }));
    });
    expect(shell.hasAttribute('data-scrubbing')).toBe(true);
    expect(onCommit).toHaveBeenLastCalledWith(12);
    act(() => {
      shell.dispatchEvent(new Pointer('pointerup', { bubbles: true, clientX: 100 + SCRUB_PX_PER_STEP * 4 }));
    });
    expect(shell.hasAttribute('data-scrubbing')).toBe(false);
    expect(document.activeElement).not.toBe(input);

    act(() => {
      shell.dispatchEvent(new Pointer('pointerdown', { bubbles: true, clientX: 50, button: 0 }));
    });
    act(() => {
      shell.dispatchEvent(new Pointer('pointerup', { bubbles: true, clientX: 50 }));
    });
    expect(document.activeElement).toBe(input);
  });
});
