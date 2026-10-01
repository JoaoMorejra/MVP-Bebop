import React, { useState } from 'react';
import { Compass } from 'lucide-react';
import type { MagnetoCalibration } from '../../types/bmg';
import { cn } from '../../lib/format';
import { magnetoView, type AxisState } from '../../lib/magnetoCalibration';

interface MagnetoCalibrationPanelProps {
  /** Latest `states/magneto_calibration` report, or null before the aircraft sent one. */
  report: MagnetoCalibration | null;
  missionRunning: boolean;
  /** Start (true) or abort (false); resolves with the host's answer. */
  onCommand: (start: boolean) => Promise<{ success: boolean; error?: string }>;
}

const AXIS_STYLE: Record<AxisState, string> = {
  done: 'border-mint/50 bg-mint/10 text-mint',
  active: 'border-amber/60 bg-amber/10 text-amber anim-breathe',
  pending: 'border-[#1c3a47] text-haze',
};

const AXIS_TEXT: Record<AxisState, string> = { done: 'calibrado', active: 'girar agora', pending: 'pendente' };

/**
 * Magnetometer calibration, started by hand and read back from the aircraft.
 *
 * Never triggered automatically: the operator turns the powered-down aircraft
 * about each axis in turn, and every state shown is the one the aircraft
 * reported (`lib/magnetoCalibration.ts`).
 */
export const MagnetoCalibrationPanel: React.FC<MagnetoCalibrationPanelProps> = ({
  report,
  missionRunning,
  onCommand,
}) => {
  const view = magnetoView(report, missionRunning);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const send = async (start: boolean) => {
    setPending(true);
    setError(null);
    try {
      const result = await onCommand(start);
      if (!result.success) setError(result.error ?? 'comando recusado');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'comando recusado');
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="flex flex-col gap-2 rounded-bezel border border-[#0f2a36] bg-[#061219] p-3 font-mono text-2xs text-frost/80">
      <header className="flex items-center gap-2 text-frost">
        <Compass size={13} />
        <span className="font-cond text-sm font-semibold tracking-wide">Calibração do magnetômetro</span>
      </header>
      <div className="grid grid-cols-3 gap-1.5">
        {(['x', 'y', 'z'] as const).map((axis) => (
          <div
            key={axis}
            data-axis={axis}
            data-state={view.axes[axis]}
            className={cn('rounded border px-2 py-1 text-center', AXIS_STYLE[view.axes[axis]])}
          >
            <div className="text-xs font-semibold uppercase">{axis}</div>
            <div className="text-3xs">{AXIS_TEXT[view.axes[axis]]}</div>
          </div>
        ))}
      </div>
      <p className={cn('leading-relaxed', view.phase === 'failed' ? 'text-amber' : 'text-haze')}>{view.instruction}</p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={!view.canStart || pending}
          onClick={() => void send(true)}
          className="rounded border border-mint/45 px-2.5 py-1 text-mint transition-colors hover:bg-mint/10 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Iniciar
        </button>
        <button
          type="button"
          disabled={!view.canAbort || pending}
          onClick={() => void send(false)}
          className="rounded border border-[#1c3a47] px-2.5 py-1 text-haze transition-colors hover:text-frost disabled:cursor-not-allowed disabled:opacity-40"
        >
          Cancelar
        </button>
      </div>
      {missionRunning ? <p className="text-haze-deep">Indisponível com a missão em execução.</p> : null}
      {error ? <p className="text-amber">{error}</p> : null}
    </section>
  );
};
