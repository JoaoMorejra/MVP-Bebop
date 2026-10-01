import type { FailsafeAction } from './batteryFailsafe';

/** What the station says on a critical charge, by the path actually taken. */
export const BATTERY_CALLS = {
  alreadyReturning: 'Bateria crítica. Retorno à base já em curso.',
  returning: 'Bateria crítica. Retornando à base para pouso.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

export interface BatteryReturnHooks {
  /** Speak through the narration queue, ahead of narration (`NarrationQueue.preempt`). */
  say: (label: string, text: string) => void;
  /** Ask the mission to continue at the return stage; resolves with its acknowledgement. */
  gotoStage: () => Promise<{ success: boolean }>;
  /** Land in place. */
  land: () => Promise<void>;
}

/**
 * The critical-battery response, spoken as it happens.
 *
 * "Retornando à base" used to be said before the jump was even asked for, and
 * stayed said when the mission refused it and the aircraft landed where it
 * stood. The return call now follows the acknowledgement; a refused or failed
 * jump says it is landing, then lands.
 *
 * @returns The path taken: `rtl`, `land`, or `none` when a return was already
 *   under way.
 */
export async function runCriticalBatteryReturn(
  action: FailsafeAction,
  hooks: BatteryReturnHooks
): Promise<FailsafeAction> {
  if (action === 'none') {
    hooks.say('battery.critical', BATTERY_CALLS.alreadyReturning);
    return 'none';
  }
  if (action === 'rtl') {
    const jump = await hooks.gotoStage().catch(() => ({ success: false }));
    if (jump.success) {
      hooks.say('battery.critical', BATTERY_CALLS.returning);
      return 'rtl';
    }
  }
  hooks.say('battery.critical', BATTERY_CALLS.landing);
  await hooks.land();
  return 'land';
}
