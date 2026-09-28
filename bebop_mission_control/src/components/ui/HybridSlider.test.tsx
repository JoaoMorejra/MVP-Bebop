// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExactValueField, HybridSlider } from './HybridSlider';

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

const setNativeValue = (input: HTMLInputElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  setter.call(input, value);
};

describe('HybridSlider', () => {
  it('emits the dragged value rounded to the precision', () => {
    const onChange = vi.fn();
    act(() =>
      root.render(
        <HybridSlider value={0.2} min={0.05} max={0.6} step={0.01} precision={2} label="v" onChange={onChange} />
      )
    );
    const range = container.querySelector('input[type="range"]') as HTMLInputElement;
    act(() => {
      setNativeValue(range, '0.37000000000000005');
      range.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(onChange).toHaveBeenLastCalledWith(0.37);
  });

  it('draws the marker only when one is given', () => {
    act(() =>
      root.render(<HybridSlider value={1} min={0} max={2} step={1} precision={0} label="v" onChange={() => undefined} />)
    );
    expect(container.querySelector('[aria-hidden]')).toBeNull();
    act(() =>
      root.render(
        <HybridSlider value={1} min={0} max={2} step={1} precision={0} label="v" marker={1} onChange={() => undefined} />
      )
    );
    expect(container.querySelector('[aria-hidden]')).not.toBeNull();
  });
});

describe('ExactValueField', () => {
  const renderField = (value: number, onCommit: (v: number) => void) =>
    act(() =>
      root.render(
        <ExactValueField value={value} min={0.5} max={4} precision={1} unit="m" label="Altitude" onCommit={onCommit} />
      )
    );

  const type = (input: HTMLInputElement, text: string) => {
    act(() => {
      setNativeValue(input, text);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => {
      input.focus();
      input.blur();
    });
  };

  it('commits a clamped, rounded value on blur and accepts a decimal comma', () => {
    const onCommit = vi.fn();
    renderField(1.8, onCommit);
    const input = container.querySelector('input') as HTMLInputElement;
    type(input, '9');
    expect(onCommit).toHaveBeenLastCalledWith(4);
    type(input, '2,46');
    expect(onCommit).toHaveBeenLastCalledWith(2.5);
  });

  it('restores the committed value when the entry does not parse', () => {
    const onCommit = vi.fn();
    renderField(1.8, onCommit);
    const input = container.querySelector('input') as HTMLInputElement;
    type(input, 'abc');
    expect(onCommit).not.toHaveBeenCalled();
    expect(input.value).toBe('1.8');
  });

  it('follows the value when the slider moves it', () => {
    renderField(1.8, () => undefined);
    renderField(3.2, () => undefined);
    expect((container.querySelector('input') as HTMLInputElement).value).toBe('3.2');
  });
});
