// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MagnetoCalibration } from '../../types/bmg';
import { MagnetoCalibrationPanel } from './MagnetoCalibrationPanel';

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
});

const running: MagnetoCalibration = {
  sequence: 4, kind: 'axis', x: 1, y: 0, z: 0, failed: 0, required: 1, axis: 'y', started: 1, stamp: 1,
};

const button = (label: string) =>
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes(label))!;

describe('MagnetoCalibrationPanel', () => {
  it('shows each axis as the aircraft reports it and the current instruction', () => {
    act(() => root.render(<MagnetoCalibrationPanel report={running} missionRunning={false} onCommand={vi.fn()} />));
    expect(host.querySelector('[data-axis="x"]')?.getAttribute('data-state')).toBe('done');
    expect(host.querySelector('[data-axis="y"]')?.getAttribute('data-state')).toBe('active');
    expect(host.textContent).toContain('eixo Y');
    expect(button('Iniciar').disabled).toBe(true);
    expect(button('Cancelar').disabled).toBe(false);
  });

  it('starts only on the operator request and shows a refusal', async () => {
    const onCommand = vi.fn(async () => ({ success: false, error: 'ponte de comando indisponível' }));
    const idle = { ...running, started: 0, axis: 'none' as const, x: 0 };
    act(() => root.render(<MagnetoCalibrationPanel report={idle} missionRunning={false} onCommand={onCommand} />));
    expect(onCommand).not.toHaveBeenCalled();
    await act(async () => {
      button('Iniciar').click();
    });
    expect(onCommand).toHaveBeenCalledWith(true);
    expect(host.textContent).toContain('ponte de comando indisponível');
  });

  it('cannot start while a mission runs', () => {
    const idle = { ...running, started: 0, axis: 'none' as const };
    act(() => root.render(<MagnetoCalibrationPanel report={idle} missionRunning onCommand={vi.fn()} />));
    expect(button('Iniciar').disabled).toBe(true);
  });
});
