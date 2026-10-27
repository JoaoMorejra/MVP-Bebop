/**
 * Conflicts between parameters: combinations that each field's range admits
 * but the mission cannot fly as configured, or flies degraded.
 *
 * Two levels. An `error` is provably inexecutable: it violates a hard backend
 * invariant or an interlock, or keeps a stage from ending. The sheet refuses to
 * save it and the launch is blocked. A `warning` flies, but degrades or fails
 * under plausible conditions; the operator confirms it at save time.
 *
 * Every input is read from the document, fields outside the sheet included. A
 * rule whose input is missing or not a finite number is skipped, never fed a
 * default: `launchBlockers` already names the missing field. The same rules
 * and constants live in `mvp_mission_bebop/parameter_conflicts.py`, and
 * `test/fixtures/parameter_conflict_cases.json` holds both to the same answers.
 *
 * The mission mode is the station's concern alone: {@link activeConflicts}
 * drops the conflicts a mode never flies into, and the backend mirror stays
 * mode-agnostic.
 */
import {
  ALTITUDE_MAX_M,
  ALTITUDE_MIN_M,
  VENUE_HEADROOM_M,
  launchesFromGround,
  marginUnlocked,
  padClearanceM,
  padHeightM,
} from './altitudeEnvelope';
import { isPathActive, readMissionMode } from './missionMode';
import { markerCount } from './parameterSchema';
import { getPath } from './paths';

export type ConflictSeverity = 'error' | 'warning';

export interface ParameterConflict {
  id: string;
  severity: ConflictSeverity;
  /** Parameters involved, as dotted document paths. */
  paths: string[];
  title: string;
  /** What diverges, with the current figures, and what it costs in flight. */
  message: string;
  /** A concrete correction, with the figure computed. */
  fix: string;
}

/**
 * Height at which the Bebop 2 firmware ends its takeoff, metres. The airframe
 * ignores the altitude it is given and stabilizes here
 * (`actuators/simulator.py`, `FIRMWARE_HOVER_ALTITUDE_M`; `TakeoffStep._acquire_altitude`).
 * Estimated from the code's comments ("around one metre"), not measured in
 * flight: confirm against a real flight log. The mission's ceiling ratchet
 * (`estimation/ceiling_schedule.py`) and {@link ceilingFloorFrame} rest on it.
 */
export const FIRMWARE_HOVER_HEIGHT_M = 1.0;

/**
 * Factory value of the low-altitude advisory floor (W03): the larger of
 * `vision.min_altitude_for_ibvs_m` (0.35) and
 * `kinematics.takeoff_settle_min_altitude_m` (0.30). The rule reads both
 * fields from the document; this is the figure they give at the factory.
 */
export const LOW_ALTITUDE_ADVISORY_M = 0.35;

/**
 * Factory value of the ceiling-margin noise floor (W02):
 * `kinematics.climb_deadband_m` (0.05) plus twice
 * `kinematics.climb_settle_max_position_sigma_m` (0.08). The rules read both
 * fields from the document.
 */
export const LOW_CEILING_MARGIN_ADVISORY_M = 0.21;

/** Resolution floor of a marker module for a reliable decode, pixels (W12). */
export const MIN_PX_PER_MODULE = 4;

/**
 * IMU settle after the flat trim, seconds (`steps/takeoff.py`,
 * `_FLAT_TRIM_SETTLE_SEC`). No longer a blocking wait, it still bounds how soon
 * the takeoff can follow the flat trim (W10).
 */
export const FLAT_TRIM_SETTLE_SEC = 2.0;

/** Width of the REC1080_STREAM480 frame Stage 5 detects on, pixels (`mission.py`). */
export const STREAM_WIDTH_PX = 856;

/** Fastest forward cruise flown inside the original horizontal ceiling (W14). */
export const UNCALIBRATED_SPEED_ADVISORY = 0.3;

/** Share of the visible ground width the marker may cover with room for odometry error (W13). */
export const TAG_FRAME_SHARE = 0.5;

/** Tolerance of every comparison against a threshold derived by arithmetic. */
const EPS = 1e-9;

const P = {
  altitude: 'kinematics.target_altitude_m',
  margin: 'kinematics.altitude_ceiling_margin_m',
  forward: 'kinematics.forward_cruise_velocity',
  reverse: 'rtl.reverse_cruise_velocity',
  ceiling: 'kinematics.max_horizontal_speed',
  approach: 'kinematics.max_approach_forward_speed',
  search: 'gimbal.search_tilt_deg',
  nadir: 'gimbal.nadir_tilt_deg',
  nadirTolerance: 'gimbal.nadir_tilt_tolerance_deg',
  bandUp: 'gimbal.centering_tilt_band_up_deg',
  climbBand: 'kinematics.climb_deadband_m',
  climbSigma: 'kinematics.climb_settle_max_position_sigma_m',
  ibvsFloor: 'vision.min_altitude_for_ibvs_m',
  settleFloor: 'kinematics.takeoff_settle_min_altitude_m',
  fov: 'vision.horizontal_fov_deg',
  accel: 'kinematics.max_accel_mps2',
  jerk: 'kinematics.max_jerk_mps3',
  searchTimeout: 'timeouts.search_timeout_sec',
  rtlTimeout: 'rtl.timeout_sec',
  stabilize: 'kinematics.takeoff_stabilize_duration_sec',
  settleWindow: 'kinematics.takeoff_settle_window_sec',
  countdown: 'kinematics.countdown_sec',
  flatTrimAck: 'timeouts.flat_trim_ack_timeout_sec',
  returnTilt: 'rtl.camera_tilt_deg',
  tag: 'rtl.tag_size',
  markerId: 'rtl.target_aruco_id',
  markerDict: 'rtl.marker_dict',
  venue: 'platform.venue_ceiling_agl_m',
} as const;

/** Module grid of a dictionary family, without the black border; null when unknown. */
export function markerGrid(name: unknown): number | null {
  if (typeof name !== 'string') return null;
  if (name.includes('4X4') || name === 'DICT_APRILTAG_16h5') return 4;
  if (name.includes('5X5') || name === 'DICT_ARUCO_ORIGINAL' || name === 'DICT_APRILTAG_25h9') return 5;
  if (name.includes('6X6') || name === 'DICT_APRILTAG_36h10' || name === 'DICT_APRILTAG_36h11') return 6;
  if (name.includes('7X7')) return 7;
  return null;
}

const rad = (deg: number) => (Math.abs(deg) * Math.PI) / 180;
const m2 = (value: number) => `${value.toFixed(2)} m`;
const f2 = (value: number) => value.toFixed(2);
const s1 = (value: number) => (Number.isFinite(value) ? `${value.toFixed(1)} s` : 'sem limite');

type Read = (path: string) => number | null;

interface Rule {
  id: string;
  severity: ConflictSeverity;
  title: string;
  evaluate: (read: Read, doc: unknown, fired: ReadonlySet<string>) => Omit<ParameterConflict, 'id' | 'severity' | 'title'> | null;
}

/** Ground width the Stage 5 camera sees at the return altitude, metres. */
function returnFootprint(read: Read): { distance: number; width: number; tilt: number; fov: number } | null {
  const h = read(P.altitude);
  const tilt = read(P.returnTilt);
  const fov = read(P.fov);
  if (h === null || tilt === null || fov === null || Math.abs(tilt) < EPS || fov <= 0) return null;
  const distance = h / Math.sin(rad(tilt));
  return { distance, width: 2 * distance * Math.tan(rad(fov) / 2), tilt, fov };
}

function usableTag(read: Read): number | null {
  const tag = read(P.tag);
  return tag !== null && tag > 0 && tag <= 1 ? tag : null;
}

function noiseBand(read: Read): number | null {
  const band = read(P.climbBand);
  const sigma = read(P.climbSigma);
  return band === null || sigma === null ? null : band + 2 * sigma;
}

const RULES: readonly Rule[] = [
  {
    id: 'E01',
    severity: 'error',
    title: 'Câmera de inspeção acima da varredura',
    evaluate: (read) => {
      const s = read(P.search);
      const n = read(P.nadir);
      if (s === null || n === null || n <= s - EPS) return null;
      return {
        paths: [P.nadir, P.search],
        message: `A câmera precisa terminar mais baixa que o ângulo de varredura (nadir ${n.toFixed(0)}°, varredura ${s.toFixed(0)}°): a aproximação nunca alcança a atitude de inspeção.`,
        fix: `Ajuste o nadir para menos de ${s.toFixed(0)}° ou suba a varredura acima de ${n.toFixed(0)}°.`,
      };
    },
  },
  {
    id: 'E02',
    severity: 'error',
    title: 'Varredura e nadir quase iguais',
    evaluate: (read) => {
      const s = read(P.search);
      const n = read(P.nadir);
      const tol = read(P.nadirTolerance);
      if (s === null || n === null || tol === null || n > s - EPS || s - n > tol + EPS) return null;
      return {
        paths: [P.search, P.nadir],
        message: `Diferença entre varredura e nadir de ${(s - n).toFixed(1)}°, dentro da tolerância de chegada (${tol.toFixed(1)}°): a aproximação termina sem se mover.`,
        fix: `Deixe o nadir abaixo de ${(s - tol).toFixed(1)}°.`,
      };
    },
  },
  {
    // Retired: the mission's ceiling ratchet covers the end of the native
    // takeoff (`estimation/ceiling_schedule.py`). Kept so the ids and the order
    // shared with `parameter_conflicts.py` do not move.
    id: 'E03',
    severity: 'error',
    title: 'Teto abaixo da altura de decolagem do firmware',
    evaluate: () => null,
  },
  {
    id: 'E04',
    severity: 'error',
    title: 'Margem do teto menor que a banda de chegada',
    evaluate: (read, doc) => {
      const m = read(P.margin);
      const band = read(P.climbBand);
      if (!marginUnlocked(doc) || m === null || band === null || m >= band - EPS) return null;
      return {
        paths: [P.margin],
        message: `Margem de ${m2(m)} menor que a banda de chegada da subida (${m2(band)}): o teto dispara em voo nominal.`,
        fix: `Aumente a margem para pelo menos ${m2(band)}.`,
      };
    },
  },
  {
    id: 'E05',
    severity: 'error',
    title: 'Velocidades de avanço e retorno desacopladas',
    evaluate: (read) => {
      const vf = read(P.forward);
      const vr = read(P.reverse);
      if (vf === null || vr === null || (Math.abs(vf + vr) <= EPS && vr < 0)) return null;
      return {
        paths: [P.forward, P.reverse],
        message: `Velocidade de avanço (${f2(vf)}) e de retorno (${f2(vr)}) devem ter a mesma magnitude, com o retorno negativo.`,
        fix: `Mova o controle de velocidade uma vez: o retorno passa a ${f2(-Math.abs(vf))}.`,
      };
    },
  },
  {
    id: 'E06',
    severity: 'error',
    title: 'Teto de velocidade horizontal corta a velocidade pedida',
    evaluate: (read) => {
      const vmax = read(P.ceiling);
      const vf = read(P.forward);
      const vr = read(P.reverse);
      const va = read(P.approach);
      if (vmax === null || vf === null || vr === null || va === null) return null;
      const demand = Math.max(Math.abs(vf), Math.abs(vr), Math.abs(va));
      if (vmax >= demand - EPS && vmax < 1) return null;
      return {
        paths: [P.ceiling, P.forward],
        message:
          vmax >= 1
            ? `O teto de velocidade horizontal (${f2(vmax)}) não está abaixo de 1.0: deixa de proteger contra um defeito da guiagem.`
            : `O teto de velocidade horizontal (${f2(vmax)}) corta a velocidade pedida (${f2(demand)}): a aeronave voa mais devagar do que a ficha diz.`,
        fix: `Mova o controle de velocidade para a estação elevar o teto, entre ${f2(demand)} e 0.95.`,
      };
    },
  },
  {
    id: 'E07',
    severity: 'error',
    title: 'Tamanho de tag inválido',
    evaluate: (_read, doc) => {
      const tag = getPath(doc, P.tag);
      if (tag === undefined || (typeof tag === 'number' && Number.isFinite(tag) && tag > 0 && tag <= 1)) return null;
      return {
        paths: [P.tag],
        message: `Tamanho de tag ${String(tag)} fora de (0, 1.0] m: a estimativa de pose da base fica sem escala.`,
        fix: 'Informe a aresta externa da borda preta, em metros, entre 0.005 e 1.0.',
      };
    },
  },
  {
    id: 'E08',
    severity: 'error',
    title: 'ID do marcador fora do dicionário',
    evaluate: (_read, doc) => {
      const id = getPath(doc, P.markerId);
      if (id === undefined) return null;
      const dict = getPath(doc, P.markerDict);
      const count = markerCount(dict);
      const integer = typeof id === 'number' && Number.isInteger(id) && id >= 0;
      if (integer && (count === null || (id as number) < count)) return null;
      return {
        paths: [P.markerId, P.markerDict],
        message:
          count !== null
            ? `ID ${String(id)} não existe em ${String(dict)} (válidos 0 a ${count - 1}): a marca da base nunca é reconhecida.`
            : `ID ${String(id)} não é um inteiro igual ou maior que zero: a marca da base nunca é reconhecida.`,
        fix: count !== null ? `Use um ID entre 0 e ${count - 1}, ou outro dicionário.` : 'Use um ID inteiro igual ou maior que zero.',
      };
    },
  },
  {
    id: 'E09',
    severity: 'error',
    title: 'Dicionário do marcador não reconhecido',
    evaluate: (_read, doc) => {
      const dict = getPath(doc, P.markerDict);
      if (dict === undefined) return null;
      if (markerCount(dict) !== null) return null;
      if (typeof dict === 'string' && /^\s*\d+\s*$/.test(dict)) return null;
      if (typeof dict === 'number' && Number.isInteger(dict) && dict >= 0) return null;
      return {
        paths: [P.markerDict],
        message: `Dicionário ${JSON.stringify(dict)} não reconhecido: o pouso de precisão fica sem detector e o retorno é só por odometria.`,
        fix: 'Escolha um dicionário da lista em Configurações Avançadas.',
      };
    },
  },
  {
    id: 'E10',
    severity: 'error',
    title: 'Cota fora do envelope da superfície de decolagem',
    evaluate: (read, doc) => {
      const h = read(P.altitude);
      if (h === null) return null;
      const ground = launchesFromGround(doc);
      const low = ground ? ALTITUDE_MIN_M : Math.max(ALTITUDE_MIN_M, padHeightM(doc) + padClearanceM(doc));
      const high = ALTITUDE_MAX_M;
      if (h >= low - EPS && h <= high + EPS) return null;
      return {
        paths: [P.altitude],
        message:
          h < low && !ground
            ? `Cota de ${m2(h)} abaixo do mínimo de ${m2(low)} sobre o totem (topo do totem + folga).`
            : `Cota de ${m2(h)} fora da faixa de ${m2(low)} a ${m2(high)} sobre o piso.`,
        fix: `Cota entre ${m2(low)} e ${m2(high)}.`,
      };
    },
  },
  {
    id: 'E11',
    severity: 'error',
    title: 'Cota colada no teto do local',
    evaluate: (read) => {
      const h = read(P.altitude);
      const venue = read(P.venue);
      if (h === null || venue === null || h <= venue - VENUE_HEADROOM_M + EPS) return null;
      return {
        paths: [P.altitude, P.venue],
        message: `Cota de ${m2(h)} a menos de ${m2(VENUE_HEADROOM_M)} do teto do local (${m2(venue)}).`,
        fix: `Cota de no máximo ${m2(venue - VENUE_HEADROOM_M)}.`,
      };
    },
  },
  {
    // Retired with E03: the ceiling ratchet covers the end of the native takeoff.
    id: 'W01',
    severity: 'warning',
    title: 'Teto próximo da altura de decolagem do firmware',
    evaluate: () => null,
  },
  {
    id: 'W02',
    severity: 'warning',
    title: 'Margem do teto abaixo do ruído dos sensores',
    evaluate: (read, doc, fired) => {
      const m = read(P.margin);
      const band = noiseBand(read);
      if (!marginUnlocked(doc) || fired.has('E04') || m === null || band === null || m >= band - EPS) return null;
      return {
        paths: [P.margin],
        message: `Margem de ${m2(m)} abaixo do ruído de sonar e barômetro (${m2(band)}): risco de pouso de emergência por teto.`,
        fix: `Aumente a margem para pelo menos ${m2(band)}.`,
      };
    },
  },
  {
    id: 'W03',
    severity: 'warning',
    title: 'Altitude abaixo do piso da guiagem visual',
    evaluate: (read) => {
      const h = read(P.altitude);
      const ibvs = read(P.ibvsFloor);
      const settle = read(P.settleFloor);
      if (h === null || ibvs === null || settle === null) return null;
      const floor = Math.max(ibvs, settle);
      if (h > floor + EPS) return null;
      return {
        paths: [P.altitude],
        message: `Altitude de ${m2(h)}, no piso de ${m2(floor)} ou abaixo: a aproximação usa rampa aberta e a estabilização pós-decolagem tende a esperar o limite inteiro.`,
        fix: `Suba a altitude acima de ${m2(floor)}.`,
      };
    },
  },
  {
    id: 'W04',
    severity: 'warning',
    title: 'A aeronave tende a passar do alvo',
    // Retired: the overrun check rested on the forward approach distance, which the
    // lateral pattern does not have. Kept so rule ids and order match the backend.
    evaluate: () => null,
  },
  {
    id: 'W05',
    severity: 'warning',
    title: 'Aproximação curta demais',
    // Retired with W04: the minimum approach length no longer exists.
    evaluate: () => null,
  },
  {
    id: 'W06',
    severity: 'warning',
    title: 'Aproximação consome a janela de rastreamento',
    // Retired with W04: the approach no longer consumes the tracking window.
    evaluate: () => null,
  },
  {
    id: 'W07',
    severity: 'warning',
    title: 'Varredura curta para atingir a velocidade',
    evaluate: (read) => {
      const search = read(P.searchTimeout);
      const vf = read(P.forward);
      const accel = read(P.accel);
      const jerk = read(P.jerk);
      if (search === null || vf === null || accel === null || jerk === null || accel <= 0 || jerk <= 0) return null;
      const ramp = 2 * (vf / accel + accel / jerk);
      if (search >= ramp - EPS) return null;
      return {
        paths: [P.searchTimeout, P.forward],
        message: `A varredura de ${s1(search)} acaba antes de a aeronave atingir a velocidade pedida (rampa de ${s1(ramp)}).`,
        fix: `Aumente o timeout de varredura para pelo menos ${s1(ramp)}.`,
      };
    },
  },
  {
    id: 'W08',
    severity: 'warning',
    title: 'Janela de retorno menor que o tempo para voltar',
    evaluate: (read) => {
      const timeout = read(P.rtlTimeout);
      const vf = read(P.forward);
      const vr = read(P.reverse);
      const search = read(P.searchTimeout);
      if (timeout === null || vf === null || vr === null || search === null) return null;
      if (Math.abs(vr) < EPS) return null;
      const needed = (vf * search) / Math.abs(vr);
      if (timeout >= needed - EPS) return null;
      return {
        paths: [P.rtlTimeout, P.reverse, P.searchTimeout],
        message: `Janela de retorno de ${s1(timeout)} menor que o tempo para voltar no pior caso (${s1(needed)}): o pouso ocorrerá fora da base.`,
        fix: `Aumente o timeout de retorno para pelo menos ${s1(Math.ceil(needed - EPS))}.`,
      };
    },
  },
  {
    id: 'W09',
    severity: 'warning',
    title: 'Estabilização sem tempo para assentar',
    evaluate: (read) => {
      const stabilize = read(P.stabilize);
      const window = read(P.settleWindow);
      if (stabilize === null || window === null || stabilize > window + 1 + EPS) return null;
      return {
        paths: [P.stabilize],
        message: `Com ${s1(stabilize)} de estabilização, o critério de assentamento (janela de ${s1(window)} mais amostras) não tem tempo de ser satisfeito: a fase sempre dura o limite.`,
        fix: `Aumente a estabilização para mais de ${s1(window + 1)}.`,
      };
    },
  },
  {
    id: 'W10',
    severity: 'warning',
    title: 'Contagem menor que a sequência de solo',
    evaluate: (read) => {
      const countdown = read(P.countdown);
      const ack = read(P.flatTrimAck);
      if (countdown === null || ack === null) return null;
      const ground = ack + FLAT_TRIM_SETTLE_SEC;
      if (countdown >= ground - EPS) return null;
      return {
        paths: [P.countdown],
        message: `Contagem de ${s1(countdown)} menor que a sequência de solo (até ${s1(ground)}): a decolagem acontecerá depois do zero.`,
        fix: `Use pelo menos ${s1(ground)} de contagem.`,
      };
    },
  },
  {
    id: 'W11',
    severity: 'warning',
    title: 'Faixa de centragem acima da horizontal',
    evaluate: (read) => {
      const s = read(P.search);
      const band = read(P.bandUp);
      if (s === null || band === null || s + band <= EPS) return null;
      return {
        paths: [P.search],
        message: `Com a varredura em ${s.toFixed(0)}°, a faixa de centragem sobe a ${(s + band).toFixed(0)}° e ultrapassa a horizontal.`,
        fix: `Desça a varredura para ${(-band).toFixed(0)}° ou menos.`,
      };
    },
  },
  {
    id: 'W12',
    severity: 'warning',
    title: 'Marca pequena demais para a câmera',
    evaluate: (read, doc) => {
      const footprint = returnFootprint(read);
      const tag = usableTag(read);
      const grid = markerGrid(getPath(doc, P.markerDict));
      if (footprint === null || tag === null || grid === null) return null;
      const fx = STREAM_WIDTH_PX / 2 / Math.tan(rad(footprint.fov) / 2);
      const perModule = (fx * tag) / footprint.distance / (grid + 2);
      if (perModule >= MIN_PX_PER_MODULE - EPS) return null;
      return {
        paths: [P.tag, P.altitude],
        message: `A marca terá cerca de ${perModule.toFixed(1)} px por módulo a ${m2(footprint.distance)}: detecção pouco confiável.`,
        fix: `Use uma tag de pelo menos ${m2((MIN_PX_PER_MODULE * (grid + 2) * footprint.distance) / fx)} ou desça a altitude.`,
      };
    },
  },
  {
    id: 'W13',
    severity: 'warning',
    title: 'Marca sem folga no campo de visão',
    evaluate: (read, _doc, fired) => {
      const footprint = returnFootprint(read);
      const tag = usableTag(read);
      if (fired.has('W15') || footprint === null || tag === null) return null;
      if (tag <= TAG_FRAME_SHARE * footprint.width + EPS) return null;
      return {
        paths: [P.tag, P.altitude],
        message: `A marca de ${m2(tag)} ocupa mais da metade do campo visível (${m2(footprint.width)}): sem folga para erro de posição no retorno.`,
        fix: `Suba a altitude ou use uma tag de até ${m2(TAG_FRAME_SHARE * footprint.width)}.`,
      };
    },
  },
  {
    id: 'W14',
    severity: 'warning',
    title: 'Velocidade acima do envelope original',
    evaluate: (read) => {
      const vf = read(P.forward);
      if (vf === null || vf <= UNCALIBRATED_SPEED_ADVISORY + EPS) return null;
      return {
        paths: [P.forward],
        message: `Acima de ${f2(UNCALIBRATED_SPEED_ADVISORY)} o teto de velocidade horizontal é elevado junto, e o valor enviado é normalizado, não calibrado em m/s.`,
        fix: 'Valide em bancada e em voo curto antes de usar.',
      };
    },
  },
  {
    id: 'W15',
    severity: 'warning',
    title: 'Marca maior que o campo de visão',
    evaluate: (read) => {
      const footprint = returnFootprint(read);
      const tag = usableTag(read);
      if (footprint === null || tag === null || tag < footprint.width - EPS) return null;
      const minAltitude = (tag * Math.sin(rad(footprint.tilt))) / (2 * Math.tan(rad(footprint.fov) / 2));
      return {
        paths: [P.tag, P.altitude],
        message: `Marca de ${m2(tag)} não cabe no campo de visão a ${m2(read(P.altitude) ?? 0)} de altitude (visível: ${m2(footprint.width)}): a detecção exige a marca inteira no quadro.`,
        fix: `Suba a altitude para mais de ${m2(minAltitude)} ou use uma tag menor que ${m2(footprint.width)}.`,
      };
    },
  },
];

/**
 * The conflicts `doc` holds, errors first, then warnings, each level in the
 * catalogue's order. One record per rule.
 */
export function evaluateParameterConflicts(doc: unknown): ParameterConflict[] {
  const read: Read = (path) => {
    const value = getPath(doc, path);
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const fired = new Set<string>();
  const found: ParameterConflict[] = [];
  // W13 defers to W15, which the catalogue lists after it.
  const order = [...RULES].sort((a, b) => (a.id === 'W15' ? -1 : b.id === 'W15' ? 1 : 0));
  for (const rule of order) {
    const result = rule.evaluate(read, doc, fired);
    if (result === null) continue;
    fired.add(rule.id);
    found.push({ id: rule.id, severity: rule.severity, title: rule.title, ...result });
  }
  const rank = new Map(RULES.map((rule, index) => [rule.id, index]));
  return found.sort((a, b) => rank.get(a.id)! - rank.get(b.id)!);
}

/**
 * The conflicts that matter to the mission mode of `doc`: a conflict is kept
 * unless every parameter it names is inactive in that mode. A conflict that
 * names no path is always kept.
 */
export function activeConflicts(doc: unknown): ParameterConflict[] {
  const mode = readMissionMode(doc);
  return evaluateParameterConflicts(doc).filter(
    (conflict) => conflict.paths.length === 0 || conflict.paths.some((path) => isPathActive(mode, path, doc))
  );
}

/** The conflicts of `doc`, in its mission mode, that refuse a save and a launch. */
export function blockingConflicts(doc: unknown): ParameterConflict[] {
  return activeConflicts(doc).filter((conflict) => conflict.severity === 'error');
}
