import { describe, expect, it } from 'vitest';
import type { StorageLike } from './copilotPhrases';
import { HISTORY_STORAGE_KEY } from './copilotPhrases';
import { buildForensicReport, type FindingTopic } from './forensics';

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (key) => data.get(key) ?? null, setItem: (key, value) => void data.set(key, value) };
}

function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

const TOPICS: FindingTopic[] = ['police', 'samu', 'victim', 'vehicle'];

describe('buildForensicReport', () => {
  it('covers each topic once, ordinal-prefixed for the voice', () => {
    const report = buildForensicReport({ storage: null, random: seeded(1) });
    expect(report.map((finding) => finding.topic).sort()).toEqual([...TOPICS].sort());
    const ordinals = ['Primeiro', 'Segundo', 'Terceiro', 'Quarto'];
    report.forEach((finding, index) => {
      expect(finding.speech.startsWith(`${ordinals[index]}: `)).toBe(true);
      expect(finding.speech.endsWith('.')).toBe(true);
    });
  });

  it('never repeats the topic order of any of the last five flights', () => {
    const storage = memoryStorage();
    const random = seeded(9);
    const orders: string[] = [];
    for (let flight = 0; flight < 12; flight++) {
      orders.push(buildForensicReport({ storage, random }).map((f) => f.topic).join(','));
    }
    for (let start = 0; start + 6 <= orders.length; start++) {
      expect(new Set(orders.slice(start, start + 6)).size).toBe(6);
    }
  });

  it('never reads a topic with the wording it had on the previous flight', () => {
    const storage = memoryStorage();
    const random = seeded(4);
    let previous: Record<string, string> | null = null;
    for (let flight = 0; flight < 12; flight++) {
      const cards = Object.fromEntries(
        buildForensicReport({ storage, random }).map((f) => [f.topic, f.card])
      );
      if (previous) for (const topic of TOPICS) expect(cards[topic]).not.toBe(previous[topic]);
      previous = cards;
    }
  });

  it('keeps its memory in the copilot history document', () => {
    const storage = memoryStorage();
    buildForensicReport({ storage, random: seeded(2) });
    const stored = JSON.parse(storage.data.get(HISTORY_STORAGE_KEY)!);
    expect(Object.keys(stored).sort()).toEqual(
      ['finding.order', 'finding.police', 'finding.samu', 'finding.vehicle', 'finding.victim'].sort()
    );
  });

  it('still draws a report with no storage at all', () => {
    expect(buildForensicReport({ storage: null }).length).toBe(4);
  });
});
