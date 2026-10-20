import { useEffect, useState } from 'react';
import type { InspectionAssessment, RawEvidence } from '../types/bmg';
import { captureStampOf, parseInspectionAssessment } from '../types/bmg';
import { useBridge } from './useBridge';

/** Milestone carrying the victim assessment (`perception/inspection_worker.py`). */
export const INSPECTION_MILESTONE = 'mission.inspection_ready';

/**
 * The victim assessment of the capture on screen, or null until it arrives.
 *
 * The mission raises `mission.inspection_ready` from a worker thread, after
 * `mission.capture_done` and normally during the return leg; the milestone is
 * forwarded like `mission.flat_trim` and never narrated. The latest assessment
 * is kept by its `capture_stamp` and handed out only for the capture whose
 * filename carries the same stamp, so an assessment of an earlier flight, or
 * one that arrives before its capture is displayed, is never matched to the
 * wrong photograph. A malformed payload or an unknown schema is ignored.
 */
export function useInspectionAssessment(capture: RawEvidence | null): InspectionAssessment | null {
  const bridge = useBridge();
  const [latest, setLatest] = useState<InspectionAssessment | null>(null);

  useEffect(() => {
    if (!bridge) return;
    return bridge.onMilestone((event) => {
      if (event.kind !== 'milestone') return;
      if (event.key === 'mission.start') {
        setLatest(null);
        return;
      }
      if (event.key !== INSPECTION_MILESTONE) return;
      const assessment = parseInspectionAssessment(event.payload);
      if (assessment) setLatest(assessment);
    });
  }, [bridge]);

  const stamp = capture ? captureStampOf(capture.filename) : null;
  return stamp !== null && latest?.capture_stamp === stamp ? latest : null;
}
