import { useEffect, useState } from 'react';
import type { RawEvidence } from '../types/bmg';
import { INSPECTION_DELAY_MS, INSPECTION_FALLBACK_MS, inspectionDeadline } from '../lib/evidenceReveal';

export interface InspectionSchedule {
  /**
   * The flight the inspection belongs to, once there is one to inspect: the
   * flight reached Stage 4 or produced a capture. Null before that.
   */
  runToken: string | null;
  /** The inspection of `runToken` has started: the findings take the column. Latched for the flight. */
  started: boolean;
}

/**
 * When the inspection of the current flight starts.
 *
 * {@link INSPECTION_DELAY_MS} after the capture reached the station, or, when
 * no capture ever does (a capture that failed, an abort during Stage 4, an
 * announcement lost on the way), {@link INSPECTION_FALLBACK_MS} after the
 * flight entered Stage 4, whichever comes first. Nothing else gates it: not
 * the touchdown, the link, the mission's exit or the copilot. Once started it
 * stays started until the flight changes (a launch, the end of the cycle).
 *
 * Parameters
 * ----------
 * runStartedAt : the current run's start, epoch ms (`useMissionRuntime`), or
 *   null when no run is open. A new value is a new flight.
 * stage : the stage the mission last reported, 0 before the first.
 * capture : the latest capture of the run, or null.
 */
export function useInspectionSchedule(
  runStartedAt: number | null,
  stage: number,
  capture: RawEvidence | null
): InspectionSchedule {
  const run = runStartedAt === null ? null : String(runStartedAt);
  const [stageFourAt, setStageFourAt] = useState<{ run: string | null; at: number } | null>(null);
  const [startedRun, setStartedRun] = useState<string | null>(null);

  const reachedAt = stageFourAt !== null && stageFourAt.run === run ? stageFourAt.at : null;
  useEffect(() => {
    if (run === null || stage < 4 || reachedAt !== null) return;
    setStageFourAt({ run, at: Date.now() });
  }, [run, stage, reachedAt]);

  const deadline = run === null ? null : inspectionDeadline(capture ? capture.timestamp : null, reachedAt);
  const started = run !== null && startedRun === run;
  useEffect(() => {
    if (run === null || deadline === null || started) return;
    const timer = window.setTimeout(() => setStartedRun(run), Math.max(0, deadline - Date.now()));
    return () => window.clearTimeout(timer);
  }, [run, deadline, started]);

  return { runToken: run !== null && (deadline !== null || started) ? run : null, started };
}

export { INSPECTION_DELAY_MS, INSPECTION_FALLBACK_MS };
