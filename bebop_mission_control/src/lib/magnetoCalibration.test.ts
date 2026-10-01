import { describe, expect, it } from 'vitest';
import type { MagnetoCalibration } from '../types/bmg';
import { magnetoView } from './magnetoCalibration';

const report = (overrides: Partial<MagnetoCalibration>): MagnetoCalibration => ({
  sequence: 1,
  kind: 'state',
  x: 0,
  y: 0,
  z: 0,
  failed: 0,
  required: 1,
  axis: 'none',
  started: 0,
  stamp: 1,
  ...overrides,
});

describe('magnetoView', () => {
  it('knows nothing before the aircraft has reported', () => {
    const view = magnetoView(null, false);
    expect(view.phase).toBe('unknown');
    expect(view.canStart).toBe(false);
  });

  it('offers the calibration when the aircraft asks for it', () => {
    const view = magnetoView(report({ required: 1 }), false);
    expect(view.phase).toBe('idle');
    expect(view.required).toBe(true);
    expect(view.canStart).toBe(true);
    expect(view.canAbort).toBe(false);
  });

  it('follows the axis the aircraft wants rotated', () => {
    const view = magnetoView(report({ started: 1, x: 1, axis: 'y' }), false);
    expect(view.phase).toBe('running');
    expect(view.axes).toEqual({ x: 'done', y: 'active', z: 'pending' });
    expect(view.instruction).toContain('eixo Y');
    expect(view.canStart).toBe(false);
    expect(view.canAbort).toBe(true);
  });

  it('reports success only as the aircraft states it', () => {
    const view = magnetoView(report({ started: 0, x: 1, y: 1, z: 1, required: 0 }), false);
    expect(view.phase).toBe('done');
    expect(view.instruction).toMatch(/confirmad/i);
  });

  it('reports a failure', () => {
    const view = magnetoView(report({ failed: 1, started: 0 }), false);
    expect(view.phase).toBe('failed');
    expect(view.canStart).toBe(true);
  });

  it('never starts while a mission is up', () => {
    expect(magnetoView(report({ required: 1 }), true).canStart).toBe(false);
  });
});
