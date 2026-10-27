import { describe, expect, it } from 'vitest';
import {
  BATTERY_POOLS,
  LINK_LOSS_SENTENCES,
  PHRASE_POOLS,
  poolsForPlan,
  alertSentence,
  isMilestoneAllowed,
  phraseForMilestone,
  renderPhrase,
  spokenPercent,
  type FlightPlan,
  type MilestoneKey,
  type MissionParametersPayload,
} from './copilotPhrases';
import { buildForensicReport, drawForensicScript } from './forensics';
import { BATTERY_LANDING_CALL } from './batteryReturn';
import type { InspectionAssessment } from '../types/bmg';
import {
  BATTERY_WARNING_PCT,
  FAIR_PARAMETERS,
  FIXED_ALERT_SENTENCES,
  FLIGHT_PLANS,
  SCHEMA_DEFAULT_PARAMETERS,
  SPEECH_CATEGORIES,
  buildSpeechManifest,
  buildSpeechTable,
  missionConfigParameters,
  speechParameterSets,
  type SpeechEntry,
} from './spokenCatalog';

const BANNED = new RegExp(['sini', 'stro'].join(''), 'i');

const DETECTED: InspectionAssessment = {
  schema: 'bmg.inspection.v1',
  capture_stamp: '20261024_101500',
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
  completed_at_utc: '2026-10-24T13:15:01+00:00',
};

const NOT_DETECTED: InspectionAssessment = { ...DETECTED, victim_detected: false, victim_count: 0, victim_boxes: [] };

const DRAWS = Array.from({ length: 12 }, (_, index) => () => index / 12);

const defaultManifest = (): SpeechEntry[] =>
  buildSpeechManifest(speechParameterSets([FAIR_PARAMETERS, SCHEMA_DEFAULT_PARAMETERS]));

const texts = (entries: readonly SpeechEntry[]): Set<string> => new Set(entries.map((entry) => entry.text));

/** Every payload shape the mission sends for a milestone, so each pool and veto is reached. */
function payloadsFor(key: MilestoneKey, parameters: MissionParametersPayload): Record<string, unknown>[] {
  switch (key) {
    case 'mission.takeoff':
      return [{}, { altitude_m: parameters.target_altitude_m }, { altitude_m: parameters.target_altitude_m, native: true }];
    case 'mission.scan_start':
      return [{}, { window_sec: 30, cruise_mps: parameters.cruise_mps }];
    case 'mission.target_found':
      return [{}, { forward_m: 3.3, bearing_deg: 12 }, { forward_m: Number.NaN, bearing_deg: Number.NaN }];
    case 'mission.approaching':
      return [{}, { ibvs: true }, { ibvs: false }];
    case 'mission.capture_done':
      return [{}, { target_confirmed: true }, { target_confirmed: false }];
    case 'mission.rtl_start':
      return [
        {},
        { via_jump: true },
        { inspected: true },
        { inspected: true, marker_guided: true },
        { inspected: false, marker_guided: true },
        { battery_return: true, via_jump: true, marker_guided: true },
        { battery_return: true, via_jump: true, marker_guided: false },
      ];
    case 'mission.landing':
      return [{}, { at_base: true }, { at_base: false }, { at_base: true, battery_return: true }, { at_base: false, battery_return: true }];
    case 'mission.touchdown':
      return [
        {},
        { confirmed: true, at_base: true },
        { confirmed: true, at_base: false },
        { confirmed: false },
        { confirmed: true, at_base: true, battery_return: true },
        { confirmed: true, at_base: false, battery_return: true },
        { confirmed: false, battery_return: true },
      ];
    case 'mission.battery_return':
      return [{ battery_pct: 19, threshold_pct: 20, from_stage: 2 }, { mode: 'advanced' }, { mode: 'simple' }];
    case 'mission.battery_warning':
      return [{}, { battery_pct: 9.6, threshold_pct: BATTERY_WARNING_PCT }];
    default:
      return [{}];
  }
}

const MILESTONES: readonly MilestoneKey[] = [
  'mission.start',
  'mission.countdown_3',
  'mission.takeoff',
  'mission.scan_start',
  'mission.target_found',
  'mission.approaching',
  'mission.capture_done',
  'mission.rtl_start',
  'mission.landing',
  'mission.touchdown',
  'mission.battery_warning',
  'mission.battery_return',
  'inspection.intro',
  'inspection.outro',
];

describe('spoken catalog (offline voice manifest)', () => {
  it('the detection call never carries a distance or a side', () => {
    const manifest = texts(defaultManifest());
    for (const payload of [{ forward_m: 3.3, bearing_deg: 12 }, { forward_m: Number.NaN, bearing_deg: Number.NaN }, {}]) {
      for (const random of DRAWS) {
        const line = phraseForMilestone('mission.target_found', payload, {}, { storage: null, random });
        expect(line).toMatch(/na pista\.$/);
        expect(line).not.toMatch(/metro|esquerda|direita|\{/);
        expect(manifest.has(line), line).toBe(true);
      }
    }
  });

  it('speaks the fair parameters', () => {
    const manifest = texts(buildSpeechManifest(speechParameterSets([FAIR_PARAMETERS])));
    expect(FAIR_PARAMETERS).toEqual({ target_altitude_m: 2, cruise_mps: 0.15 });
    expect(manifest.has('Decolagem autorizada. Subindo para 2 metros.')).toBe(true);
    expect(manifest.has('Varredura iniciada a 0,15 metro por segundo.')).toBe(true);
  });

  it('has the configured battery threshold line', () => {
    const manifest = texts(defaultManifest());
    const threshold = spokenPercent(BATTERY_WARNING_PCT);
    const withThreshold = PHRASE_POOLS['mission.battery_warning'].filter((variant) => variant.includes('{threshold}'));
    expect(withThreshold.length).toBeGreaterThan(0);
    for (const variant of withThreshold) {
      expect(manifest.has(renderPhrase(variant, { threshold })), variant).toBe(true);
    }
    for (const random of DRAWS) {
      const line = phraseForMilestone(
        'mission.battery_warning',
        { battery_pct: 9.6, threshold_pct: BATTERY_WARNING_PCT },
        {},
        { storage: null, random }
      );
      expect(manifest.has(line), line).toBe(true);
    }
  });

  it('manifest has no unresolved placeholder and no banned word', () => {
    const entries = defaultManifest();
    const seen = new Set<string>();
    for (const entry of entries) {
      expect(entry.text).not.toMatch(/\{[a-z_]+\}/);
      expect(entry.text).not.toMatch(BANNED);
      expect(entry.condition).not.toMatch(BANNED);
      expect(SPEECH_CATEGORIES).toContain(entry.category);
      expect(entry.source.trim().length, entry.text).toBeGreaterThan(0);
      expect(entry.condition.trim().length, entry.text).toBeGreaterThan(0);
      expect(seen.has(entry.text), entry.text).toBe(false);
      seen.add(entry.text);
    }
  });

  it('covers every sentence the station can compose', () => {
    for (const parameters of [SCHEMA_DEFAULT_PARAMETERS, FAIR_PARAMETERS]) {
      const manifest = texts(buildSpeechManifest(speechParameterSets([parameters])));
      for (const plan of FLIGHT_PLANS) {
        for (const key of MILESTONES) {
          for (const payload of payloadsFor(key, parameters)) {
            if (!isMilestoneAllowed(key, plan, payload)) continue;
            for (const random of DRAWS) {
              const line = phraseForMilestone(key, payload, parameters, { storage: null, random }, plan);
              expect(manifest.has(line), `${plan.mode}/${plan.rtlEnabled} ${key}: ${line}`).toBe(true);
            }
          }
        }
      }
      for (const assessment of [null, DETECTED, NOT_DETECTED]) {
        for (const random of DRAWS) {
          for (const finding of buildForensicReport({ assessment, storage: null, random })) {
            expect(manifest.has(finding.speech), finding.speech).toBe(true);
          }
          const script = drawForensicScript({ assessment, storage: null, random });
          expect(manifest.has(script.intro), script.intro).toBe(true);
          expect(manifest.has(script.outro), script.outro).toBe(true);
        }
      }
      expect(manifest.has(BATTERY_LANDING_CALL), BATTERY_LANDING_CALL).toBe(true);
      expect(manifest.has(alertSentence({}, 'mission.launch_refused'))).toBe(true);
      expect(manifest.has(alertSentence({}))).toBe(true);
      expect(manifest.has(alertSentence({ text: 'Missão abortada. Pouso imediato comandado.' }, 'mission.abort'))).toBe(true);
    }
  });

  it('lists the three link-loss variants under falha.link', () => {
    expect(LINK_LOSS_SENTENCES).toEqual([
      'Enlace com a aeronave perdido. Verifique a aeronave visualmente.',
      'Telemetria interrompida. Confirme a posição da aeronave a olho.',
      'Sem dados da aeronave. Mantenha contato visual e prepare o pouso manual.',
    ]);
    const entries = defaultManifest();
    for (const sentence of LINK_LOSS_SENTENCES) {
      const entry = entries.find((candidate) => candidate.text === sentence);
      expect(entry?.category, sentence).toBe('falha.link');
    }
  });

  it('files the six detection lines under the sweep and the fixed alerts under failures', () => {
    const entries = defaultManifest();
    for (const variant of PHRASE_POOLS['mission.target_found']) {
      expect(entries.find((entry) => entry.text === variant)?.category, variant).toBe('voo.varredura');
    }
    for (const alert of FIXED_ALERT_SENTENCES) {
      expect(alert.category.startsWith('falha.'), alert.text).toBe(true);
      expect(entries.find((entry) => entry.text === alert.text)?.category).toBe(alert.category);
    }
    const byText = new Map(entries.map((entry) => [entry.text, entry.category]));
    expect(byText.get('Iniciando retorno à base.')).toBe('voo.retorno');
    expect(byText.get('Para começar, sem necessidade de polícia.')).toBe('laudo.policia');
    expect(byText.get('Por fim, sem necessidade de Samu.')).toBe('laudo.samu');
    expect(byText.get('Em seguida, nenhuma vítima visível na cena.')).toBe('laudo.vitima');
    expect(byText.get('Por fim, veículo sem danos estruturais aparentes. Guincho acionado para desobstrução da via.')).toBe(
      'laudo.veiculo'
    );
    expect(byText.get(PHRASE_POOLS['inspection.intro'][0])).toBe('laudo.abertura');
    expect(byText.get(PHRASE_POOLS['inspection.outro'][0])).toBe('laudo.encerramento');
    expect(byText.get('Missão abortada. Pouso imediato comandado.')).toBe('falha.aborto');
    expect(byText.get(BATTERY_LANDING_CALL)).toBe('falha.bateria');
  });

  it('holds a single battery sentence of the station, the emergency landing', () => {
    const entries = defaultManifest();
    const station = entries.filter((entry) => entry.source.startsWith('src/lib/batteryReturn.ts'));
    expect(station.map((entry) => entry.text)).toEqual([BATTERY_LANDING_CALL]);
    const spoken = new Set(entries.map((entry) => entry.text));
    for (const retired of [
      'Bateria crítica. Retorno à base já em curso.',
      'Bateria crítica. Pouso já em curso.',
      'Bateria crítica: pousando no local.',
      'Bateria crítica. Pouso na base já em curso.',
    ]) {
      expect(spoken.has(retired), retired).toBe(false);
    }
  });

  it('files every battery return sentence under falha.bateria, each plan caching the ones it can say', () => {
    const entries = defaultManifest();
    const byText = new Map(entries.map((entry) => [entry.text, entry.category]));
    const all = BATTERY_POOLS.flatMap((key) => PHRASE_POOLS[key]);
    expect(all.length).toBeGreaterThanOrEqual(42);
    for (const line of all) expect(byText.get(line), line).toBe('falha.bateria');
    for (const plan of FLIGHT_PLANS) {
      const own = texts(buildSpeechManifest([{ parameters: FAIR_PARAMETERS, plan }]));
      for (const key of poolsForPlan(plan).filter((pool) => BATTERY_POOLS.includes(pool))) {
        for (const line of PHRASE_POOLS[key]) expect(own.has(line), `${plan.mode}/${plan.rtlEnabled} ${line}`).toBe(true);
      }
    }
    const stationBattery = FIXED_ALERT_SENTENCES.filter((entry) => entry.category === 'falha.bateria');
    expect(stationBattery.map((entry) => entry.text)).toEqual([BATTERY_LANDING_CALL]);
  });

  it('is deterministic and holds the fixed sentences without any parameter set', () => {
    const sets = speechParameterSets([FAIR_PARAMETERS, SCHEMA_DEFAULT_PARAMETERS]);
    expect(buildSpeechManifest(sets)).toEqual(buildSpeechManifest(sets));
    expect(sets).toHaveLength(8);
    const plans = new Set(sets.map((set) => `${(set.plan as FlightPlan).mode}/${(set.plan as FlightPlan).rtlEnabled}`));
    expect(plans).toEqual(new Set(['advanced/true', 'advanced/false', 'simple/true', 'simple/false']));
    const fixedOnly = texts(buildSpeechManifest([]));
    for (const sentence of LINK_LOSS_SENTENCES) expect(fixedOnly.has(sentence)).toBe(true);
    for (const variant of PHRASE_POOLS['mission.target_found']) expect(fixedOnly.has(variant)).toBe(true);
  });
});

describe('mission_config.json as spoken parameters', () => {
  it('reads the altitude and the cruise the mission would emit on mission.parameters', () => {
    const doc = {
      kinematics: {
        target_altitude_m: 2,
        forward_cruise_velocity: 0.15,
        normalized_to_mps: 1,
        lateral_normalized_to_mps: null,
      },
    };
    expect(missionConfigParameters(doc)).toEqual({ target_altitude_m: 2, cruise_mps: 0.15 });
  });

  it('applies the lateral gain, as SpeedCalibration.lateral_gain does', () => {
    const doc = {
      kinematics: { target_altitude_m: 1.234, forward_cruise_velocity: 0.2, normalized_to_mps: 1.5, lateral_normalized_to_mps: 0.8 },
    };
    expect(missionConfigParameters(doc)).toEqual({ target_altitude_m: 1.23, cruise_mps: 0.16 });
    const inherited = { kinematics: { target_altitude_m: 1, forward_cruise_velocity: 0.2, normalized_to_mps: 1.5 } };
    expect(missionConfigParameters(inherited)).toEqual({ target_altitude_m: 1, cruise_mps: 0.3 });
  });

  it('refuses a document that is not an object and leaves out a value that is not a finite number', () => {
    expect(() => missionConfigParameters(null)).toThrow(TypeError);
    expect(() => missionConfigParameters([])).toThrow(TypeError);
    expect(missionConfigParameters({ kinematics: { target_altitude_m: 'x' } })).toEqual({});
  });
});

describe('speech table', () => {
  it('groups the manifest in the three sections of TABELA_DE_FALAS_COPILOTO.md', () => {
    const entries = defaultManifest();
    const table = buildSpeechTable(entries);
    expect(table.map((section) => section.title)).toEqual(['Voo normal', 'Laudo pericial', 'Falhas']);
    const rows = table.flatMap((section) => section.groups.flatMap((group) => group.rows));
    expect(rows).toHaveLength(entries.length);
    for (const section of table) {
      for (const group of section.groups) {
        for (const row of group.rows) expect(row.category).toBe(group.category);
      }
    }
    expect(table[1].notes.join(' ')).toMatch(/Cenário A/);
    expect(table[1].notes.join(' ')).toMatch(/Cenário B/);
    expect(table.map((section) => section.groups.map((group) => group.category)).flat()).toEqual([...SPEECH_CATEGORIES]);
  });
});
