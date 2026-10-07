import { useCallback, useEffect, useState } from 'react';
import type { LandProgressPhase } from '../types/bmg';
import { useBridge } from './useBridge';

export function useLandProgress(): {
  phase: LandProgressPhase | null;
  reset: () => void;
} {
  const bridge = useBridge();
  const [phase, setPhase] = useState<LandProgressPhase | null>(null);

  useEffect(() => {
    if (!bridge) return;

    const unsubs: Array<() => void> = [];

    if (bridge.onLandProgress) {
      unsubs.push(
        bridge.onLandProgress((event) => {
          setPhase(event.phase);
        })
      );
    }

    if (bridge.onMilestone) {
      unsubs.push(
        bridge.onMilestone((event) => {
          if (event.key === 'mission.start') {
            setPhase(null);
          }
        })
      );
    }

    if (bridge.onMissionReset) {
      unsubs.push(
        bridge.onMissionReset(() => {
          setPhase(null);
        })
      );
    }

    return () => {
      for (const unsub of unsubs) unsub();
    };
  }, [bridge]);

  const reset = useCallback(() => setPhase(null), []);

  return { phase, reset };
}
