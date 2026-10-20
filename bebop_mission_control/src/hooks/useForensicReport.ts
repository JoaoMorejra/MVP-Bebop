import { useCallback, useEffect, useRef, useState } from 'react';
import type { InspectionAssessment, RawEvidence } from '../types/bmg';
import { captureStampOf } from '../types/bmg';
import type { StorageLike } from '../lib/copilotPhrases';
import { drawForensicScript, scriptLines, type ForensicScript } from '../lib/forensics';

/**
 * How long the report waits on the ground for the victim assessment, in ms.
 *
 * The assessment normally settles during the return leg. Past this wait after
 * confirmed ground, the report is drawn with the neutral victim line (reason
 * `timeout`); it is never drawn with a result that did not arrive.
 */
export const ASSESSMENT_GROUND_WAIT_MS = 6000;

export interface ForensicReportState {
  /** The drawn report of the capture on screen, or null while none is drawn. */
  script: ForensicScript | null;
  /** A capture is on screen and its report waits for the victim assessment. */
  analyzing: boolean;
}

/**
 * The forensic report of the latest capture, drawn when its assessment arrives.
 *
 * The draw happens once per capture: on the matching `mission.inspection_ready`
 * when it comes in flight (the usual case, so the reveal on the ground waits
 * for nothing), or, if the aircraft is down and it has not come within
 * {@link ASSESSMENT_GROUND_WAIT_MS}, with no assessment and reason `timeout`.
 * All six lines are handed to `prepare` the moment they are drawn so the copilot
 * synthesizes them before the reveal. Until a draw, `analyzing` is true and
 * nothing about the victim is read. The capture leaving (a new launch, the end
 * of the cycle) drops the report with it.
 */
export function useForensicReport(options: {
  capture: RawEvidence | null;
  assessment: InspectionAssessment | null;
  grounded: boolean;
  prepare: (text: string) => void;
  storage?: StorageLike | null;
  random?: () => number;
}): ForensicReportState {
  const { capture, assessment, grounded, prepare, storage, random } = options;
  const [script, setScript] = useState<ForensicScript | null>(null);
  const drawnFor = useRef<RawEvidence | null>(null);
  const settings = useRef({ prepare, storage, random });
  settings.current = { prepare, storage, random };

  const draw = useCallback((evidence: RawEvidence, result: InspectionAssessment | null, reason: string | null) => {
    if (drawnFor.current === evidence) return;
    drawnFor.current = evidence;
    const { prepare: synthesize, storage: store, random: rng } = settings.current;
    const drawn = drawForensicScript({
      assessment: result,
      captureStamp: captureStampOf(evidence.filename),
      reason,
      ...(store === undefined ? {} : { storage: store }),
      random: rng,
    });
    for (const line of scriptLines(drawn)) synthesize(line);
    setScript(drawn);
  }, []);

  useEffect(() => {
    if (drawnFor.current !== capture) {
      drawnFor.current = null;
      setScript(null);
    }
  }, [capture]);

  useEffect(() => {
    if (capture && assessment) draw(capture, assessment, null);
  }, [capture, assessment, draw]);

  useEffect(() => {
    if (!capture || !grounded || script !== null) return;
    const timer = window.setTimeout(() => draw(capture, null, 'timeout'), ASSESSMENT_GROUND_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [capture, grounded, script, draw]);

  return { script: capture ? script : null, analyzing: Boolean(capture) && script === null };
}
