import React, { useCallback, useEffect, useRef } from 'react';
import { AlertOctagon, RotateCcw } from 'lucide-react';
import { cn } from '../../lib/format';
import type { LandProgressPhase } from '../../types/bmg';
import { landProgressLabel } from '../../lib/flightState';

export interface AbortControlProps {
  onAbort: () => void;
  disabled?: boolean;
  busy?: boolean;
  landProgress?: LandProgressPhase | null;
  /** Fill the parent, label centred: the pre-flight dock, where it is the two pills fused. */
  fill?: boolean;
}

/**
 * Zero the velocity, command a landing, and stop the mission process.
 *
 * One press, acted on immediately, and shown as nothing: no flash, no press
 * scale, no change of wording while the aircraft lands. The abort is the
 * operator's own business; the audience watching the station sees the flight
 * come to an end, not an abort. The one exception is a landing the aircraft
 * never confirmed (R6 / L2): the button re-enables as "REENVIAR POUSO", still
 * and steady, so the land command can be repeated.
 */
export const AbortControl: React.FC<AbortControlProps> = ({
  onAbort,
  disabled,
  busy,
  landProgress,
  fill = false,
}) => {
  const firedRef = useRef(false);
  const releaseTimer = useRef<number | null>(null);

  const isUnconfirmed = landProgress === 'unconfirmed';
  const effectiveDisabled = isUnconfirmed ? false : Boolean(disabled || busy);
  // While the abort it started is landing the aircraft the button refuses a
  // second press but keeps its look: greying it out on the press would show
  // the abort. It greys only once it is unavailable for another reason (on
  // the ground, after the flight), exactly as at the end of a normal flight.
  const looksDisabled = effectiveDisabled && !busy;

  const fire = useCallback(() => {
    if (effectiveDisabled || firedRef.current) return;
    firedRef.current = true;
    onAbort();
    if (releaseTimer.current !== null) window.clearTimeout(releaseTimer.current);
    releaseTimer.current = window.setTimeout(() => {
      firedRef.current = false;
    }, 600);
  }, [effectiveDisabled, onAbort]);

  useEffect(
    () => () => {
      if (releaseTimer.current !== null) window.clearTimeout(releaseTimer.current);
    },
    []
  );

  const label = isUnconfirmed ? 'REENVIAR POUSO' : 'ABORTAR MISSÃO';
  const progressText = isUnconfirmed ? landProgressLabel(landProgress) : null;

  return (
    <div className={cn('flex flex-col items-center gap-1', fill && 'h-full w-full')}>
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
        className={cn(
          'group relative flex items-center gap-2.5 overflow-hidden rounded-full border-2 px-6 py-2.5',
          fill && 'h-full w-full justify-center',
          isUnconfirmed
            ? 'border-amber-500 bg-amber-500/25 text-amber-200 shadow-abort backdrop-blur-sm'
            : looksDisabled
            ? 'cursor-not-allowed border-strut bg-hull text-haze-deep'
            : 'border-ember bg-ember/20 text-frost shadow-abort backdrop-blur-sm'
        )}
      >
        {isUnconfirmed ? (
          <RotateCcw
            size={16}
            strokeWidth={2}
            className="relative shrink-0 text-amber-400"
          />
        ) : (
          <AlertOctagon
            size={16}
            strokeWidth={2}
            className={cn(
              'relative shrink-0',
              looksDisabled ? 'text-haze-deep' : 'text-ember'
            )}
          />
        )}
        <span className="title-pixel relative font-pixel text-[13px] font-semibold tracking-[0.08em]">
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
