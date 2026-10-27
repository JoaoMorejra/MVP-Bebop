/**
 * Every sentence the station can speak, as one finite manifest.
 *
 * The copilot voice is synthesized ahead and played from the on-disk phrase
 * cache (`telemetry/announcer.py:PhraseCache`), so a sentence missing here is
 * a sentence the station cannot say without the network. The manifest is the
 * union of the flight narration for each parameter set and flight plan, the
 * forensic report, the alerts the station composes itself, the detection call
 * (literal since D2), the link-loss call and the finite launch refusals. The
 * alerts the mission composes in Python are listed by
 * `mvp_mission_bebop/telemetry/spoken_alerts.py` and merged by the pre-cache
 * tool (`streamer/precache_speech.py`).
 *
 * `scripts/exportSpeechManifest.ts` prints it as JSON
 * (`bmg.speech_manifest.v1`), with the grouping of
 * `docs/TABELA_DE_FALAS_COPILOTO.md`.
 */

import {
  ALERT_FALLBACK,
  LAUNCH_REFUSED_ALERT,
  LINK_LOSS_SENTENCES,
  MILESTONE_KEYS,
  PHRASE_POOLS,
  PLAN_PHRASES,
  POOL_KEYS,
  alertSentence,
  allMissionPhrases,
  phraseValues,
  renderPhrase,
  spokenPercent,
  toPlan,
  type FlightPlan,
  type MissionParametersPayload,
  type PlanLike,
  type PlanPhraseKey,
  type PoolKey,
} from './copilotPhrases';
import { CLOSERS, FIXED_WORDINGS, LINKERS, OPENERS, VICTIM_WORDINGS, allFindingSentences } from './forensics';
import { BATTERY_LANDING_CALL } from './batteryReturn';
import type { VictimStatus } from '../types/bmg';

export type SpeechCategory =
  | 'voo.contagem'
  | 'voo.decolagem'
  | 'voo.varredura'
  | 'voo.aproximacao'
  | 'voo.captura'
  | 'voo.retorno'
  | 'voo.pouso'
  | 'laudo.abertura'
  | 'laudo.veiculo'
  | 'laudo.vitima'
  | 'laudo.samu'
  | 'laudo.policia'
  | 'laudo.encerramento'
  | 'falha.aborto'
  | 'falha.link'
  | 'falha.bateria'
  | 'falha.timeout_varredura'
  | 'falha.calibracao'
  | 'falha.decolagem'
  | 'falha.recusa_lancamento';

/** Every category, in the order of `TABELA_DE_FALAS_COPILOTO.md`. */
export const SPEECH_CATEGORIES: readonly SpeechCategory[] = [
  'voo.contagem',
  'voo.decolagem',
  'voo.varredura',
  'voo.aproximacao',
  'voo.captura',
  'voo.retorno',
  'voo.pouso',
  'laudo.abertura',
  'laudo.veiculo',
  'laudo.vitima',
  'laudo.samu',
  'laudo.policia',
  'laudo.encerramento',
  'falha.aborto',
  'falha.link',
  'falha.bateria',
  'falha.timeout_varredura',
  'falha.calibracao',
  'falha.decolagem',
  'falha.recusa_lancamento',
];

export interface SpeechEntry {
  text: string;
  category: SpeechCategory;
  /** File and pool the sentence comes from. */
  source: string;
  /** When the station speaks it. */
  condition: string;
}

export interface SpeechParameterSet {
  parameters: MissionParametersPayload;
  plan: PlanLike;
}

/**
 * Charge, in percent, the mission cites in `mission.battery_warning`
 * (`threshold_pct`). Mirrors `BATTERY_WARNING_PCT` in
 * `mvp_mission_bebop/telemetry/battery.py`, a fixed constant rather than a
 * parameter; `test/test_spoken_alerts.py` holds the two equal.
 */
export const BATTERY_WARNING_PCT = 10;

/**
 * The spoken parameters a mission emits with the defaults of
 * `mvp_mission_bebop/parameters.py` (`telemetry/mission_parameters.py`).
 * `test/test_spoken_alerts.py` holds these equal to `spoken_parameters` of a
 * default `MissionParameters`.
 */
export const SCHEMA_DEFAULT_PARAMETERS: MissionParametersPayload = { target_altitude_m: 1, cruise_mps: 0.2 };

/** The fair parameters (D7): spoken "2 metros" and "0,15 metro por segundo". */
export const FAIR_PARAMETERS: MissionParametersPayload = { target_altitude_m: 2, cruise_mps: 0.15 };

/** The four flight plans: both profiles, with and without the return. */
export const FLIGHT_PLANS: readonly FlightPlan[] = [
  { mode: 'advanced', rtlEnabled: true },
  { mode: 'advanced', rtlEnabled: false },
  { mode: 'simple', rtlEnabled: true },
  { mode: 'simple', rtlEnabled: false },
];

/**
 * The station's only battery sentence. The return by battery is narrated from
 * the mission's milestones; the station speaks only when it lands on its own.
 */
const BATTERY_LANDING_ENTRY: SpeechEntry = {
  text: BATTERY_LANDING_CALL,
  category: 'falha.bateria',
  source: 'src/lib/batteryReturn.ts:BATTERY_LANDING_CALL',
  condition:
    'Bateria no limiar crítico; sem missão em execução, salto recusado ou sem confirmação do retorno em duas janelas, pouso no local',
};

/**
 * Alert sentences the station composes itself, or that it has always cached
 * on its own. The rest come from the mission (`telemetry/spoken_alerts.py`).
 */
export const FIXED_ALERT_SENTENCES: readonly SpeechEntry[] = dedupe([
  {
    text: 'Missão abortada. Pouso imediato comandado.',
    category: 'falha.aborto',
    source: 'electron/main.cjs:bmg:abort-mission',
    condition: 'Aborto manual com a aeronave em voo ou sem solo confirmado',
  },
  {
    text: 'Missão abortada.',
    category: 'falha.aborto',
    source: 'electron/main.cjs:bmg:abort-mission',
    condition: 'Aborto manual com a aeronave comprovadamente em solo (flying_state 0)',
  },
  BATTERY_LANDING_ENTRY,
  {
    text: 'Falha na decolagem: decolagem não confirmada pela aeronave.',
    category: 'falha.decolagem',
    source: 'mvp_mission_bebop/steps/takeoff.py:_confirm_takeoff (alerta mission.takeoff_failed)',
    condition: 'A aeronave não confirmou a decolagem dentro do prazo (R1)',
  },
  {
    text: 'Falha na decolagem.',
    category: 'falha.decolagem',
    source: 'mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement',
    condition: 'Falha na decolagem relatada sem motivo',
  },
  {
    text: 'Falha de segurança.',
    category: 'falha.aborto',
    source: 'src/hooks/useCopilot.ts (lista histórica do cache)',
    condition: 'Mantida do cache anterior; nenhuma composição atual a produz',
  },
  {
    text: 'Falha na etapa.',
    category: 'falha.aborto',
    source: 'mvp_mission_bebop/telemetry/announcer.py:_format_telemetry_statement',
    condition: 'Falha de etapa relatada sem nome de etapa',
  },
  {
    text: ALERT_FALLBACK,
    category: 'falha.aborto',
    source: 'src/lib/copilotPhrases.ts:ALERT_FALLBACK',
    condition: 'Alerta da missão recebido sem frase própria',
  },
]);

/**
 * Launch refusals whose sentence is known before the launch. The reasons the
 * mission sends name the refused value, so only the fallback is finite.
 */
const LAUNCH_REFUSAL_ENTRIES: readonly SpeechEntry[] = [
  {
    text: alertSentence({}, LAUNCH_REFUSED_ALERT),
    category: 'falha.recusa_lancamento',
    source: 'src/lib/copilotPhrases.ts:alertSentence (mission.launch_refused)',
    condition: 'Documento de lançamento recusado sem motivo informado',
  },
];

const POOL_CATALOG: Readonly<Record<PoolKey, { category: SpeechCategory; condition: string }>> = {
  'mission.start': { category: 'voo.contagem', condition: 'Clique em iniciar missão (mission.start)' },
  'mission.countdown_3': { category: 'voo.contagem', condition: 'Fim da contagem regressiva (mission.countdown_3)' },
  'mission.takeoff': { category: 'voo.decolagem', condition: 'Decolagem confirmada, modo avançado (mission.takeoff)' },
  'mission.takeoff_native': {
    category: 'voo.decolagem',
    condition: 'Decolagem confirmada, modo simples (mission.takeoff)',
  },
  'mission.scan_start': { category: 'voo.varredura', condition: 'Início da varredura lateral (mission.scan_start)' },
  'mission.scan_start_yaw': {
    category: 'voo.varredura',
    condition: 'Início da varredura por rotação, modo simples (mission.scan_start)',
  },
  'mission.target_found': {
    category: 'voo.varredura',
    condition: 'Alvo confirmado pelo detector (mission.target_found)',
  },
  'mission.approaching': {
    category: 'voo.aproximacao',
    condition: 'Aproximação com guiagem visual (mission.approaching, ibvs true)',
  },
  'mission.approaching_direct': {
    category: 'voo.aproximacao',
    condition: 'Aproximação sem guiagem visual (mission.approaching, ibvs false)',
  },
  'mission.approaching_yaw': {
    category: 'voo.aproximacao',
    condition: 'Centralização angular, modo simples (mission.approaching)',
  },
  'mission.capture_done': {
    category: 'voo.captura',
    condition: 'Registro com alvo confirmado (mission.capture_done, target_confirmed true)',
  },
  'mission.capture_reference': {
    category: 'falha.timeout_varredura',
    condition: 'Registro sem alvo confirmado, após a janela de varredura (mission.capture_done)',
  },
  'mission.rtl_start': {
    category: 'voo.retorno',
    condition: 'Retorno após a inspeção, modo avançado com retorno (mission.rtl_start)',
  },
  'mission.rtl_start_jump': {
    category: 'voo.retorno',
    condition: 'Retorno comandado pela estação (mission.rtl_start, via_jump true)',
  },
  'mission.rtl_start_no_target': {
    category: 'falha.timeout_varredura',
    condition: 'Retorno sem alvo inspecionado (mission.rtl_start, inspected false)',
  },
  'mission.landing': { category: 'voo.pouso', condition: 'Pouso na base (mission.landing, at_base true)' },
  'mission.landing_in_place': {
    category: 'voo.pouso',
    condition: 'Pouso fora da base ou sem retorno (mission.landing)',
  },
  'mission.touchdown': {
    category: 'voo.pouso',
    condition: 'Toque confirmado na base (mission.touchdown, confirmed e at_base)',
  },
  'mission.touchdown_in_place': {
    category: 'voo.pouso',
    condition: 'Toque confirmado fora da base (mission.touchdown, confirmed)',
  },
  'mission.touchdown_unconfirmed': {
    category: 'voo.pouso',
    condition: 'Pouso sem confirmação odométrica do toque (mission.touchdown)',
  },
  'mission.battery_warning': {
    category: 'falha.bateria',
    condition: `Bateria no aviso fixo de ${BATTERY_WARNING_PCT} por cento (mission.battery_warning)`,
  },
  'mission.battery_return': {
    category: 'falha.bateria',
    condition: 'Bateria no limiar crítico, retorno forçado, modo avançado (mission.battery_return)',
  },
  'mission.battery_return_simple': {
    category: 'falha.bateria',
    condition: 'Bateria no limiar crítico, pouso forçado sobre a base, modo simples (mission.battery_return)',
  },
  'mission.rtl_start_battery': {
    category: 'falha.bateria',
    condition: 'Início da busca do marcador no retorno por bateria (mission.rtl_start, battery_return true)',
  },
  'mission.landing_battery': {
    category: 'falha.bateria',
    condition: 'Pouso na base no retorno por bateria (mission.landing, battery_return e at_base)',
  },
  'mission.landing_battery_in_place': {
    category: 'falha.bateria',
    condition: 'Pouso fora da base no retorno por bateria (mission.landing, battery_return)',
  },
  'mission.touchdown_battery': {
    category: 'falha.bateria',
    condition: 'Toque confirmado na base no retorno por bateria (mission.touchdown, battery_return e at_base)',
  },
  'mission.touchdown_battery_in_place': {
    category: 'falha.bateria',
    condition: 'Toque confirmado fora da base no retorno por bateria (mission.touchdown, battery_return)',
  },
  'inspection.intro': { category: 'laudo.abertura', condition: 'Aeronave em solo, início do laudo (inspection.intro)' },
  'inspection.outro': {
    category: 'laudo.encerramento',
    condition: 'Último card do laudo exibido (inspection.outro)',
  },
};

const PLAN_CONDITIONS: Readonly<Record<PlanPhraseKey, string>> = {
  'plan.rtl': 'Início da contagem, plano com retorno à base',
  'plan.land_in_place': 'Início da contagem, modo avançado sem retorno',
  'plan.land_in_place_simple': 'Início da contagem, modo simples sem retorno',
};

/**
 * Pools in the order a shared sentence is attributed: the milestone's own pool
 * before the derived ones, so "Iniciando retorno à base." files under the
 * return and not under the return without a target.
 */
const ATTRIBUTION_ORDER: readonly PoolKey[] = [
  ...MILESTONE_KEYS,
  ...POOL_KEYS.filter((key) => !(MILESTONE_KEYS as readonly string[]).includes(key)),
];

function poolEntry(key: PoolKey, text: string): SpeechEntry {
  const { category, condition } = POOL_CATALOG[key];
  return { text, category, condition, source: `src/lib/copilotPhrases.ts:PHRASE_POOLS['${key}']` };
}

/** The sentences of one parameter set and plan, each attributed to its pool. */
function missionEntries(set: SpeechParameterSet): SpeechEntry[] {
  const plan = toPlan(set.plan);
  const values = phraseValues({}, set.parameters);
  const owner = new Map<string, SpeechEntry>();
  for (const key of ATTRIBUTION_ORDER) {
    for (const variant of PHRASE_POOLS[key]) {
      const text = renderPhrase(variant, values);
      if (!owner.has(text)) owner.set(text, poolEntry(key, text));
    }
  }
  for (const [planKey, sentences] of Object.entries(PLAN_PHRASES) as [PlanPhraseKey, readonly string[]][]) {
    for (const text of sentences) {
      if (owner.has(text)) continue;
      owner.set(text, {
        text,
        category: 'voo.contagem',
        source: `src/lib/copilotPhrases.ts:PLAN_PHRASES['${planKey}']`,
        condition: PLAN_CONDITIONS[planKey],
      });
    }
  }
  return allMissionPhrases(set.parameters, plan).map((text) => {
    const entry = owner.get(text);
    if (!entry) throw new Error(`no pool renders "${text}"`);
    return entry;
  });
}

/** The battery warning with the threshold its milestone carries. */
function batteryWarningEntries(): SpeechEntry[] {
  const values = { threshold: spokenPercent(BATTERY_WARNING_PCT) };
  return PHRASE_POOLS['mission.battery_warning']
    .filter((variant) => variant.includes('{threshold}'))
    .map((variant) => poolEntry('mission.battery_warning', renderPhrase(variant, values)));
}

const VICTIM_CONDITIONS: Readonly<Record<VictimStatus, string>> = {
  detected: 'Vítima detectada na imagem da inspeção (victim_detected true)',
  not_detected: 'Nenhuma vítima detectada na imagem da inspeção',
  unknown: 'Inferência de vítima não concluída (unknown): linha neutra',
};

function decapitalisedMatch(sentence: string, wording: string): boolean {
  return sentence.toLowerCase().includes(wording.toLowerCase());
}

/** A finding sentence, attributed to its topic by the wording it carries. */
function findingEntry(text: string): SpeechEntry {
  const source = 'src/lib/forensics.ts:allFindingSentences';
  const fixed: ReadonlyArray<['police' | 'samu' | 'vehicle', SpeechCategory, string]> = [
    ['police', 'laudo.policia', 'Card de autoridade policial do laudo'],
    ['samu', 'laudo.samu', 'Card de socorro médico do laudo'],
    ['vehicle', 'laudo.veiculo', 'Card do veículo, com guincho acionado'],
  ];
  for (const [topic, category, condition] of fixed) {
    if (FIXED_WORDINGS[topic].some((wording) => decapitalisedMatch(text, wording))) {
      return { text, category, source: `${source} (FIXED_WORDINGS.${topic})`, condition };
    }
  }
  for (const status of Object.keys(VICTIM_WORDINGS) as VictimStatus[]) {
    if (VICTIM_WORDINGS[status].some((wording) => decapitalisedMatch(text, wording))) {
      return {
        text,
        category: 'laudo.vitima',
        source: `${source} (VICTIM_WORDINGS.${status})`,
        condition: VICTIM_CONDITIONS[status],
      };
    }
  }
  throw new Error(`no topic carries "${text}"`);
}

function linkLossEntries(): SpeechEntry[] {
  return LINK_LOSS_SENTENCES.map((text) => ({
    text,
    category: 'falha.link' as const,
    source: 'src/lib/copilotPhrases.ts:LINK_LOSS_SENTENCES',
    condition: 'Telemetria da aeronave interrompida durante um voo real (useLinkLossNarration), uma vez por perda',
  }));
}

function dedupe(entries: readonly SpeechEntry[]): SpeechEntry[] {
  const seen = new Set<string>();
  const out: SpeechEntry[] = [];
  for (const entry of entries) {
    if (seen.has(entry.text)) continue;
    seen.add(entry.text);
    out.push(entry);
  }
  return out;
}

/** `parameters`, each in the four flight plans. */
export function speechParameterSets(parameters: readonly MissionParametersPayload[]): SpeechParameterSet[] {
  return parameters.flatMap((set) => FLIGHT_PLANS.map((plan) => ({ parameters: set, plan })));
}

/**
 * Every sentence the station can speak for the parameter sets given.
 *
 * Deduplicated by text, the first attribution winning, in a fixed order: the
 * flight narration of each set and plan with the battery warning at its
 * threshold, the forensic findings, the report's intro and outro, the station's
 * fixed alerts, the six detection calls, the link-loss call and the finite
 * launch refusals. Everything after the narration is independent of `sets`.
 */
export function buildSpeechManifest(sets: readonly SpeechParameterSet[]): SpeechEntry[] {
  const entries: SpeechEntry[] = [];
  for (const set of sets) {
    entries.push(...missionEntries(set));
    entries.push(...batteryWarningEntries());
  }
  entries.push(...allFindingSentences().map(findingEntry));
  for (const key of ['inspection.intro', 'inspection.outro'] as const) {
    entries.push(...PHRASE_POOLS[key].map((text) => poolEntry(key, text)));
  }
  entries.push(...FIXED_ALERT_SENTENCES);
  entries.push(...PHRASE_POOLS['mission.target_found'].map((text) => poolEntry('mission.target_found', text)));
  entries.push(...linkLossEntries());
  entries.push(...LAUNCH_REFUSAL_ENTRIES);
  return dedupe(entries);
}

/**
 * The spoken parameters of a `mission_config.json` document, as
 * `telemetry/mission_parameters.py:spoken_parameters` emits them on
 * `mission.parameters`: the altitude rounded to 2 decimals, the cruise
 * converted with the lateral gain (`SpeedCalibration.lateral_gain`, which
 * inherits `normalized_to_mps` when `lateral_normalized_to_mps` is null) and
 * rounded to 3. Only the keys the narration speaks are returned; a value that
 * is not a finite number is left out.
 *
 * @throws TypeError when `doc` is not a JSON object.
 */
export function missionConfigParameters(doc: unknown): MissionParametersPayload {
  if (!isRecord(doc)) throw new TypeError('mission_config.json must hold a JSON object');
  const kinematics = isRecord(doc.kinematics) ? doc.kinematics : {};
  const out: Record<string, number> = {};
  const altitude = finiteNumber(kinematics.target_altitude_m);
  if (altitude !== null) out.target_altitude_m = roundTo(altitude, 2);
  const cruise = finiteNumber(kinematics.forward_cruise_velocity);
  const longitudinal = finiteNumber(kinematics.normalized_to_mps) ?? 1;
  const gain = finiteNumber(kinematics.lateral_normalized_to_mps) ?? longitudinal;
  if (cruise !== null) out.cruise_mps = roundTo(cruise * gain, 3);
  return out;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function roundTo(value: number, decimals: number): number {
  const scale = 10 ** decimals;
  return Math.round(value * scale) / scale;
}

export interface SpeechTableRow {
  category: SpeechCategory;
  condition: string;
  source: string;
  text: string;
}

export interface SpeechTableGroup {
  category: SpeechCategory;
  title: string;
  rows: SpeechTableRow[];
}

export interface SpeechTableSection {
  title: string;
  groups: SpeechTableGroup[];
  notes: string[];
}

const CATEGORY_TITLES: Readonly<Record<SpeechCategory, string>> = {
  'voo.contagem': 'Contagem e plano de voo',
  'voo.decolagem': 'Decolagem',
  'voo.varredura': 'Varredura e detecção',
  'voo.aproximacao': 'Aproximação',
  'voo.captura': 'Captura',
  'voo.retorno': 'Retorno',
  'voo.pouso': 'Pouso',
  'laudo.abertura': 'Aberturas',
  'laudo.veiculo': 'Veículo com guincho',
  'laudo.vitima': 'Vítima detectada, não detectada e desconhecida',
  'laudo.samu': 'SAMU',
  'laudo.policia': 'Polícia',
  'laudo.encerramento': 'Encerramento',
  'falha.aborto': 'Aborto manual e falhas de etapa',
  'falha.link': 'Perda de enlace e watchdog',
  'falha.bateria': 'Bateria crítica e auto-land',
  'falha.timeout_varredura': 'Timeout de varredura sem alvo',
  'falha.calibracao': 'Falha de calibração',
  'falha.decolagem': 'Recusa e falha de decolagem',
  'falha.recusa_lancamento': 'Recusa de lançamento',
};

const SECTIONS: ReadonlyArray<{ title: string; prefix: string; notes: string[] }> = [
  { title: 'Voo normal', prefix: 'voo.', notes: [] },
  {
    title: 'Laudo pericial',
    prefix: 'laudo.',
    notes: [
      'Cenário A (vítima não detectada ou inferência não concluída): Veículo, Vítima, depois SAMU e Polícia.',
      'Cenário B (vítima detectada): Vítima, Veículo, depois SAMU e Polícia.',
      'SAMU e Polícia alternam a ordem entre si de um voo para o outro (finding.dispatch_order).',
      `Cada card é lido após um conector: ${OPENERS.length} aberturas no primeiro, ${LINKERS.length} ligações nos do meio e ${CLOSERS.length} fechamentos no último.`,
    ],
  },
  { title: 'Falhas', prefix: 'falha.', notes: [] },
];

/** The manifest grouped as `TABELA_DE_FALAS_COPILOTO.md`: three sections, one group per category. */
export function buildSpeechTable(entries: readonly SpeechEntry[]): SpeechTableSection[] {
  return SECTIONS.map(({ title, prefix, notes }) => ({
    title,
    notes: [...notes],
    groups: SPEECH_CATEGORIES.filter((category) => category.startsWith(prefix)).map((category) => ({
      category,
      title: CATEGORY_TITLES[category],
      rows: entries
        .filter((entry) => entry.category === category)
        .map(({ text, source, condition }) => ({ category, condition, source, text })),
    })),
  }));
}
