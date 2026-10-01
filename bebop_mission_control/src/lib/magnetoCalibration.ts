/**
 * The magnetometer calibration as the aircraft reports it (6.4).
 *
 * Everything shown comes from `states/magneto_calibration`: the station never
 * marks an axis done or the calibration complete on its own account, and the
 * start control is offered only when no mission process exists.
 */
import type { MagnetoCalibration } from '../types/bmg';

export type MagnetoPhase = 'unknown' | 'idle' | 'running' | 'done' | 'failed';
export type AxisState = 'done' | 'active' | 'pending';
type Axis = 'x' | 'y' | 'z';

export interface MagnetoView {
  phase: MagnetoPhase;
  /** The aircraft asks for a calibration; `null` before it has said. */
  required: boolean | null;
  axes: Record<Axis, AxisState>;
  instruction: string;
  canStart: boolean;
  canAbort: boolean;
}

const AXIS_LABEL: Record<Axis, string> = { x: 'X', y: 'Y', z: 'Z' };

export function magnetoView(report: MagnetoCalibration | null, missionRunning: boolean): MagnetoView {
  if (!report) {
    return {
      phase: 'unknown',
      required: null,
      axes: { x: 'pending', y: 'pending', z: 'pending' },
      instruction: 'Aguardando o estado de calibração da aeronave.',
      canStart: false,
      canAbort: false,
    };
  }
  const running = report.started === 1;
  const active = running && report.axis && report.axis !== 'none' ? report.axis : null;
  const axisState = (axis: Axis): AxisState => {
    if (report[axis] === 1) return 'done';
    return axis === active ? 'active' : 'pending';
  };
  const axes = { x: axisState('x'), y: axisState('y'), z: axisState('z') };

  let phase: MagnetoPhase;
  let instruction: string;
  if (report.failed === 1) {
    phase = 'failed';
    instruction = 'A aeronave relatou falha na calibração. Inicie novamente.';
  } else if (running) {
    phase = 'running';
    instruction = active
      ? `Gire a aeronave em torno do eixo ${AXIS_LABEL[active]}, motores desligados.`
      : 'Calibração em andamento. Siga a aeronave.';
  } else if (axes.x === 'done' && axes.y === 'done' && axes.z === 'done') {
    phase = 'done';
    instruction = 'Calibração confirmada pela aeronave.';
  } else {
    phase = 'idle';
    instruction =
      report.required === 1 ? 'A aeronave pede calibração do magnetômetro.' : 'Calibração não solicitada pela aeronave.';
  }

  return {
    phase,
    required: report.required === 1 ? true : report.required === 0 ? false : null,
    axes,
    instruction,
    canStart: !missionRunning && phase !== 'running',
    canAbort: phase === 'running',
  };
}
