// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { isMilestoneKey } from '../lib/copilotPhrases';
import type { InspectionAssessment, RawEvidence } from '../types/bmg';
import { captureStampOf, parseInspectionAssessment, victimStatusOf } from '../types/bmg';
import { INSPECTION_MILESTONE, useInspectionAssessment } from './useInspectionAssessment';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

type Event = { kind: 'milestone' | 'alert'; key: string; payload: Record<string, unknown> };
let emit: (event: Event) => void = () => undefined;
let container: HTMLDivElement;
let root: Root;
let seen: InspectionAssessment | null = null;

const Probe: React.FC<{ capture: RawEvidence | null }> = ({ capture }) => {
  seen = useInspectionAssessment(capture);
  return null;
};

const capture = (stamp: string): RawEvidence => ({
  filename: `accident_raw_${stamp}.png`,
  url: `file:///out/accident_raw_${stamp}.png`,
  timestamp: 1,
});

function payload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: 'bmg.inspection.v1',
    capture_stamp: '20261020_101500',
    status: 'complete',
    reason: null,
    victim_status: 'detected',
    victim_detected: true,
    victim_count: 1,
    victim_confidence: 0.62,
    victim_boxes: [[100, 100, 160, 220]],
    frame: [856, 480],
    inference_ms: 212.3,
    model: 'yolov8n.pt',
    imgsz: 640,
    conf: 0.35,
    completed_at_utc: '2026-10-20T13:15:01+00:00',
    ...overrides,
  };
}

const send = (key: string, body: Record<string, unknown>) => act(() => emit({ kind: 'milestone', key, payload: body }));
const render = (value: RawEvidence | null) => act(() => root.render(<Probe capture={value} />));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (window as unknown as { bmgAPI: unknown }).bmgAPI = {
    onMilestone: (listener: (event: Event) => void) => {
      emit = listener;
      return () => undefined;
    },
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as { bmgAPI?: unknown }).bmgAPI;
});

describe('useInspectionAssessment', () => {
  it('matches the assessment to the capture by the stamp in its filename', () => {
    render(capture('20261020_101500'));
    expect(seen).toBeNull();
    send(INSPECTION_MILESTONE, payload());
    expect(seen?.capture_stamp).toBe('20261020_101500');
    expect(victimStatusOf(seen)).toBe('detected');
  });

  it('holds an assessment that arrives before its capture is displayed', () => {
    render(null);
    send(INSPECTION_MILESTONE, payload());
    expect(seen).toBeNull();
    render(capture('20261020_101500'));
    expect(seen?.capture_stamp).toBe('20261020_101500');
  });

  it('never hands out the assessment of another capture', () => {
    render(capture('20261020_101501'));
    send(INSPECTION_MILESTONE, payload());
    expect(seen).toBeNull();
  });

  it('ignores an unknown schema and a malformed payload', () => {
    render(capture('20261020_101500'));
    send(INSPECTION_MILESTONE, payload({ schema: 'bmg.inspection.v2' }));
    expect(seen).toBeNull();
    send(INSPECTION_MILESTONE, payload({ victim_count: -1 }));
    expect(seen).toBeNull();
  });

  it('forgets the previous flight on a new launch', () => {
    render(capture('20261020_101500'));
    send(INSPECTION_MILESTONE, payload());
    send('mission.start', {});
    expect(seen).toBeNull();
  });

  it('is forwarded but never narrated, like mission.flat_trim', () => {
    expect(isMilestoneKey(INSPECTION_MILESTONE)).toBe(false);
    expect(isMilestoneKey('mission.flat_trim')).toBe(false);
  });
});

describe('parseInspectionAssessment', () => {
  it('rejects an unknown schema', () => {
    expect(parseInspectionAssessment(payload({ schema: 'bmg.inspection.v0' }))).toBeNull();
    expect(parseInspectionAssessment(null)).toBeNull();
    expect(parseInspectionAssessment([])).toBeNull();
  });

  it('rejects an unavailable assessment without reason and malformed fields', () => {
    expect(parseInspectionAssessment(payload({ status: 'unavailable', reason: null }))).toBeNull();
    expect(parseInspectionAssessment(payload({ victim_boxes: [[1, 2, 3]] }))).toBeNull();
    expect(parseInspectionAssessment(payload({ conf: 2 }))).toBeNull();
    expect(parseInspectionAssessment(payload({ status: 'maybe' }))).toBeNull();
  });

  it('derives unknown from an unavailable status whatever the victim flag says', () => {
    const parsed = parseInspectionAssessment(
      payload({ status: 'unavailable', reason: 'detector_error', victim_detected: true, victim_count: 1 })
    );
    expect(parsed).not.toBeNull();
    expect(victimStatusOf(parsed)).toBe('unknown');
    expect(victimStatusOf(null)).toBe('unknown');
    expect(victimStatusOf(parseInspectionAssessment(payload({ victim_count: 0, victim_boxes: [] })))).toBe(
      'not_detected'
    );
  });

  it('reads the stamp of every evidence filename the mission writes', () => {
    expect(captureStampOf('accident_raw_20261020_101500.png')).toBe('20261020_101500');
    expect(captureStampOf('accident_inspected_20261020_101500.jpg')).toBe('20261020_101500');
    expect(captureStampOf('photo.png')).toBeNull();
  });
});
