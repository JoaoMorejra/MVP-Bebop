import { describe, expect, it } from 'vitest';
import { FLIGHT_MODES, NO_FLY_PATH, parametersEnabled, readFlightMode } from './flightMode';

describe('flightMode', () => {
  it('readFlightMode returns test when no_fly is true regardless of mission_mode', () => {
    expect(readFlightMode({ no_fly: true, mission_mode: 'simple' })).toBe('test');
    expect(readFlightMode({ no_fly: true, mission_mode: 'advanced' })).toBe('test');
  });

  it('readFlightMode falls back to mission_mode when no_fly is false or absent', () => {
    expect(readFlightMode({ no_fly: false, mission_mode: 'simple' })).toBe('simple');
    expect(readFlightMode({ mission_mode: 'advanced' })).toBe('advanced');
    expect(readFlightMode({})).toBe('advanced');
    expect(readFlightMode(null)).toBe('advanced');
  });

  it('readFlightMode treats a non-boolean no_fly as not test', () => {
    expect(readFlightMode({ [NO_FLY_PATH]: 'true', mission_mode: 'simple' })).toBe('simple');
    expect(readFlightMode({ [NO_FLY_PATH]: 1, mission_mode: 'advanced' })).toBe('advanced');
  });

  it('FLIGHT_MODES labels are Simples, Avançado, Teste in that order', () => {
    expect(FLIGHT_MODES.map((mode) => mode.id)).toEqual(['simple', 'advanced', 'test']);
    expect(FLIGHT_MODES.map((mode) => mode.label)).toEqual(['Simples', 'Avançado', 'Teste']);
  });

  it('parametersEnabled is false only for test', () => {
    expect(parametersEnabled('simple')).toBe(true);
    expect(parametersEnabled('advanced')).toBe(true);
    expect(parametersEnabled('test')).toBe(false);
  });
});
