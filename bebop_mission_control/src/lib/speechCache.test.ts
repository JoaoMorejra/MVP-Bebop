import { describe, expect, it } from 'vitest';
import { PHRASE_POOLS, allMissionPhrases } from './copilotPhrases';
import { allFindingSentences, buildForensicReport } from './forensics';
import type { InspectionAssessment } from '../types/bmg';

const DETECTED: InspectionAssessment = {
  schema: 'bmg.inspection.v1',
  capture_stamp: '20261020_101500',
  status: 'complete',
  reason: null,
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
};

describe('sentences known before the flight (3.9)', () => {
  it('renders every pool with the mission parameters, leaving no placeholder', () => {
    const phrases = allMissionPhrases({ target_altitude_m: 2.3, cruise_mps: 0.35 });
    expect(phrases).toContain('Decolagem autorizada. Subindo para 2,3 metros.');
    expect(phrases).toContain('Varredura iniciada a 0,35 metro por segundo.');
    expect(phrases).toContain('Pouso comandado. Confirmação de toque indisponível. Verifique visualmente.');
    for (const line of phrases) expect(line).not.toMatch(/\{[a-z_]+\}/);
    expect(new Set(phrases).size).toBe(phrases.length);
  });

  it('includes the target call, which no longer depends on a flight estimate (D2)', () => {
    const phrases = allMissionPhrases({ target_altitude_m: 2.3 });
    expect(phrases.some((line) => /Ocorrência identificada|Acidente avistado|Alvo identificado/.test(line))).toBe(true);
    for (const variant of PHRASE_POOLS['mission.target_found']) expect(phrases).toContain(variant);
  });

  it('enumerates every sentence the forensic report can say (D1)', () => {
    const sentences = allFindingSentences();
    // 13 fixed wordings and 15 victim wordings (three branches), each after any
    // of the 19 connectors.
    expect(sentences.length).toBe(28 * 19);
    for (const assessment of [null, DETECTED, { ...DETECTED, victim_count: 0, victim_boxes: [] }]) {
      const report = buildForensicReport({ assessment, storage: null, random: () => 0.3 });
      for (const finding of report) expect(sentences).toContain(finding.speech);
    }
  });
});
