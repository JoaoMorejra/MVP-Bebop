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
