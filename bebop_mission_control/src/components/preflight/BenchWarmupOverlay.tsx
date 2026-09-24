import React, { useEffect, useRef, useState } from 'react';
import { FlaskConical, Loader2 } from 'lucide-react';
import { Wordmark } from '../brand/Wordmark';

/**
 * How long a bench launch holds the cockpit back.
 *
 * The mission process spends its first seconds importing the SDK, loading the
 * YOLO weights and opening the camera subscription. Handing the operator the
 * cockpit at once showed a feed with no video and a detector not yet in the
 * picture, which read as a broken rehearsal rather than a starting one.
 */
export const BENCH_WARMUP_MS = 10_000;

/** Progress refresh period. Visual only; completion is its own timer. */
const TICK_MS = 100;

interface BenchWarmupOverlayProps {
  /** Called once, when the warm-up window has elapsed. */
  onDone: () => void;
  durationMs?: number;
}

/**
 * The bench counterpart of the flight countdown, and deliberately not a
 * variant of it.
 *
 * The countdown is a safety window — the last chance to stop before the
 * motors arm, with a cancel control at full size. A bench run has no motors to
 * arm and nothing to stand clear of, so this is only a loading screen: no
 * cancel, no clearance call, no pre-arm checklist that would claim steps the
 * bench does not take.
 */
export const BenchWarmupOverlay: React.FC<BenchWarmupOverlayProps> = ({
  onDone,
  durationMs = BENCH_WARMUP_MS,
}) => {
  const [elapsedMs, setElapsedMs] = useState(0);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    const startedAt = Date.now();
    const tick = window.setInterval(() => {
      setElapsedMs(Math.min(durationMs, Date.now() - startedAt));
    }, TICK_MS);
    const done = window.setTimeout(() => {
      window.clearInterval(tick);
      onDoneRef.current();
    }, durationMs);
    return () => {
      window.clearInterval(tick);
      window.clearTimeout(done);
    };
  }, [durationMs]);

  const progress = durationMs > 0 ? elapsedMs / durationMs : 1;
  const percent = Math.round(progress * 100);
  const remaining = Math.max(0, Math.ceil((durationMs - elapsedMs) / 1000));

  return (
    <div
      className="brand-field fixed inset-0 z-50 flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label="Preparando a missão de bancada"
    >
      <div className="flex items-center justify-between px-7 py-5">
        <Wordmark height={22} className="opacity-70" />
        <span className="flex items-center gap-2 font-cond text-sm tracking-wide text-haze">
          <FlaskConical size={14} strokeWidth={2} />
          Bancada · motores inertes
        </span>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center px-10 pb-8">
        <div className="flex w-full max-w-[460px] flex-col gap-6">
          <Loader2 size={34} strokeWidth={2} className="animate-spin text-mint" aria-hidden />
          <div>
            <h1 className="text-2xl font-semibold leading-tight text-frost">
              Carregando pipeline de vídeo e pesos YOLO...
            </h1>
            <p className="mt-2 max-w-[46ch] text-sm leading-relaxed text-haze">
              O processo da missão está inicializando o detector e a câmera. A cabine abre
              assim que o carregamento terminar.
            </p>
          </div>

          <div>
            <div className="flex items-baseline justify-between">
              <span className="font-cond text-2xs tracking-wide text-haze-deep">
                inicialização · {remaining}s
              </span>
              <span className="tnum font-mono text-2xs text-mint">{percent}%</span>
            </div>
            <div
              className="mt-1.5 h-[3px] w-full overflow-hidden rounded-full bg-frost/12"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <div
                className="h-full rounded-full bg-mint transition-[width] duration-100 ease-linear"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
