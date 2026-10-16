import { useEffect, useRef } from 'react';
import type { MissionMode } from '../lib/missionMode';
import { parseMissionMode } from '../lib/missionMode';
import { useBridge } from './useBridge';

export interface ModeEchoOptions {
  /** The profile of the launch in progress; null while no launch has been requested. */
  requested: MissionMode | null;
  /** Called once per mission when the profile the mission reports differs from the one requested. */
  onMismatch: (reported: string, requested: MissionMode) => void;
}

/**
 * Confirm that the mission flies the profile the operator selected.
 *
 * The mission raises `mission.mode` before `mission.parameters` and before
 * Stage 1, so a divergence is always found during the countdown, with nothing
 * in the air. Only a divergence acts: a mission that never reports the milestone
 * (an older backend, a truncated stream) is not an error here.
 */
export function useModeEcho({ requested, onMismatch }: ModeEchoOptions): void {
  const bridge = useBridge();
  const requestedRef = useRef(requested);
  requestedRef.current = requested;
  const handlerRef = useRef(onMismatch);
  handlerRef.current = onMismatch;

  const reportedFor = useRef<MissionMode | null>(null);

  useEffect(() => {
    reportedFor.current = null;
  }, [requested]);

  useEffect(() => {
    if (!bridge?.onMilestone) return;
    return bridge.onMilestone((event) => {
      if (event.kind === 'alert' || event.key !== 'mission.mode') return;
      const wanted = requestedRef.current;
      if (wanted === null || reportedFor.current === wanted) return;
      const raw = (event.payload ?? {}).mode;
      const reported = parseMissionMode(raw);
      if (reported === wanted) return;
      reportedFor.current = wanted;
      handlerRef.current(typeof raw === 'string' ? raw : String(raw), wanted);
    });
  }, [bridge]);
}
