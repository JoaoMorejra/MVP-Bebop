/** ARSDK flying states in which the airframe is in the air and able to land. */
export const AIRBORNE_FLYING_STATES: ReadonlySet<number> = new Set([1, 2, 3, 6]);

/** Whether the aircraft reports itself in the air. An unknown state is not. */
export function isAirborne(flyingState: number | null | undefined): boolean {
  return typeof flyingState === 'number' && AIRBORNE_FLYING_STATES.has(flyingState);
}

/**
 * ARSDK flying states in which the rotors are turning: taking off, hovering,
 * flying, landing, motors ramping up and emergency landing. Not landed, not
 * `usertakeoff` (waiting for a hand launch) and not `emergency` (motors cut).
 */
export const ROTORS_TURNING_STATES: ReadonlySet<number> = new Set([1, 2, 3, 4, 7, 8]);

/** Whether the rotors are turning. An unknown or stale state is not. */
export function rotorsTurning(flyingState: number | null | undefined): boolean {
  return typeof flyingState === 'number' && ROTORS_TURNING_STATES.has(flyingState);
}
