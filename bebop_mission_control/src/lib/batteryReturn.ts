import type { FailsafeAction } from './batteryFailsafe';

/**
 * The station's only battery sentence of its own, said when it lands the
 * aircraft where it is: no mission running, a jump the host could not carry,
 * or a mission silent through both acknowledgement windows. The return itself
 * is narrated from the mission's milestones (`mission.battery_return` and the
 * RTL milestones flagged `battery_return`), so the crossing is said once.
 */
export const BATTERY_LANDING_CALL = 'Bateria crítica. Pouso de emergência iniciado.';

export interface BatteryReturnHooks {
  /** Speak through the narration queue, ahead of narration (`NarrationQueue.preempt`). */
  say: (label: string, text: string) => void;
  /** Ask the mission to continue at the return stage; resolves with the host's delivery of the request. */
  gotoStage: () => Promise<{ success: boolean }>;
  /** Land in place. */
  land: () => Promise<void>;
  /**
   * Wait for a mission that may still answer after a jump the host could not
   * deliver: it starts the return on its own at the same charge. Resolves true
   * when the landing is no longer needed (the return was acknowledged, or the
   * aircraft is down). Absent, an undelivered jump lands at once.
   */
  graceWindow?: () => Promise<boolean>;
}

/**
 * The critical-battery response of the station.
 *
 * Parameters
 * ----------
 * action
 *   The decision of `failsafeAction`.
 * hooks
 *   The narration queue, the stage jump and the landing.
 *
 * Returns
 * -------
 * The path taken: `rtl` when the request reached the mission (silent; the
 * caller watches for the acknowledgement), `none` when a return was already
 * under way or the grace window made the landing unnecessary (silent), `land`
 * after the emergency call and the landing.
 */
export async function runCriticalBatteryReturn(
  action: FailsafeAction,
  hooks: BatteryReturnHooks
): Promise<FailsafeAction> {
  if (action === 'none') return 'none';
  if (action === 'rtl') {
    const jump = await hooks.gotoStage().catch(() => ({ success: false }));
    if (jump.success) return 'rtl';
    if (hooks.graceWindow && (await hooks.graceWindow())) return 'none';
  }
  await landOnCriticalBattery(hooks);
  return 'land';
}

/** The emergency landing: {@link BATTERY_LANDING_CALL}, then the landing. */
export async function landOnCriticalBattery(hooks: Pick<BatteryReturnHooks, 'say' | 'land'>): Promise<void> {
  hooks.say('battery.critical', BATTERY_LANDING_CALL);
  await hooks.land();
}
