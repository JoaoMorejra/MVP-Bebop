/** ARSDK flying states in which the airframe is in the air and able to land. */
export const AIRBORNE_FLYING_STATES: ReadonlySet<number> = new Set([1, 2, 3, 6]);

/** Whether the aircraft reports itself in the air. An unknown state is not. */
export function isAirborne(flyingState: number | null | undefined): boolean {
  return typeof flyingState === 'number' && AIRBORNE_FLYING_STATES.has(flyingState);
}
