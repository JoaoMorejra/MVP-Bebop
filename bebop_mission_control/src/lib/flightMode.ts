/**
 * The three flight modes the pre-flight selector offers.
 *
 * Two of them are the mission profiles of `missionMode.ts`; the third,
 * `test`, is the bench: the same document with `no_fly` set, motors inert.
 * The mode is derived, never stored: `no_fly === true` reads as `test` and
 * anything else as the document's `mission_mode`, so picking `test` leaves
 * the profile underneath untouched and no new field enters the document.
 */
import { readMissionMode, type MissionMode } from './missionMode';
import { getPath } from './paths';

export type FlightMode = MissionMode | 'test';

/** The bench switch of the launch document; only an exact `true` arms it. */
export const NO_FLY_PATH = 'no_fly';

export const FLIGHT_MODES: ReadonlyArray<{ id: FlightMode; label: string }> = [
  { id: 'simple', label: 'Simples' },
  { id: 'advanced', label: 'Avançado' },
  { id: 'test', label: 'Teste' },
];

/** The flight mode of `doc`: `test` when `no_fly` is exactly `true`, the mission profile otherwise. */
export function readFlightMode(doc: unknown): FlightMode {
  return getPath(doc, NO_FLY_PATH) === true ? 'test' : readMissionMode(doc);
}

/** Whether the parameter sheet opens in `mode`; the bench runs the stored document as it is. */
export function parametersEnabled(mode: FlightMode): boolean {
  return mode !== 'test';
}
