import { describe, expect, it } from 'vitest';
import { evaluateLaunchReadiness } from '../../electron/launchReadiness.cjs';
import { preflightBlockedReason } from './preflightGate';

const VECTORS: Array<{ pct: number; threshold: number | undefined }> = [
  { pct: 28, threshold: 20 },
  { pct: 20, threshold: 20 },
  { pct: 21, threshold: 20 },
  { pct: 5, threshold: 0 },
  { pct: 0, threshold: 0 },
  { pct: 100, threshold: 100 },
  { pct: 20, threshold: undefined },
  { pct: 21, threshold: undefined },
];

describe('battery gate parity between preflightGate.ts and launchReadiness.cjs', () => {
  it.each(VECTORS)('agrees on pct=$pct threshold=$threshold', ({ pct, threshold }) => {
    const gate = preflightBlockedReason({
      paramsStatus: 'ready',
      driverRunning: true,
      benchMode: false,
      connected: true,
      flightReady: true,
      missingTopics: [],
      stale: false,
      flyingState: 0,
      batteryKnown: true,
      batteryPct: pct,
      batteryFailsafeThreshold: threshold,
      bridgeReady: true,
    });
    const verdict = evaluateLaunchReadiness({
      benchMode: false,
      noFly: false,
      configValid: true,
      driverRunning: true,
      orphanPids: [],
      statesLink: true,
      telemetryAgeMs: 0,
      odomFresh: true,
      videoFresh: true,
      missingTopics: [],
      flyingState: 0,
      batteryKnown: true,
      batteryPct: pct,
      batteryFailsafeThreshold: threshold,
      commandBridgeReady: true,
      ros2CliAvailable: true,
      voiceReady: true,
      standbyReady: true,
    });
    expect(verdict.blockedReason).toBe(gate);
  });
});

/**
 * The same `<=` rule as `steps/takeoff.py:_check_prearm_battery`, restated
 * here because vitest cannot import Python: switched off, nothing is refused;
 * on, a known charge at or below the threshold is. An unknown charge is
 * refused by the station gates and let through by the onboard gate, which
 * acts only on a reading (`test/test_prearm_battery_gate.py`).
 */
function onboardRefuses(enabled: boolean, pct: number | null, threshold: number): boolean {
  if (!enabled || pct === null) return false;
  return pct <= threshold;
}

describe('battery switch parity: preflightGate.ts, launchReadiness.cjs and the onboard gate', () => {
  const cases: Array<{ enabled: boolean; pct: number | null }> = [];
  for (const enabled of [true, false]) {
    for (const pct of [null, 0, 19, 20, 21]) cases.push({ enabled, pct });
  }

  it.each(cases)('agrees on enabled=$enabled pct=$pct', ({ enabled, pct }) => {
    const known = pct !== null;
    const gate = preflightBlockedReason({
      paramsStatus: 'ready',
      driverRunning: true,
      benchMode: false,
      connected: true,
      flightReady: true,
      missingTopics: [],
      stale: false,
      flyingState: 0,
      batteryKnown: known,
      batteryPct: pct,
      batteryFailsafeThreshold: 20,
      batteryFailsafeEnabled: enabled,
      bridgeReady: true,
    });
    const verdict = evaluateLaunchReadiness({
      benchMode: false,
      noFly: false,
      configValid: true,
      driverRunning: true,
      orphanPids: [],
      statesLink: true,
      telemetryAgeMs: 0,
      odomFresh: true,
      videoFresh: true,
      missingTopics: [],
      flyingState: 0,
      batteryKnown: known,
      batteryPct: pct,
      batteryFailsafeThreshold: 20,
      batteryFailsafeEnabled: enabled,
      commandBridgeReady: true,
      ros2CliAvailable: true,
      voiceReady: true,
      standbyReady: true,
    });
    expect(verdict.blockedReason).toBe(gate);
    if (known) expect(gate !== null).toBe(onboardRefuses(enabled, pct, 20));
    if (!enabled) expect(gate).toBeNull();
  });
});
