import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertOctagon } from 'lucide-react';
import { cn } from '../../lib/format';

interface AbortControlProps {
  onAbort: () => void;
  disabled?: boolean;
  busy?: boolean;
}

/**
 * Zero the velocity, command a landing, and stop the mission process.
 *
 * One press, acted on immediately. This used to require a three-quarter-second
 * hold, on the reasoning that a click is easy to land on by accident mid-flight
 * — but the operator asked for it to fire without hesitation, and they are
 * right about which failure costs more. An abort pressed by mistake lands an
 * aircraft that was going to land anyway; an abort that waits is an aircraft
 * still flying at whatever made the operator reach for it. The press fires on
 * pointer-down rather than on click, so the command leaves before the button is
 * even released.
 *
 * It straddles the boundary between the video and the map on purpose: it
 * belongs to neither panel and must be findable without reading anything.
 */
export const AbortControl: React.FC<AbortControlProps> = ({ onAbort, disabled, busy }) => {
  const [flash, setFlash] = useState(false);
  const firedRef = useRef(false);
  const flashTimer = useRef<number | null>(null);

  const fire = useCallback(() => {
    if (disabled || busy || firedRef.current) return;
    firedRef.current = true;
    setFlash(true);
    onAbort();
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    // Long enough to register as a press, short enough that a second abort is
    // never refused for cosmetic reasons.
    flashTimer.current = window.setTimeout(() => {
      firedRef.current = false;
      setFlash(false);
    }, 600);
  }, [disabled, busy, onAbort]);

  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    []
  );

  return (
    <button
      type="button"
      disabled={disabled || busy}
      onPointerDown={fire}
      onKeyDown={(e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          fire();
        }
      }}
      aria-label="Abortar missão e pousar imediatamente"
      title={disabled ? 'Disponível durante a missão' : 'Pousa a aeronave imediatamente'}
      className={cn(
        'group relative flex items-center gap-2.5 overflow-hidden rounded-full border-2 px-6 py-2.5',
        'transition-all duration-150 active:scale-[0.98]',
        disabled || busy
          ? 'cursor-not-allowed border-strut bg-hull text-haze-deep'
          : 'border-ember bg-ember/20 text-frost shadow-abort backdrop-blur-sm hover:bg-ember/35',
        flash && 'bg-ember text-abyss'
      )}
    >
      <AlertOctagon
        size={16}
        strokeWidth={2}
        className={cn(
          'relative shrink-0',
          disabled || busy ? 'text-haze-deep' : flash ? 'text-abyss' : 'text-ember'
        )}
      />
      <span className="relative font-cond text-sm font-semibold tracking-[0.12em]">
        {busy ? 'ABORTANDO' : 'ABORTAR MISSÃO'}
      </span>
    </button>
  );
};
