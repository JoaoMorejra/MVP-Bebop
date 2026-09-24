/**
 * The editable surface of `mvp_mission_bebop.parameters.MissionParameters`.
 *
 * Deliberately small. The operator changes the six figures that define how the
 * mission behaves — how high, how fast, how long it settles, where the camera
 * starts and where it stops, and how long each open-ended stage may run — and
 * nothing else. PID gains, jerk ceilings, dead reckoning and the vision
 * thresholds stay in `mission_config.json` untouched: the document round-trips
 * whole, so a save never resets a field this schema does not list.
 *
 * Every `path` here is a real field on one of the Python dataclasses, and the
 * mission reads it from the `--params-json` payload the station sends at launch.
 * `gimbal.nadir_tilt_deg` in particular is the exact angle at which the IBVS
 * controller (`controllers/visual_servoing.py`) raises `nadir_frozen`, zeroes
 * the horizontal velocity and hands over to the inspection hover.
 */

export type ParameterGroupId = 'envelope' | 'gimbal' | 'timeouts';

interface Common {
  path: string;
  label: string;
  hint: string;
}

export interface NumberParameter extends Common {
  kind: 'number';
  min: number;
  max: number;
  step: number;
  unit?: string;
  /** Decimal places used for display and for change detection. */
  precision: number;
  /** The value "Restaurar padrões" puts back. */
  defaultValue: number;
}

export type ParameterSpec = NumberParameter;

export interface ParameterGroup {
  id: ParameterGroupId;
  title: string;
  summary: string;
  items: ParameterSpec[];
}

export const PARAMETER_GROUPS: ParameterGroup[] = [
  {
    id: 'envelope',
    title: 'Envelope de Voo',
    summary: 'Altitude, cruzeiro e estabilização após a decolagem',
    items: [
      {
        kind: 'number',
        path: 'kinematics.target_altitude_m',
        label: 'Altitude de Voo',
        hint: 'Altitude de cruzeiro e teto de segurança estabilizado.',
        min: 0.5,
        max: 4,
        step: 0.1,
        unit: 'm',
        precision: 1,
        defaultValue: 1.8,
      },
      {
        kind: 'number',
        path: 'kinematics.forward_cruise_velocity',
        label: 'Velocidade de Cruzeiro',
        hint: 'Velocidade retilínea de busca na etapa de varredura.',
        min: 0.05,
        max: 0.6,
        step: 0.01,
        unit: 'm/s',
        precision: 2,
        defaultValue: 0.2,
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
        defaultValue: 2,
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
        label: 'Inclinação Inicial de Varredura',
        hint: 'Ângulo inicial da câmera durante a varredura para busca de alvos.',
        min: -45,
        max: 0,
        step: 1,
        unit: '°',
        precision: 0,
        defaultValue: -20,
      },
      {
        kind: 'number',
        path: 'gimbal.nadir_tilt_deg',
        label: 'Ângulo da Inclinação Nadir',
        hint: 'Inclinação final da câmera sobre o alvo. Ao atingir este ângulo, o drone congela o movimento horizontal para captura.',
        min: -90,
        max: -50,
        step: 1,
        unit: '°',
        precision: 0,
        defaultValue: -69,
      },
    ],
  },
  {
    id: 'timeouts',
    title: 'Limites de Tempo',
    summary: 'Quanto tempo a busca e o retorno podem durar antes do failsafe',
    items: [
      {
        kind: 'number',
        path: 'timeouts.search_timeout_sec',
        label: 'Timeout de Procura/Varredura',
        hint: 'Sem sinistro confirmado neste tempo, a varredura encerra e a aeronave retorna.',
        min: 5,
        max: 180,
        step: 1,
        unit: 's',
        precision: 0,
        defaultValue: 30,
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
        defaultValue: 60,
      },
    ],
  },
];

export const ALL_PARAMETERS: ParameterSpec[] = PARAMETER_GROUPS.flatMap((g) => g.items);

/**
 * Paths whose edits make the working document dirty.
 *
 * The six parameters, plus `no_fly`: the bench switch on the pre-flight screen
 * edits the same document, and a toggle that never counted as a change would
 * never be committed to disk before launch.
 */
export const TRACKED_PATHS: string[] = [...ALL_PARAMETERS.map((spec) => spec.path), 'no_fly'];
