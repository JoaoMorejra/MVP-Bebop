import type { MissionState, Screen } from '../types/mission';
import { isAirborne } from './flightState';
import { isMissionOver } from './missionOutcome';

/**
 * When the pre-flight dial may be skipped for the cockpit.
 *
 * The normal path into the cockpit is the launch sequence reaching 100 %
 * (`useLaunchSequence`): parameters committed, link ready, the mission's stage
 * and countdown confirmed. The shortcuts below exist only so an aircraft that
 * is actually flying is never watched from the dial.
 *
 * Odometry altitude is not evidence of flight: on the ground it drifts, and a
 * ground reference left by the previous flight read above 0.35 m at the click,
 * which threw the operator into a cockpit with nothing running.
 */

export interface AirborneHandoffFacts {
  missionState: MissionState;
  flyingState: number | null | undefined;
  stage: number;
  counting: boolean;
  screen: Screen;
}

/** The aircraft is flying under a confirmed mission process while the dial is still up. */
export function airborneHandoff(facts: AirborneHandoffFacts): boolean {
  if (!facts.counting && facts.screen !== 'preflight') return false;
  if (facts.missionState !== 'running') return false;
  return isAirborne(facts.flyingState) || facts.stage >= 2;
}

export interface CountdownExitFacts {
  counting: boolean;
  spawnRequested: boolean;
  missionState: MissionState;
  flyingState: number | null | undefined;
  /** The mission refused its launch document (exit 78); handled on its own. */
  refused: boolean;
  bench: boolean;
}

/**
 * Where a mission process that exited during the countdown is shown.
 *
 * A flight that never left the ground stays on pre-flight with the reason: a
 * cockpit over a dead process froze every stage at zero and read as "all
 * stages skipped". An aircraft already in the air, and a bench routine, are
 * shown in the cockpit.
 */
export function countdownExitHandoff(facts: CountdownExitFacts): 'cockpit' | 'preflight' | null {
  if (!facts.counting || !facts.spawnRequested || !isMissionOver(facts.missionState)) return null;
  if (facts.refused) return null;
  if (facts.bench || isAirborne(facts.flyingState)) return 'cockpit';
  return 'preflight';
}
