import type { FailsafeAction } from './batteryFailsafe';
import type { MissionMode } from './missionMode';

const ADVANCED_CALLS = {
  alreadyReturning: 'Bateria crítica. Retorno à base já em curso.',
  returning: 'Bateria crítica. Retornando à base para pouso.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

/** Either profile without the return (`rtl.enabled` false): stage 5 lands where it is. */
const IN_PLACE_CALLS = {
  alreadyReturning: 'Bateria crítica. Pouso já em curso.',
  returning: 'Bateria crítica: pousando no local.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

const SIMPLE_CALLS = {
  alreadyReturning: 'Bateria crítica. Pouso na base já em curso.',
  returning: 'Bateria crítica. Pouso na base iniciado.',
  landing: 'Bateria crítica. Pouso de emergência iniciado.',
} as const;

export type BatteryCalls = typeof ADVANCED_CALLS | typeof IN_PLACE_CALLS | typeof SIMPLE_CALLS;

/**
 * What the station says on a critical charge, by the plan being flown and the
 * path actually taken. Without the return no call names the base.
 */
export function batteryCalls(mode: MissionMode, rtlEnabled = true): BatteryCalls {
  if (!rtlEnabled) return IN_PLACE_CALLS;
  return mode === 'simple' ? SIMPLE_CALLS : ADVANCED_CALLS;
}

export const BATTERY_CALLS = ADVANCED_CALLS;

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
  hooks: BatteryReturnHooks,
  mode: MissionMode = 'advanced',
  rtlEnabled = true
): Promise<FailsafeAction> {
  const calls = batteryCalls(mode, rtlEnabled);
  if (action === 'none') {
    hooks.say('battery.critical', calls.alreadyReturning);
    return 'none';
  }
  if (action === 'rtl') {
    const jump = await hooks.gotoStage().catch(() => ({ success: false }));
    if (jump.success) {
      hooks.say('battery.critical', calls.returning);
      return 'rtl';
    }
  }
  hooks.say('battery.critical', calls.landing);
  await hooks.land();
  return 'land';
}
