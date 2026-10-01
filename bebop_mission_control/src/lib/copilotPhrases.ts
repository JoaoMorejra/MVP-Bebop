/**
 * What the copilot says at each milestone of the flight script, and the memory
 * that keeps it from saying it the same way flight after flight.
 *
 * The keys are the "pool key" column of the synchronisation table in
 * `docs/superpowers/specs/2026-09-24-bmg-mission-experience-design.md` (section
 * 4). The mission process raises the in-flight ones as `[MILESTONE <key>]`
 * lines (`mvp_mission_bebop/telemetry/milestones.py`); `mission.start` and
 * `mission.countdown_3` belong to the station, and the two `inspection.*` keys
 * frame the post-landing report.
 *
 * Selection lives here, not in `announcer.py`, which only plays what it is
 * given: the station decides the sentence, so the line in the operator's ear
 * and the one on screen are always the same string. The register follows
 * `_format_telemetry_statement` in the announcer — short, declarative, two
 * clauses at most.
 *
 * Every pool holds more variants than the history window, so excluding the
 * variants of the last five flights always leaves at least one candidate.
 */

export type MilestoneKey =
  | 'mission.start'
  | 'mission.countdown_3'
  | 'mission.takeoff'
  | 'mission.scan_start'
  | 'mission.target_found'
  | 'mission.approaching'
  | 'mission.capture_done'
  | 'mission.rtl_start'
  | 'mission.landing'
  | 'mission.touchdown'
  | 'mission.battery_warning'
  | 'inspection.intro'
  | 'inspection.outro';

/**
 * A phrase pool. A milestone speaks from its own pool unless its payload says
 * the event was a different one than the default wording assumes
 * (`poolForMilestone`): a touchdown away from the base, or one odometry never
 * confirmed. Each pool keeps its own cross-flight history.
 */
export type PoolKey =
  | MilestoneKey
  | 'mission.touchdown_in_place'
  | 'mission.touchdown_unconfirmed'
  | 'mission.capture_reference'
  | 'mission.rtl_start_jump'
  | 'mission.rtl_start_no_target'
  | 'mission.landing_in_place'
  | 'mission.approaching_direct';

/** Pools holding one dictated sentence rather than variants. */
export const FIXED_POOLS: readonly PoolKey[] = ['mission.touchdown_unconfirmed'];

/**
 * Rate of spoken pt-BR at the copilot's register, in words per second. Used
 * only to bound phrase length; the synthesizer sets the real pace.
 */
export const SPEECH_WORDS_PER_SECOND = 2.5;

/** Longest a flight call may run, so it never overlaps the next milestone. */
export const FLIGHT_CALL_MAX_SECONDS = 4;

/** Pools exempt from {@link FLIGHT_CALL_MAX_SECONDS}: the post-landing report is descriptive by design. */
export const DESCRIPTIVE_KEYS: readonly PoolKey[] = ['inspection.intro', 'inspection.outro'];

/**
 * Estimated spoken duration of `text`, in seconds.
 *
 * Word count over {@link SPEECH_WORDS_PER_SECOND}. Numbers written as digits
 * count as one word, which slightly underestimates "1,5 metro"; the bound is
 * a lint on phrase length, not a timing source.
 */
export function estimateSpeechSeconds(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return words / SPEECH_WORDS_PER_SECOND;
}

/** Flights of memory per key. */
export const HISTORY_WINDOW = 5;

/** Where the history lives. Versioned: a schema change gets a new key, not a migration. */
export const HISTORY_STORAGE_KEY = 'bmg.copilot-history.v1';

/**
 * Choices the forensic report remembers across flights: the wording used for
 * each topic, and the topic order (an index into the 24 permutations).
 */
export type FindingHistoryKey =
  | 'finding.police'
  | 'finding.samu'
  | 'finding.victim'
  | 'finding.vehicle'
  | 'finding.order'
  | 'finding.opener'
  | 'finding.linker'
  | 'finding.closer';

/** Everything with cross-flight memory. */
export type HistoryKey = PoolKey | FindingHistoryKey;

const FINDING_HISTORY_KEYS: readonly FindingHistoryKey[] = [
  'finding.police',
  'finding.samu',
  'finding.victim',
  'finding.vehicle',
  'finding.order',
  'finding.opener',
  'finding.linker',
  'finding.closer',
];

/** Variant index used per key, oldest first, at most {@link HISTORY_WINDOW} entries. */
export type HistoryStore = Partial<Record<HistoryKey, number[]>>;

/** The part of `Storage` this module touches, so tests and non-browser hosts can supply one. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const PHRASE_POOLS: Readonly<Record<PoolKey, readonly string[]>> = {
  'mission.start': [
    'Missão iniciada. Carregando parâmetros de voo.',
    'Sequência de missão iniciada. Parâmetros de voo em carregamento.',
    'Início de missão confirmado. Carregando configuração de voo.',
    'Missão autônoma iniciada. Lendo parâmetros da estação.',
    'Iniciando missão. Parâmetros de voo sendo carregados.',
    'Missão em preparação. Carregando perfil de voo.',
  ],
  'mission.countdown_3': [
    'Parâmetros carregados. Decolagem em instantes.',
    'Decolagem iminente. Afastem-se da aeronave.',
    'Contagem final. Mantenham distância da aeronave.',
    'Perfil de voo carregado. Decolagem iminente.',
    'Área de decolagem deve estar livre. Decolagem em instantes.',
    'Parâmetros aplicados. Afastem-se da aeronave.',
  ],
  'mission.takeoff': [
    'Decolagem autorizada. Subindo para {altitude}.',
    'Decolagem em curso. Altitude alvo de {altitude}.',
    'Aeronave no ar. Estabilizando a {altitude} de altura.',
    'Decolagem confirmada. Altitude alvo de {altitude}.',
    'Voo autônomo iniciado. Subida para {altitude}.',
    'Decolagem executada. Altitude de operação de {altitude}.',
    'Decolagem confirmada. Subindo para a altitude de operação.',
    'Aeronave no ar. Subida para a altitude configurada.',
  ],
  'mission.scan_start': [
    'Varredura linear iniciada. Buscando ocorrências na pista.',
    'Iniciando varredura da pista. Visão computacional ativa.',
    'Deslocamento de busca iniciado. Monitorando a área.',
    'Varredura em andamento. Procurando sinistro ao longo da pista.',
    'Busca iniciada. Detector de objetos em operação.',
    'Iniciando busca retilínea. Câmera apontada para a pista.',
    'Varredura iniciada a {speed}.',
    'Busca em curso a {speed}.',
  ],
  'mission.target_found': [
    'Sinistro detectado {location}.',
    'Alvo identificado {location}.',
    'Ocorrência localizada {location}.',
    'Detecção confirmada {location}.',
    'Sinistro avistado {location}.',
    'Alvo avistado {location}.',
  ],
  'mission.approaching': [
    'Iniciando aproximação controlada ao alvo.',
    'Aproximação em curso. Guiagem visual engajada.',
    'Deslocando até o sinistro sob controle visual.',
    'Aproximação iniciada. Centralizando o alvo na câmera.',
    'Guiagem visual ativa. Aproximando do local da ocorrência.',
    'Em aproximação ao alvo. Velocidade reduzida.',
  ],
  'mission.capture_done': [
    'Registro fotográfico concluído. Imagem enviada para inspeção.',
    'Evidência capturada. Foto encaminhada para análise.',
    'Captura concluída. Imagem disponível para inspeção.',
    'Foto do sinistro registrada. Enviando para inspeção.',
    'Registro pericial concluído. Imagem enviada à estação.',
    'Evidência fotográfica armazenada. Pronta para inspeção.',
  ],
  'mission.capture_reference': [
    'Captura de referência registrada, sem alvo confirmado.',
    'Imagem de referência salva, sem alvo confirmado.',
    'Registro de referência concluído, sem alvo confirmado.',
    'Foto de referência capturada, sem alvo confirmado.',
    'Captura registrada sem alvo confirmado.',
    'Referência visual salva, sem alvo confirmado.',
  ],
  'mission.approaching_direct': [
    'Iniciando aproximação controlada ao alvo.',
    'Em aproximação ao alvo. Velocidade reduzida.',
    'Aproximação ao alvo em curso.',
    'Deslocando até o sinistro.',
    'Aproximação iniciada. Aeronave a caminho do alvo.',
    'Aproximando do local da ocorrência.',
  ],
  'mission.rtl_start_jump': [
    'Retorno à base comandado pela estação.',
    'Retorno comandado. Navegando para o ponto de lançamento.',
    'Retorno à base comandado. Rota de volta em curso.',
    'Retorno comandado. Voltando à base.',
    'Retorno comandado pelo operador. Regressando à base.',
    'Retorno à base comandado. Buscando marcador de pouso.',
  ],
  'mission.rtl_start_no_target': [
    'Iniciando retorno à base.',
    'Retorno à base iniciado. Navegando para o ponto de lançamento.',
    'Retornando ao ponto de decolagem. Buscando marcador de pouso.',
    'Retorno autônomo engajado. Rota de volta à base.',
    'Busca encerrada. Retornando à base.',
    'Regressando ao ponto de lançamento.',
  ],
  'mission.landing_in_place': [
    'Iniciando pouso no local. Mantenham a área livre.',
    'Pouso no ponto atual. Área deve estar desobstruída.',
    'Aeronave em descida final para pouso.',
    'Descida para pouso iniciada.',
    'Preparando pouso no local. Atenção à área.',
    'Pouso fora do ponto de lançamento iniciado.',
  ],
  'mission.rtl_start': [
    'Iniciando retorno à base.',
    'Retorno à base iniciado. Navegando para o ponto de lançamento.',
    'Inspeção concluída. Regressando à base.',
    'Retornando ao ponto de decolagem. Buscando marcador de pouso.',
    'Retorno autônomo engajado. Rota de volta à base.',
    'Deixando o local da ocorrência. Retorno à base em curso.',
  ],
  'mission.landing': [
    'Iniciando pouso. Mantenham a área livre.',
    'Pouso iminente na base. Área de pouso deve estar desobstruída.',
    'Aeronave em descida final para pouso.',
    'Preparando pouso. Atenção na área da base.',
    'Descida para pouso iniciada.',
    'Aproximação final concluída. Pousando.',
  ],
  'mission.battery_warning': [
    'Bateria abaixo de {threshold}.',
    'Atenção: bateria abaixo de {threshold}.',
    'Carga da bateria abaixo de {threshold}. Missão segue.',
    'Aviso de bateria: abaixo de {threshold}.',
    'Bateria em nível de alerta, abaixo de {threshold}.',
    'Bateria abaixo de {threshold}. Monitorando a carga.',
    'Aviso de bateria baixa. Missão segue.',
    'Atenção à carga da bateria.',
  ],
  'mission.touchdown': [
    'Pouso seguro concluído com sucesso na base.',
    'Pouso confirmado na base.',
    'Aeronave pousada na base de lançamento.',
    'Toque no solo confirmado na base.',
    'Pouso concluído na base de lançamento.',
    'Aeronave em solo na base. Pouso confirmado.',
  ],
  'mission.touchdown_in_place': [
    'Pouso confirmado no local.',
    'Aeronave pousada no ponto atual. Pouso confirmado.',
    'Pouso confirmado com toque no solo, no local.',
    'Pouso concluído no ponto atual.',
    'Aeronave em solo no local do pouso.',
    'Pouso confirmado. Aeronave em solo.',
  ],
  'mission.touchdown_unconfirmed': ['Pouso comandado. Confirmação de toque indisponível. Verifique visualmente.'],
  'inspection.intro': [
    'Inspeção do sinistro concluída. Apresentando laudo preliminar.',
    'Aeronave em solo. Apresentando os achados da inspeção.',
    'Análise da ocorrência concluída. Seguem os pontos do laudo.',
    'Laudo preliminar disponível. Apresentando os resultados.',
    'Inspeção finalizada. Relatando os pontos avaliados.',
    'Avaliação do local concluída. Apresentando o parecer preliminar.',
  ],
  'inspection.outro': [
    'Relatório pericial emitido e pronto para exportação.',
    'Laudo concluído. Relatório disponível para exportação.',
    'Fim do laudo preliminar. Documento pronto para envio.',
    'Relatório finalizado e disponível na estação.',
    'Parecer emitido. Missão pronta para encerramento.',
    'Laudo pericial completo. Relatório pronto para exportação.',
  ],
};

/** Milestone keys the station narrates, as they arrive on `bmg:milestone`. */
export const MILESTONE_KEYS: readonly MilestoneKey[] = [
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
  'inspection.intro',
  'inspection.outro',
];

/** Every phrase pool. */
export const POOL_KEYS = Object.keys(PHRASE_POOLS) as readonly PoolKey[];

/**
 * The pool a milestone speaks from, given what its payload says happened.
 *
 * `mission.touchdown` (`steps/rtl.py:_touchdown`): "na base" only for a
 * confirmed touchdown with `at_base`; a touchdown away from it has its own
 * wording; anything not explicitly `confirmed: true` is the dictated
 * unconfirmed sentence.
 */
export function poolForMilestone(key: MilestoneKey, payload: Readonly<Record<string, unknown>>): PoolKey {
  // Every flag is read as "did not happen" unless the mission says it did:
  // a wording that claims an event the payload does not confirm is the defect
  // these pools exist to prevent.
  switch (key) {
    case 'mission.touchdown':
      if (payload.confirmed !== true) return 'mission.touchdown_unconfirmed';
      return payload.at_base === true ? 'mission.touchdown' : 'mission.touchdown_in_place';
    case 'mission.capture_done':
      return payload.target_confirmed === true ? 'mission.capture_done' : 'mission.capture_reference';
    case 'mission.rtl_start':
      if (payload.via_jump === true) return 'mission.rtl_start_jump';
      return payload.inspected === true ? 'mission.rtl_start' : 'mission.rtl_start_no_target';
    case 'mission.landing':
      return payload.at_base === true ? 'mission.landing' : 'mission.landing_in_place';
    case 'mission.approaching':
      return payload.ibvs === true ? 'mission.approaching' : 'mission.approaching_direct';
    default:
      return key;
  }
}

/**
 * Variants a payload rules out within its pool: the landing marker is only
 * mentioned on a return the mission reported as marker-guided.
 */
function vetoFor(key: MilestoneKey, payload: Readonly<Record<string, unknown>>): (text: string) => boolean {
  if (key === 'mission.rtl_start' && payload.marker_guided !== true) return (text) => /marcador/i.test(text);
  return () => false;
}

/**
 * Variants that exist only for a milestone whose number the mission did not
 * send (D2: the frontend never supplies one). Eligible only when no other
 * variant of the pool is.
 */
export const FALLBACK_VARIANTS: ReadonlySet<string> = new Set([
  'Decolagem confirmada. Subindo para a altitude de operação.',
  'Aeronave no ar. Subida para a altitude configurada.',
  'Aviso de bateria baixa. Missão segue.',
  'Atenção à carga da bateria.',
]);

/** Whether `value` names a narrated milestone. Milestone keys arrive as untyped IPC strings. */
export function isMilestoneKey(value: string): value is MilestoneKey {
  return (MILESTONE_KEYS as readonly string[]).includes(value);
}

function isHistoryKey(value: string): value is HistoryKey {
  return (
    Object.prototype.hasOwnProperty.call(PHRASE_POOLS, value) ||
    (FINDING_HISTORY_KEYS as readonly string[]).includes(value)
  );
}

/**
 * Draw one variant of `key`, avoiding the ones used in the last flights.
 *
 * Excludes the variants recorded for the last {@link HISTORY_WINDOW} flights.
 * A pool too small for that — which none of the shipped pools is — degrades to
 * excluding the most recent `pool.length - 1`, so it still never repeats the
 * previous flight's line and never fails. Recorded indices the pool no longer
 * has (a pool edited between releases) are disregarded.
 *
 * @throws RangeError when `pool` is empty.
 */
export function pickVariant(
  key: HistoryKey,
  pool: readonly string[],
  history: HistoryStore,
  random: () => number = Math.random,
  eligible: (index: number) => boolean = () => true
): { text: string; index: number } {
  if (pool.length === 0) throw new RangeError(`phrase pool for ${key} is empty`);
  const allowed = pool.map((_, index) => index).filter(eligible);
  if (allowed.length === 0) throw new RangeError(`phrase pool for ${key} has no eligible variant`);
  const allowedSet = new Set(allowed);

  const recorded = (history[key] ?? []).filter((index) => allowedSet.has(index));
  const excludable = Math.min(HISTORY_WINDOW, allowed.length - 1);
  const excluded = new Set(excludable > 0 ? recorded.slice(-excludable) : []);
  const candidates = allowed.filter((index) => !excluded.has(index));

  const draw = Math.min(candidates.length - 1, Math.floor(random() * candidates.length));
  const index = candidates[Math.max(0, draw)];
  return { text: pool[index], index };
}

/** Append one flight's pick for `key`, dropping the oldest beyond the window. Pure. */
export function recordVariant(history: HistoryStore, key: HistoryKey, index: number): HistoryStore {
  const entries = [...(history[key] ?? []), index].slice(-HISTORY_WINDOW);
  return { ...history, [key]: entries };
}

function defaultStorage(): StorageLike | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    // Reading `localStorage` itself throws when site data is blocked.
    return null;
  }
}

/**
 * Read the history, keeping only what this build can use.
 *
 * Never throws: a missing, blocked or corrupt store reads as no history, which
 * costs at most one repeated line rather than a silent copilot.
 */
export function loadHistory(storage: StorageLike | null = defaultStorage()): HistoryStore {
  if (!storage) return {};
  let parsed: unknown;
  try {
    const raw = storage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) return {};
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};

  const history: HistoryStore = {};
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!isHistoryKey(key) || !Array.isArray(value)) continue;
    const entries = value.filter(
      (entry): entry is number => typeof entry === 'number' && Number.isInteger(entry) && entry >= 0
    );
    if (entries.length) history[key] = entries.slice(-HISTORY_WINDOW);
  }
  return history;
}

/** Persist the history. Returns false when the store refused the write. */
export function saveHistory(history: HistoryStore, storage: StorageLike | null = defaultStorage()): boolean {
  if (!storage) return false;
  try {
    storage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history));
    return true;
  } catch {
    // Quota or private mode. The pick already happened; only the memory is lost.
    return false;
  }
}

/**
 * The two station-owned lines of a launch, which carry no placeholder and can
 * therefore be drawn before the milestone that speaks them.
 */
export const LAUNCH_KEYS = ['mission.start', 'mission.countdown_3'] as const;
export type LaunchKey = (typeof LAUNCH_KEYS)[number];

const reserved = new Map<LaunchKey, string>();

/**
 * The line `key` will be spoken with at the next launch, drawn now if it has
 * not been yet.
 *
 * Drawn ahead so its audio can be synthesized before it is needed: once the
 * milestone fires, the line plays with no synthesis wait. The draw is recorded
 * in the cross-flight history when made, as `nextPhrase` does.
 */
export function reserveLaunchPhrase(
  key: LaunchKey,
  options: { storage?: StorageLike | null; random?: () => number } = {}
): string {
  const held = reserved.get(key);
  if (held !== undefined) return held;
  const text = nextPhrase(key, {}, options);
  reserved.set(key, text);
  return text;
}

/** The reserved line for `key`, handed over once; undefined when none was drawn. */
export function takeLaunchPhrase(key: MilestoneKey): string | undefined {
  if (!(LAUNCH_KEYS as readonly string[]).includes(key)) return undefined;
  const text = reserved.get(key as LaunchKey);
  reserved.delete(key as LaunchKey);
  return text;
}

/** The `{name}` placeholders a variant needs, in order of appearance. */
export function placeholdersOf(template: string): string[] {
  return Array.from(template.matchAll(/\{([a-z_]+)\}/g), (match) => match[1]);
}

/** Replace `{name}` placeholders. Unknown placeholders are left as written. */
export function renderPhrase(template: string, values: Readonly<Record<string, string>> = {}): string {
  return template.replace(/\{([a-z_]+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match
  );
}

const DECIMAL = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

/**
 * A distance as the copilot should pronounce it: decimal comma, one decimal at
 * most, and the singular below two as the formal norm has it ("1,5 metro").
 */
export function spokenMeters(value: number): string {
  if (!Number.isFinite(value)) return 'altitude configurada';
  const rounded = Math.round(value * 10) / 10;
  return `${DECIMAL.format(rounded)} ${Math.abs(rounded) < 2 ? 'metro' : 'metros'}`;
}

const DECIMAL_2 = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });

/**
 * A speed as the copilot pronounces it: two decimals at most, since the
 * configured cruise speeds are fractions of a metre per second, and the formal
 * singular below two. Null for a value that is not a finite number: there is
 * no default to say instead.
 */
export function spokenSpeed(value: number): string | null {
  if (!Number.isFinite(value)) return null;
  const rounded = Math.round(value * 100) / 100;
  return `${DECIMAL_2.format(rounded)} ${Math.abs(rounded) < 2 ? 'metro' : 'metros'} por segundo`;
}

/** A percentage as spoken: whole number, "por cento". */
export function spokenPercent(value: number): string {
  return `${DECIMAL.format(Math.round(value))} por cento`;
}

/**
 * The effective parameters of the current mission, as `mission.py` emits them
 * once on `mission.parameters` (`telemetry/mission_parameters.py`). The only
 * source of a spoken parameter value besides the milestone's own payload.
 */
export type MissionParametersPayload = Readonly<Record<string, unknown>>;

const finite = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/**
 * The placeholder values one milestone may use (D2).
 *
 * Every number comes from the mission: the milestone's own payload first, then
 * the `mission.parameters` document the mission launched with. Nothing is
 * defaulted here; a value neither carries is absent, and the variants that
 * need it are not eligible.
 */
export function phraseValues(
  payload: Readonly<Record<string, unknown>>,
  parameters: MissionParametersPayload
): Record<string, string> {
  const values: Record<string, string> = {
    location: describeTargetLocation(payload as TargetFix),
  };
  const altitude = finite(payload.altitude_m) ?? finite(parameters.target_altitude_m);
  if (altitude !== null) values.altitude = spokenMeters(altitude);
  // The battery warning cites its configured threshold, never the reading
  // (`battery_pct`): the reading is why the call happens, not what it says.
  const threshold = finite(payload.threshold_pct);
  if (threshold !== null) values.threshold = spokenPercent(threshold);
  const speed = finite(payload.cruise_mps) ?? finite(parameters.cruise_mps);
  const spokenCruise = speed === null ? null : spokenSpeed(speed);
  if (spokenCruise) values.speed = spokenCruise;
  return values;
}

/** The subset of the `mission.target_found` payload the phrase uses. */
export interface TargetFix {
  forward_m?: number | null;
  bearing_deg?: number | null;
}

/** Bearing inside which the target is called dead ahead, in degrees. */
const AHEAD_DEG = 2;
/** Bearing beyond which the target is called to one side rather than slightly to it. */
const ASIDE_DEG = 10;

/**
 * Where the target lies, as a phrase that completes "Sinistro detectado ...".
 *
 * Built from the pinhole projection the mission attaches to the milestone.
 * Positive bearing is to the right of the nose. Without a range the phrase
 * falls back to the runway itself rather than inventing a distance.
 */
export function describeTargetLocation(fix: TargetFix | null | undefined): string {
  const forward = fix?.forward_m;
  if (typeof forward !== 'number' || !Number.isFinite(forward) || forward <= 0) return 'na pista';

  let phrase = `a ${spokenMeters(forward)} à frente`;
  const bearing = fix?.bearing_deg;
  if (typeof bearing === 'number' && Number.isFinite(bearing) && Math.abs(bearing) >= AHEAD_DEG) {
    const side = bearing > 0 ? 'à direita' : 'à esquerda';
    phrase += Math.abs(bearing) < ASIDE_DEG ? `, levemente ${side}` : `, ${side}`;
  }
  return phrase;
}

/**
 * The spoken line for one milestone event, drawn with cross-flight memory.
 *
 * Numbers follow D2: the value configured for this mission, from the
 * milestone payload or the `mission.parameters` document (`phraseValues`).
 * The frontend never supplies one; a variant whose number is missing is not
 * eligible and a number-free variant is spoken instead.
 */
export function phraseForMilestone(
  key: MilestoneKey,
  payload: Readonly<Record<string, unknown>>,
  parameters: MissionParametersPayload,
  options: { storage?: StorageLike | null; random?: () => number } = {}
): string {
  return nextPhrase(poolForMilestone(key, payload), phraseValues(payload, parameters), {
    ...options,
    veto: vetoFor(key, payload),
  });
}

/** Pools whose sentence carries a flight estimate and cannot be known before the flight. */
const FLIGHT_ESTIMATE_POOLS: ReadonlySet<PoolKey> = new Set<PoolKey>(['mission.target_found']);

/**
 * Every sentence the flight narration can say for a mission launched with
 * `parameters` (D2: the numbers are known before takeoff), for the copilot's
 * on-disk phrase cache. Variants whose placeholder has no value are left out;
 * `mission.target_found` is left out because its range is estimated in flight.
 */
export function allMissionPhrases(parameters: MissionParametersPayload): string[] {
  const values = phraseValues({}, parameters);
  delete values.location;
  const out = new Set<string>();
  for (const key of POOL_KEYS) {
    if (FLIGHT_ESTIMATE_POOLS.has(key)) continue;
    for (const variant of PHRASE_POOLS[key]) {
      const rendered = renderPhrase(variant, values);
      if (!/\{[a-z_]+\}/.test(rendered)) out.add(rendered);
    }
  }
  return [...out];
}

/** Said when an alert arrives without a sentence of its own. */
const ALERT_FALLBACK = 'Alerta de voo recebido. Verifique a aeronave.';

/**
 * The sentence for a mission alert: the one the mission composed for it, which
 * carries the specific fault ("Alerta de voo: odometria perdida. ..."), or a
 * generic call when the payload has none.
 */
export function alertSentence(payload: Readonly<Record<string, unknown>> | null | undefined): string {
  const text = payload?.text;
  return typeof text === 'string' && text.trim() ? text.trim() : ALERT_FALLBACK;
}

/**
 * Pick, render and remember one line for `key`, in a single call.
 *
 * The pick is recorded before it is spoken, so a flight that ends mid-sentence
 * still counts it: the operator heard at least the start of it.
 */
export function nextPhrase(
  key: PoolKey,
  values: Readonly<Record<string, string>> = {},
  options: { storage?: StorageLike | null; random?: () => number; veto?: (text: string) => boolean } = {}
): string {
  const storage = options.storage === undefined ? defaultStorage() : options.storage;
  const history = loadHistory(storage);
  const pool = PHRASE_POOLS[key];
  const veto = options.veto ?? (() => false);
  const satisfied = (index: number) =>
    !veto(pool[index]) &&
    placeholdersOf(pool[index]).every((name) => Object.prototype.hasOwnProperty.call(values, name));
  const preferred = pool.some((text, index) => !FALLBACK_VARIANTS.has(text) && satisfied(index));
  const eligible = (index: number) =>
    satisfied(index) && (!preferred || !FALLBACK_VARIANTS.has(pool[index]));
  const { text, index } = pickVariant(key, pool, history, options.random, eligible);
  saveHistory(recordVariant(history, key, index), storage);
  return renderPhrase(text, values);
}
