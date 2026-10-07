import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertOctagon, RotateCcw } from 'lucide-react';
import { cn } from '../../lib/format';
import type { LandProgressPhase } from '../../types/bmg';
import { landProgressLabel } from '../../lib/flightState';

export interface AbortControlProps {
  onAbort: () => void;
  disabled?: boolean;
  busy?: boolean;
  landProgress?: LandProgressPhase | null;
}

/**
 * Zero the velocity, command a landing, and stop the mission process.
 *
 * One press, acted on immediately. In unconfirmed state (R6 / L2), the button
 * re-enables with "REENVIAR POUSO" to allow repeating the land command.
 */
export const AbortControl: React.FC<AbortControlProps> = ({
  onAbort,
  disabled,
  busy,
  landProgress,
}) => {
  const [flash, setFlash] = useState(false);
  const firedRef = useRef(false);
  const flashTimer = useRef<number | null>(null);

  const isUnconfirmed = landProgress === 'unconfirmed';
  const effectiveDisabled = isUnconfirmed ? false : Boolean(disabled || busy);

  const fire = useCallback(() => {
    if (effectiveDisabled || firedRef.current) return;
    firedRef.current = true;
    setFlash(true);
    onAbort();
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => {
      firedRef.current = false;
      setFlash(false);
    }, 600);
  }, [effectiveDisabled, onAbort]);

  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    []
  );

  let label = 'ABORTAR MISSÃO';
  if (isUnconfirmed) {
    label = 'REENVIAR POUSO';
  } else if (landProgress === 'landed') {
    label = 'POUSADA';
  } else if (landProgress === 'landing') {
    label = 'POUSANDO';
  } else if (landProgress === 'commanded' || busy) {
    label = 'POUSO COMANDADO';
  }

  const title = isUnconfirmed
    ? 'Pouso não confirmado: reenviar'
    : effectiveDisabled
    ? 'Disponível durante a missão'
    : 'Pousa a aeronave imediatamente';

  const progressText = landProgressLabel(landProgress);

  return (
    <div className="flex flex-col items-center gap-1">
      <button
        type="button"
        disabled={effectiveDisabled}
        onPointerDown={fire}
        onKeyDown={(e) => {
          if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            fire();
          }
        }}
        aria-label={isUnconfirmed ? 'Reenviar pouso imediatamente' : 'Abortar missão e pousar imediatamente'}
        title={title}
        className={cn(
          'group relative flex items-center gap-2.5 overflow-hidden rounded-full border-2 px-6 py-2.5',
          'transition-all duration-150 active:scale-[0.98]',
          isUnconfirmed
            ? 'border-amber-500 bg-amber-500/25 text-amber-200 shadow-abort backdrop-blur-sm hover:bg-amber-500/40 animate-pulse'
            : effectiveDisabled
            ? 'cursor-not-allowed border-strut bg-hull text-haze-deep'
            : 'border-ember bg-ember/20 text-frost shadow-abort backdrop-blur-sm hover:bg-ember/35',
          flash && 'bg-ember text-abyss'
        )}
      >
        {isUnconfirmed ? (
          <RotateCcw
            size={16}
            strokeWidth={2}
            className={cn('relative shrink-0 text-amber-400', flash && 'text-abyss')}
          />
        ) : (
          <AlertOctagon
            size={16}
            strokeWidth={2}
            className={cn(
              'relative shrink-0',
              effectiveDisabled ? 'text-haze-deep' : flash ? 'text-abyss' : 'text-ember'
            )}
          />
        )}
        <span className="relative font-cond text-sm font-semibold tracking-[0.12em]">
          {label}
        </span>
      </button>

      {progressText && (
        <span
          className={cn(
            'rounded-full px-2 py-0.5 font-mono text-2xs font-medium tracking-wider uppercase',
            isUnconfirmed
              ? 'border border-amber-500/60 bg-amber-950/90 text-amber-300'
              : 'border border-strut bg-hull/90 text-haze'
          )}
        >
          {progressText}
        </span>
      )}
    </div>
  );
};
