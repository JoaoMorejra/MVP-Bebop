import { describe, expect, it } from 'vitest';
import {
  DRIVER_SESSION_GRACE_MS,
  ODOM_SILENT_SEC,
  staleDriverSession,
} from '../../electron/driverSession.cjs';

const LANDED = 0;
const HOVERING = 2;

/** A driver left over from an aircraft that was powered off on the ground. */
const leftover = {
  driverAlive: true,
  missionRunning: false,
  lastFlyingState: LANDED,
  telemetryFresh: true,
  odomAgeSec: 42,
  driverAgeMs: 10 * 60_000,
  bridgeAgeMs: 10 * 60_000,
};

describe('staleDriverSession', () => {
  it('flags a driver that outlived the ARSDK session of an aircraft powered off on the ground', () => {
    expect(staleDriverSession(leftover).stale).toBe(true);
  });

  it('flags it as well when the bridge was rebuilt and never saw odometry from that driver', () => {
    expect(staleDriverSession({ ...leftover, odomAgeSec: null }).stale).toBe(true);
  });

  it('keeps a driver whose odometry is flowing (already connected)', () => {
    expect(staleDriverSession({ ...leftover, odomAgeSec: 0.2 }).stale).toBe(false);
    expect(staleDriverSession({ ...leftover, odomAgeSec: ODOM_SILENT_SEC }).stale).toBe(false);
  });

  it('never restarts while a mission process is up', () => {
    expect(staleDriverSession({ ...leftover, missionRunning: true }).stale).toBe(false);
  });

  it('never restarts when the last known flying state was airborne', () => {
    expect(staleDriverSession({ ...leftover, lastFlyingState: HOVERING }).stale).toBe(false);
  });

  it('never restarts when the flying state was never known', () => {
    expect(staleDriverSession({ ...leftover, lastFlyingState: null }).stale).toBe(false);
  });

  it('leaves a freshly started driver to finish its ARSDK handshake', () => {
    expect(staleDriverSession({ ...leftover, driverAgeMs: DRIVER_SESSION_GRACE_MS - 1 }).stale).toBe(false);
  });

  it('does not read a just-rebuilt bridge with no sample yet as a dead session', () => {
    expect(
      staleDriverSession({ ...leftover, odomAgeSec: null, bridgeAgeMs: DRIVER_SESSION_GRACE_MS - 1 }).stale
    ).toBe(false);
  });

  it('does not judge on frozen telemetry', () => {
    expect(staleDriverSession({ ...leftover, telemetryFresh: false }).stale).toBe(false);
  });

  it('has nothing to restart without a driver', () => {
    expect(staleDriverSession({ ...leftover, driverAlive: false }).stale).toBe(false);
  });

  it('treats a driver of unknown age (not spawned by this station) as old', () => {
    expect(staleDriverSession({ ...leftover, driverAgeMs: null }).stale).toBe(true);
  });
});
