import { useEffect, useState } from 'react';
import { useBridge } from './useBridge';

export interface MissionCountdown {
  /** Whole seconds left as last reported (`mission.countdown`), or null before the countdown began. */
  remaining: number | null;
  /** The mission made its clearance call (`mission.countdown_3`). */
  clearance: boolean;
}

const IDLE: MissionCountdown = { remaining: null, clearance: false };

/**
 * The launch countdown as the mission process reports it.
 *
 * `TakeoffStep._countdown` raises `mission.countdown` every whole second and
 * `mission.countdown_3` at its clearance call; a bench routine the station
 * counts itself raises the same ticks (`scheduleCountdownTicks`). A new
 * `mission.start` clears the previous launch.
 */
export function useMissionCountdown(): MissionCountdown {
  const bridge = useBridge();
  const [state, setState] = useState<MissionCountdown>(IDLE);

  useEffect(() => {
    if (!bridge) return;
    return bridge.onMilestone((event) => {
      if (event.kind !== 'milestone') return;
      if (event.key === 'mission.start') {
        setState(IDLE);
      } else if (event.key === 'mission.countdown') {
        const value = Number(event.payload?.remaining_sec);
        if (typeof event.payload?.remaining_sec !== 'number' || !Number.isFinite(value)) return;
        setState((previous) => ({ ...previous, remaining: Math.max(0, Math.round(value)) }));
      } else if (event.key === 'mission.countdown_3') {
        setState((previous) => ({ ...previous, clearance: true }));
      }
    });
  }, [bridge]);

  return state;
}
