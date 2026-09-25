import type { BmgTelemetry } from './bmg';

/** The station is organised as two tabs. Everything else opens over them. */
export type Screen = 'preflight' | 'cockpit';

/**
 * The five deterministic stages executed by `mvp_mission_bebop`. `step` matches
 * the integer emitted on `bmg:step-change`, which main.cjs derives from the
 * literal `[STEP N:` marker on the mission's stdout.
 */
export interface MissionStage {
  step: 1 | 2 | 3 | 4 | 5;
  name: string;
  /** What the airframe is doing, in the operator's words. */
  detail: string;
}

export const MISSION_STAGES: MissionStage[] = [
  { step: 1, name: 'Decolagem', detail: 'Referência de solo, armamento e subida ao teto operacional' },
  { step: 2, name: 'Varredura', detail: 'Deslocamento em linha reta com detecção YOLOv8 na câmera frontal' },
  { step: 3, name: 'Acidente detectado', detail: 'Aproximação até a câmera apontar para baixo, proteção contra subida indevida ativa' },
  { step: 4, name: 'Inspeção', detail: 'Pairado imóvel sobre o sinistro, captura em dupla fidelidade' },
  { step: 5, name: 'Retornando base', detail: 'Retorno à origem e pouso sobre o marcador ArUco' },
];

export type MissionState = 'idle' | 'arming' | 'running' | 'aborting' | 'finished' | 'faulted';

/** Link readiness, derived strictly from telemetry and driver probes. */
export interface ReadinessCheck {
  id: string;
  label: string;
  detail: string;
  state: 'pass' | 'warn' | 'fail';
}

export interface TrackPoint {
  /** Metres east of the launch origin. */
  x: number;
  /** Metres north of the launch origin. */
  y: number;
  alt: number;
  at: number;
}

export type TelemetrySource = 'hardware' | 'synthetic';

export interface TelemetryView extends BmgTelemetry {
  source: TelemetrySource;
  /** Seconds since the last hardware frame, or null if none has arrived. */
  ageSec: number | null;
}
