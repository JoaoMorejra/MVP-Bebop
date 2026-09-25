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
  it('covers each topic once, and reads as one analysis rather than a numbered list', () => {
    const report = buildForensicReport({ storage: null, random: seeded(1) });
    expect(report.map((finding) => finding.topic).sort()).toEqual([...TOPICS].sort());
    for (const finding of report) {
      expect(finding.speech).not.toMatch(/\b(Primeiro|Segundo|Terceiro|Quarto)\b/);
      expect(finding.speech.endsWith('.')).toBe(true);
      // The card's own words, after the connector, lower-cased to read on.
      expect(finding.speech.toLowerCase()).toContain(finding.card.toLowerCase());
    }
  });

  it('opens with an opener, closes with a closer, and never links twice the same way', () => {
    const openers = ['Para começar,', 'De início,', 'Logo de cara,', 'Começando pelo essencial,', 'De saída,', 'Abrindo a análise,'];
    const closers = ['Por fim,', 'Para fechar,', 'Para arrematar,', 'E, para concluir,', 'Encerrando,', 'Por último,'];
    const storage = memoryStorage();
    const random = seeded(5);
    for (let flight = 0; flight < 8; flight++) {
      const report = buildForensicReport({ storage, random });
      expect(openers.some((o) => report[0].speech.startsWith(`${o} `))).toBe(true);
      expect(closers.some((c) => report[3].speech.startsWith(`${c} `))).toBe(true);
      const lead = (speech: string) => speech.split(/(?<=[,:]) /)[0];
      expect(lead(report[1].speech)).not.toBe(lead(report[2].speech));
    }
  });

  it('never opens or closes a report the way the previous flight did', () => {
    const storage = memoryStorage();
    const random = seeded(8);
    let previous: string[] | null = null;
    for (let flight = 0; flight < 10; flight++) {
      const report = buildForensicReport({ storage, random });
      const ends = [report[0].speech.split(' ')[0], report[3].speech.split(' ').slice(0, 2).join(' ')];
      if (previous) {
        expect(ends[0] === previous[0] && ends[1] === previous[1]).toBe(false);
      }
      previous = ends;
    }
  });

  it('keeps the wording of an acronym-led finding intact after its connector', () => {
    for (let seed = 0; seed < 40; seed++) {
      for (const finding of buildForensicReport({ storage: null, random: seeded(seed) })) {
        expect(finding.speech).not.toContain('sAMU');
      }
    }
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
      [
        'finding.closer',
        'finding.linker',
        'finding.opener',
        'finding.order',
        'finding.police',
        'finding.samu',
        'finding.vehicle',
        'finding.victim',
      ].sort()
    );
  });

  it('still draws a report with no storage at all', () => {
    expect(buildForensicReport({ storage: null }).length).toBe(4);
  });
});
