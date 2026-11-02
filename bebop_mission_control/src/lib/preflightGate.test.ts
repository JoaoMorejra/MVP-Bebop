import { describe, expect, it } from 'vitest';
import { preflightBlockedCode, preflightBlockedReason, preflightBlockedShort, type PreflightGateInput } from './preflightGate';

const ready: PreflightGateInput = {
  paramsStatus: 'ready',
  driverRunning: true,
  benchMode: false,
  connected: true,
  flightReady: true,
  missingTopics: [],
  stale: false,
  batteryKnown: true,
  batteryPct: 85,
};

describe('preflightBlockedReason', () => {
  it('clears a station with everything in place', () => {
    expect(preflightBlockedReason(ready)).toBeNull();
  });

  it.each([
    ['loading', 'Carregando parâmetros'],
    ['saving', 'Gravando parâmetros'],
    ['error', 'Parâmetros indisponíveis'],
  ] as const)('blocks while the parameters are %s', (paramsStatus, reason) => {
    expect(preflightBlockedReason({ ...ready, paramsStatus })).toBe(reason);
    expect(preflightBlockedReason({ ...ready, paramsStatus, benchMode: true })).toBe(reason);
  });

  it('keeps the link gates after the parameters', () => {
    expect(preflightBlockedReason({ ...ready, driverRunning: false })).toBe('Driver ROS 2 fora do ar');
    expect(preflightBlockedReason({ ...ready, connected: false })).toBe(
      'Conecte-se à rede da aeronave para liberar o comando'
    );
    expect(preflightBlockedReason({ ...ready, flightReady: false, missingTopics: ['/bebop/odom'] })).toBe(
      'Tópicos sem tráfego: /bebop/odom'
    );
    expect(preflightBlockedReason({ ...ready, flightReady: false })).toBe('Validando tópicos da aeronave');
    expect(preflightBlockedReason({ ...ready, stale: true })).toBe('Sem odometria recente');
  });

  it('lets the bench run without the aircraft link', () => {
    expect(preflightBlockedReason({ ...ready, benchMode: true, connected: false, flightReady: false })).toBeNull();
  });

  it('blocks real flight when aircraft is not in flying_state 0', () => {
    expect(preflightBlockedReason({ ...ready, flyingState: 2 })).toBe('Aeronave não está em solo (flying_state 2)');
    expect(preflightBlockedReason({ ...ready, flyingState: 0 })).toBeNull();
  });

  it('blocks real flight only when battery is unknown or at or below the operator threshold', () => {
    const gate = (batteryPct: number, batteryFailsafeThreshold?: number) =>
      preflightBlockedReason({ ...ready, batteryKnown: true, batteryPct, batteryFailsafeThreshold });
    expect(preflightBlockedReason({ ...ready, batteryKnown: false })).toBe('Bateria da aeronave desconhecida');
    expect(gate(28, 20)).toBeNull();
    expect(gate(20, 20)).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
    expect(gate(21, 20)).toBeNull();
    expect(gate(5, 0)).toBeNull();
    expect(gate(0, 0)).toBe('Bateria insuficiente para voo real (0% <= limiar 0%)');
    expect(gate(100, 100)).toBe('Bateria insuficiente para voo real (100% <= limiar 100%)');
    expect(gate(20)).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
    expect(gate(21)).toBeNull();
  });

  it('lets a disabled battery failsafe launch on any charge, known or not', () => {
    expect(preflightBlockedReason({ ...ready, batteryFailsafeEnabled: false, batteryKnown: true, batteryPct: 5 })).toBeNull();
    expect(preflightBlockedReason({ ...ready, batteryFailsafeEnabled: false, batteryKnown: false })).toBeNull();
    const enabled = { ...ready, batteryFailsafeEnabled: true, batteryKnown: true, batteryFailsafeThreshold: 20 };
    expect(preflightBlockedReason({ ...enabled, batteryPct: 20 })).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
    expect(preflightBlockedReason({ ...enabled, batteryPct: 21 })).toBeNull();
  });

  it('blocks real flight when command bridge is not ready', () => {
    expect(preflightBlockedReason({ ...ready, bridgeReady: false })).toBe('Ponte de comando não está pronta');
    expect(preflightBlockedReason({ ...ready, bridgeReady: true })).toBeNull();
  });
});

describe('preflightBlockedShort', () => {
  const cases: Array<[string, Partial<PreflightGateInput>, string | null]> = [
    ['clear', {}, null],
    ['loading', { paramsStatus: 'loading' }, 'Parâmetros'],
    ['saving', { paramsStatus: 'saving' }, 'Parâmetros'],
    ['error', { paramsStatus: 'error' }, 'Parâmetros'],
    ['driver', { driverRunning: false }, 'Sem driver'],
    ['bench', { benchMode: true, connected: false, flightReady: false }, null],
    ['link', { connected: false }, 'Sem enlace'],
    ['topics', { flightReady: false, missingTopics: ['/bebop/odom'] }, 'Sem tópicos'],
    ['validating', { flightReady: false }, 'Sem tópicos'],
    ['stale', { stale: true }, 'Sem odometria'],
    ['airborne', { flyingState: 2 }, 'Fora do solo'],
    ['battery unknown', { batteryKnown: false }, 'Bateria?'],
    ['battery low', { batteryPct: 15 }, 'Bateria baixa'],
    ['battery at threshold', { batteryPct: 30, batteryFailsafeThreshold: 30 }, 'Bateria baixa'],
    ['failsafe off', { batteryPct: 5, batteryFailsafeEnabled: false }, null],
    ['bridge', { bridgeReady: false }, 'Sem ponte'],
  ];

  it.each(cases)('preflightBlockedShort is null exactly when preflightBlockedReason is null (%s)', (_name, overrides, short) => {
    const input = { ...ready, ...overrides };
    expect(preflightBlockedShort(input)).toBe(short);
    expect(preflightBlockedShort(input) === null).toBe(preflightBlockedReason(input) === null);
  });

  it('every short label fits 18 characters', () => {
    for (const [, overrides] of cases) {
      const short = preflightBlockedShort({ ...ready, ...overrides });
      if (short !== null) expect(short.length).toBeLessThanOrEqual(18);
    }
  });

  it('exposes the blocking gate code for distinct dial animations', () => {
    expect(preflightBlockedCode(ready)).toBeNull();
    expect(preflightBlockedCode({ ...ready, driverRunning: false })).toBe('driver');
    expect(preflightBlockedCode({ ...ready, paramsStatus: 'loading' })).toBe('params');
    expect(preflightBlockedCode({ ...ready, flightReady: false, missingTopics: ['/bebop/odom'] })).toBe('topics');
  });
});
