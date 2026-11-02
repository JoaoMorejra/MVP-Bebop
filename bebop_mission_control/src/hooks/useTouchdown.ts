import { useEffect, useState } from 'react';
import { useBridge } from './useBridge';

/**
 * Whether the mission has declared touchdown in this flight.
 *
 * Set by the first `mission.touchdown*` milestone (confirmed, in place,
 * unconfirmed or on a battery return: the aircraft is down in every one), the
 * moment the mission itself commits to it, about a second before its process
 * exits. Cleared by `mission.start` and by the host resetting the mission.
 */
export function useTouchdown(): boolean {
  const bridge = useBridge();
  const [down, setDown] = useState(false);

  useEffect(() => {
    if (!bridge) return;
    const unsubs: Array<() => void> = [];
    if (bridge.onMilestone) {
      unsubs.push(
        bridge.onMilestone((event) => {
          if (event.kind !== 'milestone') return;
          if (event.key === 'mission.start') setDown(false);
          else if (event.key.startsWith('mission.touchdown')) setDown(true);
        })
      );
    }
    if (bridge.onMissionReset) unsubs.push(bridge.onMissionReset(() => setDown(false)));
    return () => {
      for (const unsub of unsubs) unsub();
    };
  }, [bridge]);

  return down;
}
