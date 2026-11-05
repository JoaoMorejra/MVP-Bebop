import { useEffect, useRef, useState } from 'react';
import type { RawEvidence } from '../types/bmg';
import { captureStampOf } from '../types/bmg';
import type { StorageLike } from '../lib/copilotPhrases';
import { REASON_VICTIM_DETECTION_DISABLED, drawForensicScript, scriptLines, type ForensicScript } from '../lib/forensics';

export interface ForensicReportState {
  /** The drawn report of the current flight, or null while none is drawn. */
  script: ForensicScript | null;
}

/**
 * The forensic report of the current flight: four findings, every flight.
 *
 * Drawn once per flight as soon as there is something to inspect
 * (`useInspectionSchedule` hands out `runToken`), so its six lines are with
 * the copilot before they are needed. The victim finding is the operator's
 * `vision.victim_detection_enabled`, not a detector verdict: on, a victim is
 * reported, conscious and without visible injuries, and the report opens with
 * the victim; off, no visible victim is reported and the report opens with the
 * vehicle. A new flight drops the report with the old one.
 *
 * Parameters
 * ----------
 * runToken : the flight the report is for, or null while there is none.
 * victimDetection : `vision.victim_detection_enabled` of the flight.
 * capture : the flight's capture, for the stamp the script carries; may be null.
 */
export function useForensicReport(options: {
  runToken: string | null;
  victimDetection: boolean;
  capture: RawEvidence | null;
  prepare: (text: string) => void;
  storage?: StorageLike | null;
  random?: () => number;
}): ForensicReportState {
  const { runToken, victimDetection, capture, prepare, storage, random } = options;
  const [drawn, setDrawn] = useState<{ run: string; script: ForensicScript } | null>(null);
  const drawnRun = useRef<string | null>(null);
  const settings = useRef({ prepare, storage, random, victimDetection, capture });
  settings.current = { prepare, storage, random, victimDetection, capture };

  useEffect(() => {
    if (runToken === null || drawnRun.current === runToken) return;
    drawnRun.current = runToken;
    const { prepare: synthesize, storage: store, random: rng, victimDetection: victim, capture: evidence } =
      settings.current;
    const script = drawForensicScript({
      assessment: null,
      victimStatus: victim ? 'detected' : 'not_detected',
      captureStamp: evidence ? captureStampOf(evidence.filename) : null,
      reason: victim ? null : REASON_VICTIM_DETECTION_DISABLED,
      ...(store === undefined ? {} : { storage: store }),
      random: rng,
    });
    for (const line of scriptLines(script)) synthesize(line);
    setDrawn({ run: runToken, script });
  }, [runToken]);

  return { script: runToken !== null && drawn?.run === runToken ? drawn.script : null };
}
