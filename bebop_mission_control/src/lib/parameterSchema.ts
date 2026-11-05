/**
 * The editable surface of `mvp_mission_bebop.parameters.MissionParameters`.
 *
 * Deliberately small. The operator changes the figures that define how the
 * mission behaves — how high, how fast, how long it settles, where the camera
 * starts and where it stops, how long each open-ended stage may run and how
 * long the launch counts down — and nothing else. Defaults are not held here:
 * they are `MissionParameters.factory()`, served by `mission.py --dump-defaults`. PID gains, jerk ceilings, dead reckoning and the vision
 * thresholds stay in `mission_config.json` untouched: the document round-trips
 * whole, so a save never resets a field this schema does not list.
 *
 * Every `path` here is a real field on one of the Python dataclasses, and the
 * mission reads it from the `--params-json` payload the station sends at launch.
 * `gimbal.nadir_tilt_deg` in particular is the exact angle at which the IBVS
 * controller (`controllers/visual_servoing.py`) raises `nadir_frozen`, zeroes
 * the horizontal velocity and hands over to the inspection hover.
 *
 * The slider ranges are what the operator may set, not what the aircraft can
 * fly: a combination of in-range values can still be inexecutable (a ceiling
 * below the firmware takeoff height, a nadir above the search angle), and
 * `parameterConflicts.ts` refuses those at save time. One slider may write
 * more than its own path: `mirrors` lists the fields it drives with it.
 *
 * A field or a card may belong to some mission modes only (`modes`), and its
 * text may change by mode (`hints`, `summaries`): {@link groupsForMode} builds
 * the sheet of one mode. A field hidden in a mode keeps its stored value.
 */
import {
  ALTITUDE_PATH,
  RTL_ENABLED_PATH,
  TAKEOFF_FROM_GROUND_PATH,
  altitudeSliderRange,
  ceilingFloorFrame,
  marginUnlocked,
  type AltitudeRange,
} from './altitudeEnvelope';
import { MISSION_MODE_PATH, inactivePaths, type MissionMode } from './missionMode';

export type ParameterGroupId = 'envelope' | 'gimbal' | 'timeouts';

/**
 * `vision.victim_detection_enabled`: off, a person is drawn as `Person` and
 * every inspection settles as `not_detected` (`perception/assessment.py:disabled`).
 */
export const VICTIM_DETECTION_PATH = 'vision.victim_detection_enabled';

interface Common {
  path: string;
  label: string;
  hint: string;
  /** Modes in which the field is shown and checked. Absent means every mode. */
  modes?: ReadonlyArray<MissionMode>;
  /** Replaces `hint` in the modes named. */
  hints?: Partial<Record<MissionMode, string>>;
  /**
   * Why the field cannot be edited with the rest of `doc`, shown in its place
   * of a hint; `null` when it can. A field that has one stays on the sheet
   * while the flight does not read it (`missionMode.ts:inactivePaths`).
   */
  disabledReason?: (doc: unknown) => string | null;
}

export interface NumberParameter extends Common {
  kind: 'number';
  min: number;
  max: number;
  step: number;
  unit?: string;
  /** Replaces `min` and `max` with a range that depends on the rest of the document. */
  rangeFor?: (doc: unknown) => AltitudeRange;
  /** Decimal places used for display and for change detection. */
  precision: number;
  /** Paths written together with `path`, each as `sign * value`. */
  mirrors?: ReadonlyArray<{ path: string; sign: 1 | -1 }>;
}

/** A yes/no field, edited with a checkbox. Its value is always a JSON boolean. */
export interface BooleanParameter extends Common {
  kind: 'boolean';
}

export type ParameterSpec = NumberParameter | BooleanParameter;

export interface ParameterGroup {
  id: ParameterGroupId;
  title: string;
  summary: string;
  /** Replaces `summary` in the modes named. */
  summaries?: Partial<Record<MissionMode, string>>;
  items: ParameterSpec[];
}

/**
 * Why the ceiling margin is locked: on the pad top at or below
 * `TOTEM_MARGIN_UNLOCK_ABOVE_M` the mission ignores it and flies a fixed
 * ceiling (D2, `estimation/ceiling_schedule.py`), stated over the floor.
 */
function ceilingMarginLock(doc: unknown): string | null {
  if (marginUnlocked(doc)) return null;
  const ceiling = ceilingFloorFrame(doc);
  const figure = ceiling === null ? '' : `: ${ceiling.toFixed(2).replace('.', ',')} m sobre o piso`;
  return `Teto fixo do perfil de totem${figure}`;
}

export const PARAMETER_GROUPS: ParameterGroup[] = [
  {
    id: 'envelope',
    title: 'Envelope de Voo',
    summary: 'Altitude, velocidade de deslocamento e estabilização após a decolagem',
    summaries: { simple: 'Altitude e estabilização após a decolagem' },
    items: [
      {
        kind: 'number',
        path: 'kinematics.target_altitude_m',
        label: 'Altitude de Voo',
        hint: 'Cota sobre o piso. Decolando do totem, o mínimo é a altura do totem mais 0,30 m.',
        min: 0.1,
        max: 3,
        rangeFor: altitudeSliderRange,
        step: 0.05,
        unit: 'm',
        precision: 2,
      },
      {
        kind: 'boolean',
        path: TAKEOFF_FROM_GROUND_PATH,
        label: 'Decolagem do Solo',
        hint: 'Desmarcada: decola do topo do totem de 1,0 m. Marcada: decola do piso e libera a margem de teto.',
      },
      {
        kind: 'number',
        path: 'kinematics.altitude_ceiling_margin_m',
        label: 'Margem do Teto de Altitude',
        hint: 'Tolerância acima da altitude alvo antes de acionar pouso de emergência por segurança.',
        disabledReason: ceilingMarginLock,
        min: 0,
        max: 2,
        step: 0.05,
        unit: 'm',
        precision: 2,
      },
      {
        kind: 'number',
        path: 'kinematics.forward_cruise_velocity',
        label: 'Velocidade de Deslocamento',
        hint: 'Velocidade lateral única do modo avançado: varredura e aproximação para a esquerda e retorno do RTL para a direita, sempre com a mesma magnitude.',
        min: 0.01,
        max: 0.6,
        step: 0.01,
        unit: 'm/s',
        precision: 2,
        mirrors: [{ path: 'rtl.reverse_cruise_velocity', sign: -1 }],
        modes: ['advanced'],
      },
      {
        kind: 'number',
        path: 'kinematics.takeoff_stabilize_duration_sec',
        label: 'Estabilização Pós-Decolagem',
        hint: 'Tempo de pairado no ponto de decolagem antes de iniciar o avanço.',
        min: 1,
        max: 15,
        step: 0.5,
        unit: 's',
        precision: 1,
      },
      {
        kind: 'number',
        path: 'yaw_pattern.scan_vyaw',
        label: 'Velocidade de Giro (Yaw)',
        hint: 'Taxa de giro da varredura (yaw) no modo simples, normalizada (0,15 equivale a cerca de 15 graus por segundo).',
        min: 0.05,
        max: 0.3,
        step: 0.01,
        precision: 2,
        modes: ['simple'],
      },
    ],
  },
  {
    id: 'gimbal',
    title: 'Gimbal & Câmera',
    summary: 'Onde a câmera começa a varredura e onde ela congela sobre o alvo',
    items: [
      {
        kind: 'number',
        path: 'gimbal.search_tilt_deg',
        label: 'Inclinação Inicial de Varredura (Gimbal)',
        hint: 'Ângulo inicial de inclinação (tilt) do gimbal durante a varredura para busca de alvos.',
        min: -45,
        max: 0,
        step: 1,
        unit: '°',
        precision: 0,
      },
      {
        kind: 'number',
        path: 'gimbal.nadir_tilt_deg',
        label: 'Ângulo da Câmera Apontada para Baixo (Nadir)',
        hint: 'Inclinação final do gimbal sobre o alvo. Ao atingir este ângulo nadir, o drone estabiliza o movimento horizontal para captura.',
        min: -80,
        max: 0,
        step: 1,
        unit: '°',
        precision: 0,
      },
      {
        kind: 'boolean',
        path: VICTIM_DETECTION_PATH,
        label: 'Detecção de Vítima',
        hint: 'Marcada: pessoas aparecem como Vitima e a inspeção avalia se há vítima. Desmarcada: aparecem como Person e o laudo sempre registra cena sem vítimas aparentes.',
      },
    ],
  },
  {
    id: 'timeouts',
    title: 'Limites de Tempo',
    summary: 'Quanto tempo a busca e o retorno podem durar',
    summaries: {
      simple: 'Quanto tempo a busca por giro pode durar',
    },
    items: [
      {
        kind: 'number',
        path: 'timeouts.search_timeout_sec',
        label: 'Timeout de Procura/Varredura',
        hint: 'Sem ocorrência confirmada neste tempo, a varredura encerra e a aeronave retorna.',
        hints: {
          simple: 'Tempo máximo girando em busca da ocorrência. Sem alvo confirmado neste tempo, a aeronave fotografa a referência e pousa.',
        },
        min: 5,
        max: 180,
        step: 1,
        unit: 's',
        precision: 0,
      },
      {
        kind: 'boolean',
        path: RTL_ENABLED_PATH,
        label: 'Retorno a base (RTL)',
        hint: 'Marcada: retorna, centraliza no ArUco e pousa na base. Desmarcada: pousa no local logo após a foto.',
        hints: {
          simple:
            'Marcada: retorna, centraliza no ArUco e pousa na base. Desmarcada: pousa no local logo após a foto. O rumo volta ao da decolagem antes do pouso.',
        },
      },
      {
        kind: 'number',
        path: 'rtl.timeout_sec',
        label: 'Timeout de Retorno e Pouso ArUco',
        hint: 'Sem chegar ao marcador neste tempo, o pouso ocorre onde a aeronave estiver.',
        min: 10,
        max: 180,
        step: 1,
        unit: 's',
        precision: 0,
        modes: ['advanced'],
      },
    ],
  },
];

export { ALTITUDE_PATH };

function resolveItem(item: ParameterSpec, doc: unknown): ParameterSpec {
  if (item.kind !== 'number') return item;
  const range = item.rangeFor?.(doc);
  return { ...item, min: range?.min ?? item.min, max: range?.max ?? item.max };
}

export const ALL_PARAMETERS: ParameterSpec[] = PARAMETER_GROUPS.flatMap((g) => g.items);

/** The sheet's numeric fields, edited with a slider. */
export const NUMBER_PARAMETERS: NumberParameter[] = ALL_PARAMETERS.filter(
  (spec): spec is NumberParameter => spec.kind === 'number'
);

/** The sheet's yes/no fields, edited with a checkbox. */
export const BOOLEAN_PARAMETERS: BooleanParameter[] = ALL_PARAMETERS.filter(
  (spec): spec is BooleanParameter => spec.kind === 'boolean'
);

/**
 * The cards of the sheet for `mode`: items the mode does not use removed, texts resolved, empty cards dropped.
 * The altitude range follows the launch surface of `doc` (`altitudeEnvelope.ts`; the floor without a document),
 * and a field the plan of `doc` does not read (`missionMode.ts:inactivePaths`, the return fields without the
 * return) is left out, unless it has a `disabledReason`: it then stays on its card, locked.
 */
export function groupsForMode(mode: MissionMode, doc?: unknown): ParameterGroup[] {
  const inactive = inactivePaths(mode, doc);
  return PARAMETER_GROUPS.map((group) => ({
    ...group,
    summary: group.summaries?.[mode] ?? group.summary,
    items: group.items
      .filter((item) => !item.modes || item.modes.includes(mode))
      .filter((item) => !inactive.has(item.path) || item.disabledReason !== undefined)
      .map((item) => ({ ...resolveItem(item, doc), hint: item.hints?.[mode] ?? item.hint })),
  })).filter((group) => group.items.length > 0);
}

/**
 * Precision Land: the marker-tracked descent from the centring height down to
 * the commit height (`rtl.visual_descent_enabled`, `controllers/precision_descent.py`).
 * Off, the aircraft still centres over the marker at the altitude it holds and
 * the firmware landing descends from there.
 */
export const PRECISION_LAND_PATH = 'rtl.visual_descent_enabled';

/**
 * Stage 5 fields edited in "Configurações Avançadas".
 *
 * The pad marker (`steps/rtl.py` builds its detector from the first three),
 * the window of the centering over it (`rtl.centering_timeout_sec`, separate
 * from `rtl.timeout_sec`, which bounds the search) and the Precision Land
 * switch. Kept out of {@link PARAMETER_GROUPS}, so the operator preset does
 * not move them.
 */
export const ADVANCED_PATHS = [
  'rtl.target_aruco_id',
  'rtl.marker_dict',
  'rtl.tag_size',
  'rtl.centering_timeout_sec',
  PRECISION_LAND_PATH,
] as const;

/**
 * Range of `rtl.centering_timeout_sec` the sheet accepts, seconds. Below one
 * second the closed-loop centering cannot settle a single cycle window before
 * it expires; 120 s matches the longest flight window of the sheet's other
 * timeouts short of `tracking_timeout_sec`.
 */
export const CENTERING_TIMEOUT_RANGE_SEC = { min: 1, max: 120 } as const;

/**
 * Predefined dictionaries the sheet offers, with their marker count.
 *
 * Every name is an attribute of `cv2.aruco`, so the Nectar SDK's
 * `resolve_aruco_dict` takes its exact-attribute branch. Counts measured with
 * cv2 4.10.0 (`getPredefinedDictionary(...).bytesList.shape[0]`); the list is
 * mirrored in `test/fixtures/marker_dictionaries.json`, which pytest checks
 * against OpenCV and vitest against this constant.
 */
export const MARKER_DICTIONARIES: ReadonlyArray<{ name: string; markers: number }> = [
  { name: 'DICT_4X4_50', markers: 50 },
  { name: 'DICT_4X4_100', markers: 100 },
  { name: 'DICT_4X4_250', markers: 250 },
  { name: 'DICT_4X4_1000', markers: 1000 },
  { name: 'DICT_5X5_50', markers: 50 },
  { name: 'DICT_5X5_100', markers: 100 },
  { name: 'DICT_5X5_250', markers: 250 },
  { name: 'DICT_5X5_1000', markers: 1000 },
  { name: 'DICT_6X6_50', markers: 50 },
  { name: 'DICT_6X6_100', markers: 100 },
  { name: 'DICT_6X6_250', markers: 250 },
  { name: 'DICT_6X6_1000', markers: 1000 },
  { name: 'DICT_7X7_50', markers: 50 },
  { name: 'DICT_7X7_1000', markers: 1000 },
  { name: 'DICT_ARUCO_ORIGINAL', markers: 1024 },
  { name: 'DICT_APRILTAG_16h5', markers: 30 },
  { name: 'DICT_APRILTAG_25h9', markers: 35 },
  { name: 'DICT_APRILTAG_36h10', markers: 2320 },
  { name: 'DICT_APRILTAG_36h11', markers: 587 },
];

/** Whether `value` names one of {@link MARKER_DICTIONARIES}. */
export function isKnownMarkerDictionary(value: unknown): value is string {
  return typeof value === 'string' && MARKER_DICTIONARIES.some((entry) => entry.name === value);
}

/** Marker count of the dictionary `value` names, or `null` when the sheet cannot tell. */
export function markerCount(value: unknown): number | null {
  return MARKER_DICTIONARIES.find((entry) => entry.name === value)?.markers ?? null;
}

/**
 * Paths whose edits make the working document dirty.
 *
 * The sheet parameters, plus `no_fly`: the bench switch on the pre-flight screen
 * edits the same document, and a toggle that never counted as a change would
 * never be committed to disk before launch. The advanced pad fields count too,
 * and so does the mission mode the selector writes.
 */
export const TRACKED_PATHS: string[] = [
  ...ALL_PARAMETERS.map((spec) => spec.path),
  'no_fly',
  ...ADVANCED_PATHS,
  MISSION_MODE_PATH,
];
