import { describe, expect, it } from 'vitest';
import { preflightBlockedReason, type PreflightGateInput } from './preflightGate';

const ready: PreflightGateInput = {
  paramsStatus: 'ready',
  driverRunning: true,
  benchMode: false,
  connected: true,
  flightReady: true,
  missingTopics: [],
  stale: false,
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
});
