import { describe, expect, it } from 'vitest';
import {
  FINISH_HOLD_MS,
  GROUND_CONFIRM_MS,
  LINK_LOST_MS,
  TELEMETRY_FRESH_SEC,
  finishLockState,
  type FinishLockInput,
} from './finishLock';

const NOW = 1_000_000;

/** A real flight that exited 3 s ago with the aircraft landed for 5 s. */
function input(overrides: Partial<FinishLockInput> = {}): FinishLockInput {
  return {
    missionState: 'finished',
    processUp: false,
    exitCode: 0,
    benchMode: false,
    flyingState: 0,
    flyingStateSince: NOW - 5000,
    navFresh: true,
    navSource: 'aircraft',
    telemetryAgeSec: 0.2,
    finishing: false,
    benchFlyingState: null,
    exitedAt: NOW - 3000,
    ...overrides,
  };
}

const lock = (overrides: Partial<FinishLockInput> = {}) => finishLockState(input(overrides), NOW);

describe('finishLockState: no mission', () => {
  it('is disabled with nothing to finish', () => {
    const result = lock({ missionState: 'idle', exitCode: null, exitedAt: null });
    expect(result).toMatchObject({ state: 'no_mission', enabled: false, requiresConfirm: false });
    expect(result.reason).toBe('Disponível após iniciar uma missão');
  });

  it('an aircraft flying without a mission waits for ground, and points at Abortar', () => {
    const result = lock({ missionState: 'idle', exitCode: null, exitedAt: null, flyingState: 2 });
    expect(result).toMatchObject({ state: 'await_ground', enabled: false });
    expect(result.reason).toBe('Em voo: use Abortar Missão');
  });
});

describe('finishLockState: mission process up', () => {
  it.each([1, 2, 3, 4, 7, 8])('flying state %i in flight is locked', (flyingState) => {
    const result = lock({ missionState: 'running', processUp: true, exitCode: null, exitedAt: null, flyingState });
    expect(result).toMatchObject({ state: 'in_flight', enabled: false });
  });

  it('says to use Abortar while flying', () => {
    expect(lock({ missionState: 'running', processUp: true, exitedAt: null, flyingState: 2 }).reason).toBe(
      'Em voo: use Abortar Missão'
    );
  });

  it('is locked during the countdown on the ground as well', () => {
    const result = lock({ missionState: 'arming', processUp: true, exitedAt: null, flyingState: 0 });
    expect(result).toMatchObject({ state: 'in_flight', enabled: false });
    expect(result.reason).toBe('Missão em execução: use Abortar Missão');
  });

  it('a landed, fresh aircraft does not unlock while the process is still up', () => {
    expect(lock({ missionState: 'running', processUp: true, exitedAt: null }).enabled).toBe(false);
  });

  it('is "Pousando..." while aborting', () => {
    const result = lock({ missionState: 'aborting', processUp: true, exitedAt: null, flyingState: 4 });
    expect(result).toMatchObject({ state: 'aborting', enabled: false, reason: 'Pousando...' });
  });

  it('a bench run in progress is locked', () => {
    const result = lock({ benchMode: true, missionState: 'running', processUp: true, exitedAt: null, navSource: 'simulator' });
    expect(result.state).toBe('in_flight');
  });
});

describe('finishLockState: after exit, real flight', () => {
  it('unlocks with landed continuously for the confirmation window, fresh, from the aircraft', () => {
    expect(lock()).toMatchObject({ state: 'grounded', enabled: true, requiresConfirm: false, reason: '' });
  });

  it('waits out the continuous-landed window', () => {
    const early = lock({ flyingStateSince: NOW - (GROUND_CONFIRM_MS - 1) });
    expect(early).toMatchObject({ state: 'await_ground', enabled: false });
    expect(early.reason).toBe('Aguardando confirmação de pouso (flying_state)');
    expect(lock({ flyingStateSince: NOW - GROUND_CONFIRM_MS }).state).toBe('grounded');
  });

  it.each([1, 2, 3, 7])('flying state %i after exit waits for ground', (flyingState) => {
    const result = lock({ flyingState, flyingStateSince: NOW - 60000 });
    expect(result).toMatchObject({ state: 'await_ground', enabled: false, reason: 'Em voo: use Abortar Missão' });
  });

  it.each([4, 8])('flying state %i after exit reads as landing', (flyingState) => {
    expect(lock({ flyingState }).reason).toBe('Pousando...');
  });

  it('usertakeoff (6) is not a confirmed landing', () => {
    expect(lock({ flyingState: 6 }).state).toBe('await_ground');
  });

  it('refuses a landed reading that is stale, simulated or not fresh', () => {
    expect(lock({ telemetryAgeSec: TELEMETRY_FRESH_SEC }).state).toBe('await_ground');
    expect(lock({ telemetryAgeSec: null }).state).toBe('await_ground');
    expect(lock({ navSource: 'simulator' }).state).toBe('await_ground');
    expect(lock({ navSource: 'none' }).state).toBe('await_ground');
    expect(lock({ navFresh: false }).state).toBe('await_ground');
  });

  it('emergency (5, motors cut) is grounded, with a warning', () => {
    const result = lock({ flyingState: 5, flyingStateSince: NOW });
    expect(result).toMatchObject({ state: 'grounded', enabled: true, requiresConfirm: false });
    expect(result.reason).toBe('Emergência: motores cortados');
  });

  it('an unknown state waits, then becomes link lost with hold-to-confirm', () => {
    expect(lock({ flyingState: null, flyingStateSince: NOW - 5000, exitedAt: NOW - 5000 }).state).toBe('await_ground');
    const lost = lock({
      flyingState: null,
      flyingStateSince: NOW - (LINK_LOST_MS + 1),
      exitedAt: NOW - (LINK_LOST_MS + 1),
    });
    expect(lost).toMatchObject({ state: 'link_lost', enabled: true, requiresConfirm: true });
    expect(lost.reason).toBe('Link perdido: confirme visualmente e segure para finalizar');
  });

  it('link lost needs the silence to outlast the exit by the window, not just the silence', () => {
    const result = lock({ flyingState: null, flyingStateSince: NOW - 60000, exitedAt: NOW - 2000 });
    expect(result.state).toBe('await_ground');
  });

  it('a stale telemetry frame counts as silence for link lost', () => {
    const result = lock({ telemetryAgeSec: 30, flyingStateSince: NOW - 60000, exitedAt: NOW - 60000 });
    expect(result.state).toBe('link_lost');
  });

  it('link lost returns to await_ground the moment an airborne state arrives', () => {
    expect(lock({ flyingState: 2, flyingStateSince: NOW, exitedAt: NOW - 60000 }).state).toBe('await_ground');
  });
});

describe('finishLockState: after exit, bench', () => {
  const bench = (overrides: Partial<FinishLockInput> = {}) =>
    lock({ benchMode: true, navSource: 'none', flyingState: null, flyingStateSince: NOW - 4000, ...overrides });

  it('unlocks on exit with the simulator last reporting landed', () => {
    expect(bench({ benchFlyingState: 0 })).toMatchObject({ state: 'grounded', enabled: true });
  });

  it('waits while the simulator last reported airborne', () => {
    expect(bench({ benchFlyingState: 2 }).state).toBe('await_ground');
  });

  it('falls back to link lost with hold-to-confirm when the simulator never reported', () => {
    const result = bench({ benchFlyingState: null, flyingStateSince: NOW - 60000, exitedAt: NOW - 60000 });
    expect(result).toMatchObject({ state: 'link_lost', requiresConfirm: true });
  });

  it('a real aircraft reporting flight overrides a landed simulator', () => {
    expect(bench({ benchFlyingState: 0, flyingState: 2, navSource: 'aircraft' }).state).toBe('await_ground');
  });
});

describe('finishLockState: finishing', () => {
  it('is locked once the finish has been clicked, until it resolves', () => {
    expect(lock({ finishing: true })).toMatchObject({ state: 'finishing', enabled: false });
  });

  it('the hold duration is the specified 1.5 s', () => {
    expect(FINISH_HOLD_MS).toBe(1500);
  });
});

describe('reportMayStart', () => {
  it('lets the report (which opens with "Aeronave em solo") start only on confirmed ground', async () => {
    const { reportMayStart } = await import('./finishLock');
    expect(reportMayStart(lock())).toBe(true);
    expect(reportMayStart(lock({ flyingState: 5, flyingStateSince: NOW }))).toBe(true);
    expect(reportMayStart(lock({ flyingStateSince: NOW - 100 }))).toBe(false);
    expect(reportMayStart(lock({ flyingState: 4 }))).toBe(false);
    expect(reportMayStart(lock({ flyingState: null, flyingStateSince: NOW - 60000, exitedAt: NOW - 60000 }))).toBe(false);
    expect(reportMayStart(lock({ finishing: true }))).toBe(false);
  });
});
