import { describe, expect, it } from 'vitest';
import type { InspectionAssessment, VictimStatus } from '../types/bmg';
import type { StorageLike } from './copilotPhrases';
import { HISTORY_STORAGE_KEY, PHRASE_POOLS, loadHistory } from './copilotPhrases';
import {
  FIXED_WORDINGS,
  VICTIM_WORDINGS,
  allFindingSentences,
  buildForensicReport,
  checkTopicOrder,
  drawForensicScript,
  scriptLines,
  type FindingTopic,
} from './forensics';

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

function assessment(status: VictimStatus): InspectionAssessment | null {
  if (status === 'unknown') return null;
  const detected = status === 'detected';
  return {
    schema: 'bmg.inspection.v1',
    capture_stamp: '20261020_101500',
    status: 'complete',
    reason: null,
    victim_detected: detected,
    victim_count: detected ? 1 : 0,
    victim_confidence: detected ? 0.62 : null,
    victim_boxes: detected ? [[100, 100, 160, 220]] : [],
    frame: [856, 480],
    inference_ms: 212.3,
    model: 'yolov8n.pt',
    imgsz: 640,
    conf: 0.35,
    completed_at_utc: '2026-10-20T13:15:01+00:00',
  };
}

const TOPICS: FindingTopic[] = ['police', 'samu', 'victim', 'vehicle'];
const STATUSES: VictimStatus[] = ['detected', 'not_detected', 'unknown'];

/** Stems the neutral line must never contain, compared without case or accents. */
const FORBIDDEN_IN_UNKNOWN = [
  'conscien',
  'ferim',
  'lesã',
  'nenhum',
  'sem vítimas',
  'ausência',
  'identificad',
  'visível',
  'detect',
];

const fold = (text: string) =>
  text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();

const cardsOf = (report: { topic: FindingTopic; card: string }[]) =>
  Object.fromEntries(report.map((finding) => [finding.topic, finding.card])) as Record<FindingTopic, string>;

describe('buildForensicReport', () => {
  it('covers each topic once, and reads as one analysis rather than a numbered list', () => {
    const report = buildForensicReport({ storage: null, random: seeded(1) });
    expect(report.map((finding) => finding.topic).sort()).toEqual([...TOPICS].sort());
    for (const finding of report) {
      expect(finding.speech).not.toMatch(/\b(Primeiro|Segundo|Terceiro|Quarto)\b/);
      expect(finding.speech.endsWith('.')).toBe(true);
      expect(finding.speech.toLowerCase()).toContain(finding.card.toLowerCase());
    }
  });

  it.each(STATUSES)('never opens with a dispatch topic (%s, 1000 draws)', (status) => {
    for (let seed = 0; seed < 1000; seed++) {
      const topics = buildForensicReport({ assessment: assessment(status), storage: null, random: seeded(seed) }).map(
        (finding) => finding.topic
      );
      expect(['vehicle', 'victim']).toContain(topics[0]);
      expect(new Set(topics.slice(2))).toEqual(new Set(['samu', 'police']));
    }
  });

  it('alternates the opening topic and the dispatch order across flights', () => {
    const storage = memoryStorage();
    const random = seeded(3);
    const leads: string[] = [];
    const dispatch: string[] = [];
    for (let flight = 0; flight < 8; flight++) {
      const report = buildForensicReport({ assessment: assessment('detected'), storage, random });
      leads.push(report[0].topic);
      dispatch.push(report.slice(2).map((finding) => finding.topic).join(','));
    }
    for (let flight = 1; flight < 8; flight++) {
      expect(leads[flight]).not.toBe(leads[flight - 1]);
      expect(dispatch[flight]).not.toBe(dispatch[flight - 1]);
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

  it('keeps the wording of an acronym-led finding intact after its connector', () => {
    for (let seed = 0; seed < 40; seed++) {
      for (const finding of buildForensicReport({ storage: null, random: seeded(seed) })) {
        expect(finding.speech).not.toContain('sAMU');
      }
    }
  });

  it.each(STATUSES)('never reads a topic with the wording it had on the previous flight (%s)', (status) => {
    const storage = memoryStorage();
    const random = seeded(4);
    let previous: Record<string, string> | null = null;
    for (let flight = 0; flight < 12; flight++) {
      const cards = cardsOf(buildForensicReport({ assessment: assessment(status), storage, random }));
      if (previous) for (const topic of TOPICS) expect(cards[topic]).not.toBe(previous[topic]);
      previous = cards;
    }
  });

  it('draws police, SAMU and the vehicle from the same fixed pools in the three branches', () => {
    for (const status of STATUSES) {
      for (let seed = 0; seed < 50; seed++) {
        const cards = cardsOf(buildForensicReport({ assessment: assessment(status), storage: null, random: seeded(seed) }));
        expect(FIXED_WORDINGS.police).toContain(cards.police);
        expect(FIXED_WORDINGS.samu).toContain(cards.samu);
        expect(FIXED_WORDINGS.vehicle).toContain(cards.vehicle);
      }
    }
    expect([FIXED_WORDINGS.police.length, FIXED_WORDINGS.samu.length, FIXED_WORDINGS.vehicle.length]).toEqual([4, 4, 4]);
  });

  it.each(STATUSES)('reads the victim line from the %s pool', (status) => {
    for (let seed = 0; seed < 200; seed++) {
      const cards = cardsOf(buildForensicReport({ assessment: assessment(status), storage: null, random: seeded(seed) }));
      expect(VICTIM_WORDINGS[status]).toContain(cards.victim);
    }
  });

  it('reads the neutral line for an unavailable assessment, whatever its victim flag says', () => {
    const unavailable: InspectionAssessment = {
      ...(assessment('detected') as InspectionAssessment),
      status: 'unavailable',
      reason: 'detector_error',
    };
    for (let seed = 0; seed < 50; seed++) {
      const cards = cardsOf(buildForensicReport({ assessment: unavailable, storage: null, random: seeded(seed) }));
      expect(VICTIM_WORDINGS.unknown).toContain(cards.victim);
    }
  });

  it('never states anything about the victim in the neutral pool', () => {
    for (const line of VICTIM_WORDINGS.unknown) {
      for (const stem of FORBIDDEN_IN_UNKNOWN) expect(fold(line)).not.toContain(fold(stem));
    }
  });

  it('keeps its memory in the copilot history document, under the new keys', () => {
    const storage = memoryStorage();
    buildForensicReport({ assessment: assessment('not_detected'), storage, random: seeded(2) });
    const stored = JSON.parse(storage.data.get(HISTORY_STORAGE_KEY)!);
    expect(Object.keys(stored).sort()).toEqual(
      [
        'finding.closer',
        'finding.dispatch_order',
        'finding.lead',
        'finding.linker',
        'finding.opener',
        'finding.police',
        'finding.samu',
        'finding.vehicle',
        'finding.victim.not_detected',
      ].sort()
    );
  });

  it('drops the retired keys of an earlier history and keeps the fixed ones', () => {
    const storage = memoryStorage();
    storage.setItem(
      HISTORY_STORAGE_KEY,
      JSON.stringify({ 'finding.victim': [3], 'finding.order': [17], 'finding.police': [2], 'mission.takeoff': [1] })
    );
    const history = loadHistory(storage);
    expect(history).toEqual({ 'finding.police': [2], 'mission.takeoff': [1] });
    const cards = cardsOf(buildForensicReport({ storage, random: seeded(1) }));
    expect(cards.police).not.toBe(FIXED_WORDINGS.police[2]);
  });

  it('still draws a report with no storage at all', () => {
    expect(buildForensicReport({ storage: null }).length).toBe(4);
  });

  it('refuses an order that breaks the invariants', () => {
    expect(() => checkTopicOrder(['vehicle', 'victim', 'samu', 'police'])).not.toThrow();
    expect(() => checkTopicOrder(['samu', 'victim', 'vehicle', 'police'])).toThrow(RangeError);
    expect(() => checkTopicOrder(['vehicle', 'victim', 'samu', 'samu'])).toThrow(RangeError);
  });
});

describe('drawForensicScript', () => {
  it('draws the six lines together, the card inside its own spoken line', () => {
    const script = drawForensicScript({ assessment: assessment('detected'), storage: null, random: seeded(7) });
    const lines = scriptLines(script);
    expect(lines).toHaveLength(6);
    expect(PHRASE_POOLS['inspection.intro']).toContain(lines[0]);
    expect(PHRASE_POOLS['inspection.outro']).toContain(lines[5]);
    for (const finding of script.findings) {
      expect(finding.speech.endsWith(`${finding.card.slice(1)}.`)).toBe(true);
    }
    expect(script.victimStatus).toBe('detected');
    expect(script.captureStamp).toBe('20261020_101500');
  });

  it('records the reason of a report drawn without an assessment', () => {
    const script = drawForensicScript({ assessment: null, reason: 'timeout', storage: null, random: seeded(1) });
    expect(script.victimStatus).toBe('unknown');
    expect(script.reason).toBe('timeout');
    expect(VICTIM_WORDINGS.unknown).toContain(cardsOf(script.findings).victim);
  });
});

describe('allFindingSentences', () => {
  it('covers every victim wording of the three branches', () => {
    const sentences = allFindingSentences();
    for (const status of STATUSES) {
      for (const wording of VICTIM_WORDINGS[status]) {
        expect(sentences.some((sentence) => sentence.endsWith(` ${wording.charAt(0).toLowerCase()}${wording.slice(1)}.`))).toBe(true);
      }
    }
  });
});
