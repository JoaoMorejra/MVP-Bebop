import type { MissionState } from '../types/mission';
import { isAirborne } from './flightState';

/**
 * The lock on "Finalizar Missão", as the state machine of section 7 of
 * `docs/RELATORIO_VERIFICACAO_E2E_2026-09-30.md`.
 *
 * The button ends the cycle and returns the station to pre-flight. It used to
 * be clickable whenever a mission had been started, so an operator could end
 * the cycle with the aircraft still in the air. It now unlocks only on a
 * deterministic outcome: the mission process gone and the aircraft reporting
 * ground, continuously, on fresh telemetry from the aircraft itself.
 */
export type FinishLockState =
  | 'no_mission'
  | 'in_flight'
  | 'aborting'
  | 'await_exit'
  | 'await_ground'
  | 'grounded'
  | 'link_lost'
  | 'finishing';

export interface FinishLockInput {
  missionState: MissionState;
  /** A mission process exists or is being spawned. */
  processUp: boolean;
  exitCode: number | null;
  /** The run is `--no-fly`: grounding comes from the simulator. */
  benchMode: boolean;
  /** Effective `flying_state` from the telemetry bridge, or null while unknown. */
  flyingState: number | null;
  /** Epoch ms at which `flyingState` took its current value (null included). */
  flyingStateSince: number;
  navFresh: boolean;
  navSource: 'aircraft' | 'simulator' | 'none' | undefined;
  /** Seconds since the bridge rendered the telemetry frame, or null without one. */
  telemetryAgeSec: number | null;
  /** The finish has been clicked and `endMission` has not resolved. */
  finishing: boolean;
  /** Last flying state the simulator reported during this run, or null. */
  benchFlyingState: number | null;
  /** Epoch ms at which the mission process reported its exit, or null. */
  exitedAt: number | null;
}

export interface FinishLockResult {
  state: FinishLockState;
  enabled: boolean;
  /** Why the button is locked, or a warning on an enabled state; '' otherwise. */
  reason: string;
  /** Enabled only through a press held for `FINISH_HOLD_MS`. */
  requiresConfirm: boolean;
}

/** `flying_state === 0` must hold continuously this long before unlocking, ms. */
export const GROUND_CONFIRM_MS = 2000;

/** A telemetry frame older than this is not evidence of the current state, s. */
export const TELEMETRY_FRESH_SEC = 1.5;

/** Silence after the exit for this long is a lost link, ms. */
export const LINK_LOST_MS = 10000;

/** Hold-to-confirm duration on a lost link, ms. */
export const FINISH_HOLD_MS = 1500;

/** ARSDK `emergency`: motors cut, the airframe is on the ground or falling. */
const FLYING_STATE_EMERGENCY = 5;
const FLYING_STATE_LANDED = 0;
/** `landing` and `emergency_landing`. */
const LANDING_STATES: ReadonlySet<number> = new Set([4, 8]);

export const FINISH_REASONS = {
  noMission: 'Disponível após iniciar uma missão',
  inFlight: 'Em voo: use Abortar Missão',
  running: 'Missão em execução: use Abortar Missão',
  landing: 'Pousando...',
  awaitGround: 'Aguardando confirmação de pouso (flying_state)',
  linkLost: 'Link perdido: confirme visualmente e segure para finalizar',
  emergency: 'Emergência: motores cortados',
  finishing: 'Finalizando...',
} as const;

const locked = (state: FinishLockState, reason: string): FinishLockResult => ({
  state,
  enabled: false,
  reason,
  requiresConfirm: false,
});

/**
 * Whether the post-landing report may begin. Its introduction states that the
 * aircraft is on the ground ("Aeronave em solo"), so it waits for the same
 * confirmation that unlocks "Finalizar Missão"; a lost link is not one.
 */
export function reportMayStart(lock: FinishLockResult): boolean {
  return lock.state === 'grounded';
}

export function finishLockState(input: FinishLockInput, now: number): FinishLockResult {
  if (input.finishing) return locked('finishing', FINISH_REASONS.finishing);

  const flying = input.flyingState;
  const airborne = isAirborne(flying);
  const airborneReason =
    typeof flying === 'number' && LANDING_STATES.has(flying) ? FINISH_REASONS.landing : FINISH_REASONS.inFlight;

  if (input.processUp) {
    if (input.missionState === 'aborting') return locked('aborting', FINISH_REASONS.landing);
    return locked('in_flight', airborne ? FINISH_REASONS.inFlight : FINISH_REASONS.running);
  }

  if (airborne) return locked('await_ground', airborneReason);

  if (input.exitedAt === null) return locked('no_mission', FINISH_REASONS.noMission);

  const fresh =
    flying !== null &&
    input.navFresh &&
    input.telemetryAgeSec !== null &&
    input.telemetryAgeSec < TELEMETRY_FRESH_SEC;

  if (input.benchMode && input.benchFlyingState !== null) {
    if (input.benchFlyingState === FLYING_STATE_LANDED) {
      return { state: 'grounded', enabled: true, reason: '', requiresConfirm: false };
    }
    if (isAirborne(input.benchFlyingState)) return locked('await_ground', FINISH_REASONS.inFlight);
  }

  if (fresh && flying === FLYING_STATE_EMERGENCY) {
    return { state: 'grounded', enabled: true, reason: FINISH_REASONS.emergency, requiresConfirm: false };
  }

  if (
    fresh &&
    flying === FLYING_STATE_LANDED &&
    input.navSource === 'aircraft' &&
    now - input.flyingStateSince >= GROUND_CONFIRM_MS
  ) {
    return { state: 'grounded', enabled: true, reason: '', requiresConfirm: false };
  }

  if (!fresh) {
    const silenceMs =
      flying === null
        ? now - input.flyingStateSince
        : input.telemetryAgeSec === null
        ? Number.POSITIVE_INFINITY
        : input.telemetryAgeSec * 1000;
    if (silenceMs >= LINK_LOST_MS && now - input.exitedAt >= LINK_LOST_MS) {
      return { state: 'link_lost', enabled: true, reason: FINISH_REASONS.linkLost, requiresConfirm: true };
    }
  }

  return locked('await_ground', FINISH_REASONS.awaitGround);
}
