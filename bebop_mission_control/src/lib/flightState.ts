import type { MissionState } from '../types/mission';
import type { LandProgressPhase } from '../types/bmg';

/**
 * The canonical airborne set: ARSDK flying states in which the rotors are
 * turning -- takingoff (1), hovering (2), flying (3), landing (4),
 * motor_ramping (7) and emergency_landing (8). Not landed (0), not emergency
 * (5, motors cut) and not usertakeoff (6, waiting on the ground for a hand
 * launch).
 *
 * The same set is mirrored in `electron/main.cjs:AIRBORNE_FLYING_STATES` and
 * `streamer/telemetry_bridge.py:AIRBORNE_STATES`, and the three are compared by
 * `test/test_contracts.py`. They used to disagree: 6 counted as airborne in
 * all three, 4 and 8 in two of them, 7 in none.
 */
export const AIRBORNE_STATES: readonly number[] = [1, 2, 3, 4, 7, 8];

const AIRBORNE_SET: ReadonlySet<number> = new Set(AIRBORNE_STATES);

/** Whether the aircraft reports itself in the air. An unknown state is not. */
export function isAirborne(flyingState: number | null | undefined): boolean {
  return typeof flyingState === 'number' && AIRBORNE_SET.has(flyingState);
}

/** Whether the rotors are turning: the same canonical set. */
export function rotorsTurning(flyingState: number | null | undefined): boolean {
  return isAirborne(flyingState);
}

/**
 * Whether the abort control can be pressed.
 *
 * While a mission is arming or running, whenever the aircraft reports an
 * airborne state regardless of the mission process, or when landing is
 * unconfirmed (R6 / L2), enabling the operator to re-send the land command.
 */
export function abortEnabled(
  missionState: MissionState,
  flyingState: number | null | undefined,
  landProgress?: LandProgressPhase | null
): boolean {
  if (landProgress === 'unconfirmed') return true;
  return missionState === 'running' || missionState === 'arming' || isAirborne(flyingState);
}

/**
 * The flying-state readout of the status bar.
 *
 * The reported label, or "estado desconhecido" when the link is up and the
 * aircraft has not reported one. It used to fall back to "em solo", which
 * presented an unknown state as a landed aircraft.
 */
export function flyingStateLabel(label: string | undefined, connected: boolean): string {
  if (label && label !== 'unknown') return label;
  return connected ? 'estado desconhecido' : '—';
}

/**
 * Human-readable label for the land progress phase (R6).
 */
export function landProgressLabel(phase: LandProgressPhase | null | undefined): string | null {
  switch (phase) {
    case 'commanded':
      return 'Pouso comandado';
    case 'landing':
      return 'Pousando';
    case 'landed':
      return 'Pousada';
    case 'unconfirmed':
      return 'Pouso não confirmado: reenviar';
    default:
      return null;
  }
}
