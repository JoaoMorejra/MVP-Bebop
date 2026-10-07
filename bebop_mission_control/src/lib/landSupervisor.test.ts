import { describe, expect, it, vi } from 'vitest';
import {
  evaluateLandStep,
  createLandSupervisor,
} from '../../electron/landSupervisor.cjs';

describe('evaluateLandStep (pure decision)', () => {
  it('identifies landed immediately when flying_state is 0', () => {
    const decision = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 1000,
        cliBackupSent: false,
        flyingState: 0,
      },
      1500
    );
    expect(decision).toEqual({
      phase: 'landed',
      shouldResendBridge: false,
      shouldSendCliBackup: false,
      isDone: true,
    });
  });

  it('identifies landed when flying_state is 5 (emergency motor cut)', () => {
    const decision = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 1000,
        cliBackupSent: false,
        flyingState: 5,
      },
      2000
    );
    expect(decision.phase).toBe('landed');
    expect(decision.isDone).toBe(true);
  });

  it('marks landing for states 4 and 8 without triggering resends', () => {
    for (const state of [4, 8]) {
      const decision = evaluateLandStep(
        {
          startedAt: 1000,
          lastBridgeResendAt: 1000,
          cliBackupSent: false,
          flyingState: state,
        },
        3500
      );
      expect(decision).toEqual({
        phase: 'landing',
        shouldResendBridge: false,
        shouldSendCliBackup: false,
        isDone: false,
      });
    }
  });

  it('commands landing and respects bridge rate limit (<= 1/s)', () => {
    // 500 ms after start: no resend yet
    const d1 = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 1000,
        cliBackupSent: false,
        flyingState: 2,
      },
      1500
    );
    expect(d1.phase).toBe('commanded');
    expect(d1.shouldResendBridge).toBe(false);
    expect(d1.shouldSendCliBackup).toBe(false);
    expect(d1.isDone).toBe(false);

    // 1000 ms after lastBridgeResendAt: resend allowed
    const d2 = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 1000,
        cliBackupSent: false,
        flyingState: 2,
      },
      2000
    );
    expect(d2.phase).toBe('commanded');
    expect(d2.shouldResendBridge).toBe(true);
  });

  it('triggers CLI backup once at elapsed >= 3000 ms', () => {
    // At 2900 ms: not yet
    const d1 = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 2000,
        cliBackupSent: false,
        flyingState: 3,
      },
      3900
    );
    expect(d1.shouldSendCliBackup).toBe(false);

    // At 3000 ms: trigger CLI backup
    const d2 = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 3000,
        cliBackupSent: false,
        flyingState: 3,
      },
      4000
    );
    expect(d2.shouldSendCliBackup).toBe(true);

    // Once sent, do not send again
    const d3 = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 4000,
        cliBackupSent: true,
        flyingState: 3,
      },
      5000
    );
    expect(d3.shouldSendCliBackup).toBe(false);
  });

  it('times out to unconfirmed after 15 s if not landed', () => {
    const decision = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 14000,
        cliBackupSent: true,
        flyingState: 2,
      },
      16000, // elapsed 15000 ms
      15000
    );
    expect(decision).toEqual({
      phase: 'unconfirmed',
      shouldResendBridge: false,
      shouldSendCliBackup: false,
      isDone: true,
    });
  });

  it('times out to unconfirmed even if stuck in landing state after 15 s', () => {
    const decision = evaluateLandStep(
      {
        startedAt: 1000,
        lastBridgeResendAt: 1000,
        cliBackupSent: true,
        flyingState: 4,
      },
      16500, // elapsed 15500 ms
      15000
    );
    expect(decision.phase).toBe('unconfirmed');
    expect(decision.isDone).toBe(true);
  });
});

describe('createLandSupervisor (executor)', () => {
  it('manages lifecycle, bridge resends, CLI backup, and transition to landed', () => {
    let now = 1000;
    let flyingState: number = 2; // hovering
    const events: Array<{ phase: string; t: number }> = [];
    const bridgeCalls: number[] = [];
    const cliCalls: number[] = [];
    const logs: string[] = [];

    const supervisor = createLandSupervisor({
      getFlyingState: () => flyingState,
      sendBridgeLand: () => bridgeCalls.push(now),
      sendCliBackup: () => cliCalls.push(now),
      emitProgress: (e: { phase: string; t: number }) => events.push(e),
      recordLog: (_chan: string, entry: { text: string }) => logs.push(entry.text),
      clock: () => now,
      intervalMs: 500,
      timeoutMs: 15000,
    });

    supervisor.start();
    expect(supervisor.isActive()).toBe(true);
    // Initial evaluation emitted 'commanded'
    expect(events).toEqual([{ phase: 'commanded', t: 1000 }]);
    expect(bridgeCalls.length).toBe(0); // initial command was sent before supervisor start

    // Step at +500 ms (1500 ms)
    now = 1500;
    supervisor.step();
    expect(bridgeCalls.length).toBe(0);
    expect(cliCalls.length).toBe(0);

    // Step at +1000 ms (2000 ms): bridge resend
    now = 2000;
    supervisor.step();
    expect(bridgeCalls).toEqual([2000]);
    expect(cliCalls.length).toBe(0);

    // Step at +2000 ms (3000 ms): bridge resend #2
    now = 3000;
    supervisor.step();
    expect(bridgeCalls).toEqual([2000, 3000]);
    expect(cliCalls.length).toBe(0);

    // Step at +3000 ms (4000 ms): bridge resend #3 AND CLI backup
    now = 4000;
    supervisor.step();
    expect(bridgeCalls).toEqual([2000, 3000, 4000]);
    expect(cliCalls).toEqual([4000]);

    // Aircraft transitions to landing (4)
    flyingState = 4;
    now = 4500;
    supervisor.step();
    expect(events.map((e) => e.phase)).toEqual(['commanded', 'landing']);
    expect(bridgeCalls.length).toBe(3); // no more bridge resends while landing

    // Aircraft touches down (0)
    flyingState = 0;
    now = 6000;
    supervisor.step();
    expect(events.map((e) => e.phase)).toEqual(['commanded', 'landing', 'landed']);
    expect(supervisor.isActive()).toBe(false); // stopped when isDone
  });

  it('stops and emits unconfirmed when timing out', () => {
    let now = 1000;
    const events: Array<{ phase: string; t: number }> = [];

    const supervisor = createLandSupervisor({
      getFlyingState: () => 2, // never lands
      sendBridgeLand: () => {},
      sendCliBackup: () => {},
      emitProgress: (e: { phase: string; t: number }) => events.push(e),
      clock: () => now,
      intervalMs: 500,
      timeoutMs: 15000,
    });

    supervisor.start();
    expect(events).toEqual([{ phase: 'commanded', t: 1000 }]);

    // Jump past timeout
    now = 16500;
    supervisor.step();
    expect(events).toEqual([
      { phase: 'commanded', t: 1000 },
      { phase: 'unconfirmed', t: 16500 },
    ]);
    expect(supervisor.isActive()).toBe(false);
  });
});
