import { describe, expect, it } from 'vitest';
import { HISTORY_WINDOW, nextPhrase, PHRASE_POOLS, POOL_KEYS, type PoolKey, type StorageLike } from './copilotPhrases';
import { buildForensicReport } from './forensics';
import type { InspectionAssessment } from '../types/bmg';

const memory = (): StorageLike => {
  const store = new Map<string, string>();
  return { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => void store.set(k, v) };
};

const VALUES = { altitude: '2 metros', speed: '0,15 metro por segundo', threshold: '20 por cento' };

/** One-line pools are dictated sentences with no alternative to draw. */
const DICTATED_SINGLE_LINE: readonly PoolKey[] = ['mission.touchdown_unconfirmed'];

const assessment = (victims: number): InspectionAssessment =>
  ({
    schema: 'bmg.inspection.v1',
    capture_stamp: '20261026_101500',
    status: 'complete',
    reason: null,
    victim_detected: victims > 0,
    victim_count: victims,
    victim_confidence: 0.6,
    victim_boxes: [],
    frame: [856, 480],
    inference_ms: 200,
    model: 'yolov8n.pt',
    imgsz: 640,
    conf: 0.35,
    completed_at_utc: '2026-10-26T13:15:01+00:00',
  }) as unknown as InspectionAssessment;

describe('the copilot does not repeat itself across flights', () => {
  it('has no sentence in two pools, so alternative pools of one moment cannot echo each other', () => {
    const owners = new Map<string, PoolKey[]>();
    for (const key of POOL_KEYS) {
      for (const sentence of PHRASE_POOLS[key]) owners.set(sentence, [...(owners.get(sentence) ?? []), key]);
    }
    const shared = [...owners.entries()].filter(([, keys]) => keys.length > 1).map(([s, keys]) => `${s} :: ${keys.join(' | ')}`);
    expect(shared).toEqual([]);
  });

  it('keeps every pool but the dictated ones above one variant', () => {
    const single = POOL_KEYS.filter((key) => PHRASE_POOLS[key].length < 2);
    expect([...single]).toEqual([...DICTATED_SINGLE_LINE]);
  });

  it('never draws a pool variant inside the memory window over 300 consecutive flights', () => {
    for (const key of POOL_KEYS) {
      const variants = PHRASE_POOLS[key].length;
      if (variants < 2) continue;
      const window = Math.min(HISTORY_WINDOW, variants - 1);
      const storage = memory();
      const draws: string[] = [];
      for (let flight = 0; flight < 300; flight++) {
        const text = nextPhrase(key, VALUES, { storage, random: Math.random });
        expect(draws.slice(-window), `${key} flight ${flight}`).not.toContain(text);
        draws.push(text);
      }
    }
  });

  it('keeps that memory across an application restart', () => {
    const storage = memory();
    const first = nextPhrase('mission.takeoff', VALUES, { storage, random: () => 0 });
    const afterRestart = nextPhrase('mission.takeoff', VALUES, { storage, random: () => 0 });
    expect(afterRestart).not.toBe(first);
  });

  it('never repeats a report card, a spoken line, or the dispatch order of the previous flight', () => {
    const storage = memory();
    let previous: { cards: Record<string, string>; speech: string[]; dispatch: string } | null = null;
    for (let flight = 0; flight < 400; flight++) {
      const victims = flight % 3 === 0 ? assessment(1) : flight % 3 === 1 ? assessment(0) : null;
      const report = buildForensicReport({ assessment: victims, storage, random: Math.random });
      const cards: Record<string, string> = {};
      report.forEach((finding) => (cards[finding.topic] = finding.card));
      const speech = report.map((finding) => finding.speech);
      const dispatch = report.slice(2).map((finding) => finding.topic).join(',');
      if (previous) {
        for (const topic of ['police', 'samu', 'vehicle']) {
          expect(cards[topic], `${topic} card, flight ${flight}`).not.toBe(previous.cards[topic]);
        }
        speech.forEach((line, index) => expect(line, `line ${index}, flight ${flight}`).not.toBe(previous!.speech[index]));
        expect(dispatch, `dispatch order, flight ${flight}`).not.toBe(previous.dispatch);
      }
      previous = { cards, speech, dispatch };
    }
  });
});
