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
  | 'inspection.intro'
  | 'inspection.outro';

/** Flights of memory per key. */
export const HISTORY_WINDOW = 5;

/** Where the history lives. Versioned: a schema change gets a new key, not a migration. */
export const HISTORY_STORAGE_KEY = 'bmg.copilot-history.v1';

/** Variant index used per key, oldest first, at most {@link HISTORY_WINDOW} entries. */
export type HistoryStore = Partial<Record<MilestoneKey, number[]>>;

/** The part of `Storage` this module touches, so tests and non-browser hosts can supply one. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const PHRASE_POOLS: Readonly<Record<MilestoneKey, readonly string[]>> = {
  'mission.start': [
    'Missão iniciada. Carregando parâmetros de voo.',
    'Sequência de missão iniciada. Parâmetros de voo em carregamento.',
    'Início de missão confirmado. Carregando configuração de voo.',
    'Missão autônoma iniciada. Lendo parâmetros da estação.',
    'Iniciando missão. Parâmetros de voo sendo carregados.',
    'Missão em preparação. Carregando perfil de voo.',
  ],
  'mission.countdown_3': [
    'Parâmetros carregados. Decolagem em três segundos.',
    'Configuração validada. Iniciando voo em três segundos.',
    'Parâmetros de voo prontos. Três segundos para a decolagem.',
    'Perfil de voo carregado. Decolagem iminente.',
    'Sistemas prontos. Início do voo em três segundos.',
    'Parâmetros aplicados. Afastem-se, decolagem em três segundos.',
  ],
  'mission.takeoff': [
    'Decolagem autorizada. Subindo para {altitude}.',
    'Decolagem em curso. Altitude alvo de {altitude}.',
    'Aeronave no ar. Estabilizando a {altitude} de altura.',
    'Decolagem confirmada. Teto operacional configurado em {altitude}.',
    'Voo autônomo iniciado. Subida para {altitude}.',
    'Decolagem executada. Altitude de operação de {altitude}.',
  ],
  'mission.scan_start': [
    'Varredura linear iniciada. Buscando ocorrências na pista.',
    'Iniciando varredura da pista. Visão computacional ativa.',
    'Cruzeiro de busca engajado. Monitorando a pista.',
    'Varredura em andamento. Procurando sinistro ao longo da pista.',
    'Busca iniciada. Detector de objetos em operação.',
    'Iniciando busca retilínea. Câmera apontada para a pista.',
  ],
  'mission.target_found': [
    'Sinistro detectado {location}. Confirmando alvo.',
    'Alvo identificado {location}.',
    'Ocorrência localizada {location}. Preparando aproximação.',
    'Detecção confirmada {location}.',
    'Veículo sinistrado avistado {location}.',
    'Alvo adquirido pela visão computacional {location}.',
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
    'Captura em alta fidelidade concluída. Imagem disponível para inspeção.',
    'Foto do sinistro registrada. Enviando para inspeção.',
    'Registro pericial concluído. Imagem enviada à estação.',
    'Evidência fotográfica armazenada. Pronta para inspeção.',
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

export const MILESTONE_KEYS = Object.keys(PHRASE_POOLS) as readonly MilestoneKey[];

function isMilestoneKey(value: string): value is MilestoneKey {
  return Object.prototype.hasOwnProperty.call(PHRASE_POOLS, value);
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
  key: MilestoneKey,
  pool: readonly string[],
  history: HistoryStore,
  random: () => number = Math.random
): { text: string; index: number } {
  if (pool.length === 0) throw new RangeError(`phrase pool for ${key} is empty`);

  const recorded = (history[key] ?? []).filter((index) => index >= 0 && index < pool.length);
  const excludable = Math.min(HISTORY_WINDOW, pool.length - 1);
  const excluded = new Set(excludable > 0 ? recorded.slice(-excludable) : []);
  const candidates = pool.map((_, index) => index).filter((index) => !excluded.has(index));

  const draw = Math.min(candidates.length - 1, Math.floor(random() * candidates.length));
  const index = candidates[Math.max(0, draw)];
  return { text: pool[index], index };
}

/** Append one flight's pick for `key`, dropping the oldest beyond the window. Pure. */
export function recordVariant(history: HistoryStore, key: MilestoneKey, index: number): HistoryStore {
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
    if (!isMilestoneKey(key) || !Array.isArray(value)) continue;
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
 * Pick, render and remember one line for `key`, in a single call.
 *
 * The pick is recorded before it is spoken, so a flight that ends mid-sentence
 * still counts it: the operator heard at least the start of it.
 */
export function nextPhrase(
  key: MilestoneKey,
  values: Readonly<Record<string, string>> = {},
  options: { storage?: StorageLike | null; random?: () => number } = {}
): string {
  const storage = options.storage === undefined ? defaultStorage() : options.storage;
  const history = loadHistory(storage);
  const { text, index } = pickVariant(key, PHRASE_POOLS[key], history, options.random);
  saveHistory(recordVariant(history, key, index), storage);
  return renderPhrase(text, values);
}
