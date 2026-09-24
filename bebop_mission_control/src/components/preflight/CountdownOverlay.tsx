import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import { Wordmark } from '../brand/Wordmark';
import { cn } from '../../lib/format';

interface CountdownOverlayProps {
  seconds: number;
  /** The mission process has reported it reached stage 1. */
  stageReached: boolean;
  /** Every essential topic is exchanging data with the airframe. */
  linkReady: boolean;
  onDone: () => void;
  onCancel: () => void;
}

/** The clearance call the mission makes near the end of its countdown. */
const CLEARANCE_AT_SEC = 3.2;

/**
 * The pre-arm sequence, in the order `TakeoffStep` actually runs it: gimbal to
 * the search attitude, IMU flat trim, ground reference from the odometry, then
 * the countdown window itself, which the mission spends warming the YOLO
 * pipeline so the first search cycle runs at full cadence.
 */
const SEQUENCE = [
  { id: 'link', label: 'Enlace com a aeronave verificado' },
  { id: 'gimbal', label: 'Gimbal na atitude de varredura' },
  { id: 'imu', label: 'Nivelamento da IMU em superfície plana' },
  { id: 'ground', label: 'Referência de solo pela odometria' },
  { id: 'vision', label: 'Sincronizando sensores e aquecendo o YOLO' },
  { id: 'clearance', label: 'Decolagem autorizada' },
] as const;

/**
 * The transition between the hub and the cockpit.
 *
 * It exists to give the operator a window in which stopping costs nothing. The
 * numeral is the whole screen because at this moment there is exactly one
 * decision left, and the cancel control sits under it at full size rather than
 * tucked in a corner — the one thing that must never be hunted for.
 */
export const CountdownOverlay: React.FC<CountdownOverlayProps> = ({
  seconds,
  stageReached,
  linkReady,
  onDone,
  onCancel,
}) => {
  const total = Math.max(1, seconds);
  const [remaining, setRemaining] = useState(total);
  const startedAt = useRef(Date.now());
  const done = useRef(false);

  useEffect(() => {
    startedAt.current = Date.now();
    done.current = false;
    setRemaining(total);

    const id = window.setInterval(() => {
      const elapsed = (Date.now() - startedAt.current) / 1000;
      const left = Math.max(0, total - elapsed);
      setRemaining(left);
      if (left <= 0 && !done.current) {
        done.current = true;
        window.clearInterval(id);
        onDone();
      }
    }, 50);

    return () => window.clearInterval(id);
  }, [total, onDone]);

  // Escape is the fastest possible path out of a launch already in motion.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const elapsed = total - remaining;
  const progress = Math.max(0, Math.min(1, elapsed / total));
  const whole = Math.ceil(remaining - 0.001);

  const RADIUS = 132;
  const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

  const status = useMemo(() => {
    // The first three run before the countdown window opens, so reaching stage
    // 1 is what confirms them. The warmup owns the window; clearance is called
    // by the mission at T-3.2 s.
    const preflightDone = stageReached || elapsed > 0.6;
    return SEQUENCE.map((item) => {
      // The link is the one item with a live answer rather than a schedule:
      // it reports the topic contract the main process is already validating.
      if (item.id === 'link') {
        return { ...item, state: linkReady ? 'done' : 'active' } as const;
      }
      if (item.id === 'clearance') {
        return { ...item, state: remaining <= CLEARANCE_AT_SEC ? 'done' : 'pending' } as const;
      }
      if (item.id === 'vision') {
        return {
          ...item,
          state: remaining <= CLEARANCE_AT_SEC ? 'done' : preflightDone ? 'active' : 'pending',
        } as const;
      }
      return { ...item, state: preflightDone ? 'done' : 'active' } as const;
    });
  }, [stageReached, linkReady, elapsed, remaining]);

  return (
    <div
      className="brand-field fixed inset-0 z-50 flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-label="Contagem regressiva para a decolagem"
    >
      <div className="flex items-center justify-between px-7 py-5">
        <Wordmark height={22} className="opacity-70" />
        <span className="font-cond text-sm tracking-wide text-haze">
          Sequência de decolagem
        </span>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-center gap-20 px-10 pb-8">
        <div className="relative shrink-0">
          <svg width={300} height={300} viewBox="0 0 300 300" aria-hidden>
            <circle
              cx="150"
              cy="150"
              r={RADIUS}
              fill="none"
              stroke="#14455D"
              strokeWidth="2"
            />
            <circle
              cx="150"
              cy="150"
              r={RADIUS}
              fill="none"
              stroke={remaining <= CLEARANCE_AT_SEC ? '#5CF2CE' : '#01D5A3'}
              strokeWidth="3"
              strokeLinecap="round"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={CIRCUMFERENCE * progress}
              transform="rotate(-90 150 150)"
              style={{ transition: 'stroke-dashoffset 60ms linear, stroke 300ms' }}
            />
            {/* Second ticks, so the ring reads as a clock and not a loader. */}
            {Array.from({ length: total }, (_, i) => {
              const angle = (i / total) * 2 * Math.PI - Math.PI / 2;
              const inner = RADIUS - 9;
              return (
                <line
                  key={i}
                  x1={150 + Math.cos(angle) * inner}
                  y1={150 + Math.sin(angle) * inner}
                  x2={150 + Math.cos(angle) * (RADIUS - 2)}
                  y2={150 + Math.sin(angle) * (RADIUS - 2)}
                  stroke={i < Math.floor(elapsed) ? '#01D5A3' : '#14455D'}
                  strokeWidth="1.5"
                />
              );
            })}
          </svg>

          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span
              key={whole}
              className="anim-tick tnum font-mono text-6xl font-medium leading-none text-frost"
            >
              {whole}
            </span>
            <span className="mt-3 font-cond text-sm tracking-wide text-haze">
              {remaining <= CLEARANCE_AT_SEC ? 'decolagem autorizada' : 'segundos para decolar'}
            </span>
          </div>
        </div>

        <div className="flex max-w-[420px] flex-col gap-7">
          <div>
            <h1 className="text-2xl font-semibold leading-tight text-frost">
              A aeronave está se preparando para decolar
            </h1>
            <p className="mt-2 max-w-[46ch] text-sm leading-relaxed text-haze">
              Afaste-se do raio das hélices. Cancelar agora interrompe o processo antes que
              os motores sejam armados.
            </p>
          </div>

          <ul className="flex flex-col gap-2.5">
            {status.map((item) => (
              <li key={item.id} className="flex items-center gap-3">
                <span
                  className={cn(
                    'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-300',
                    item.state === 'done' && 'border-mint bg-mint/15 text-mint',
                    item.state === 'active' && 'border-mint/50 text-mint',
                    item.state === 'pending' && 'border-strut text-haze-deep'
                  )}
                >
                  {item.state === 'done' ? (
                    <Check size={11} strokeWidth={3} />
                  ) : item.state === 'active' ? (
                    <Loader2 size={11} strokeWidth={2.5} className="animate-spin" />
                  ) : (
                    <span className="h-1 w-1 rounded-full bg-current" />
                  )}
                </span>
                <span
                  className={cn(
                    'text-sm transition-colors duration-300',
                    item.state === 'pending' ? 'text-haze-deep' : 'text-frost'
                  )}
                >
                  {item.label}
                </span>
              </li>
            ))}
          </ul>

          <div>
            <div className="flex items-baseline justify-between">
              <span className="font-cond text-2xs tracking-wide text-haze-deep">
                sequência de calibração
              </span>
              <span className="tnum font-mono text-2xs text-mint">
                {Math.round(progress * 100)}%
              </span>
            </div>
            <div className="mt-1.5 h-[3px] w-full overflow-hidden rounded-full bg-frost/12">
              <div
                className="h-full rounded-full bg-mint transition-[width] duration-100 ease-linear"
                style={{ width: `${progress * 100}%` }}
              />
            </div>
          </div>

          <button
            type="button"
            onClick={onCancel}
            className={cn(
              'flex h-14 w-full items-center justify-center gap-3 rounded-bezel border-2',
              'border-ember/70 bg-ember/10 text-base font-semibold text-ember',
              'transition-all duration-150 hover:bg-ember hover:text-abyss'
            )}
          >
            <X size={18} strokeWidth={2.5} />
            Cancelar decolagem
            <span className="font-mono text-2xs font-normal opacity-70">Esc</span>
          </button>
        </div>
      </div>
    </div>
  );
};
