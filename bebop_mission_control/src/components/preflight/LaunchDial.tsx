import React, { useCallback, useRef } from 'react';
import { Lock, Play } from 'lucide-react';
import { cn } from '../../lib/format';

interface LaunchDialProps {
  ready: boolean;
  /** What is holding the flight, when something is. */
  blockedReason: string | null;
  benchMode: boolean;
  /**
   * Controls seated directly beneath the dial — the flight parameters, and the
   * bench. They belong here rather than in a panel of their own: what an
   * operator reaches for immediately after deciding not to press the button yet
   * is the thing that would change their mind.
   */
  beneath?: React.ReactNode;
  onLaunch: () => void;
}

/**
 * The two lighting themes of the dial.
 *
 * A real flight is mint, the brand's live colour. The bench is the brand's
 * electric cyan: the same instrument, visibly a different act. An operator who
 * has switched the motors off should never be able to mistake the rehearsal
 * command for the flight command, and the colour is what the eye reads first.
 */
const THEME = {
  flight: {
    rgb: '1,213,163',
    bright: '92,242,206',
    ring: 'border-mint/55',
    ringInner: 'border-mint/25',
    icon: 'text-mint',
  },
  bench: {
    rgb: '0,229,255',
    bright: '122,243,255',
    ring: 'border-cyan/60',
    ringInner: 'border-cyan/30',
    icon: 'text-cyan',
  },
} as const;

/**
 * The command.
 *
 * One control, at the centre of the field, large enough to be unmistakable —
 * there is exactly one thing an operator does on this screen and everything
 * else on it is there to tell them whether they should.
 *
 * Clicking initiates the mission sequence (with countdown floor) in both
 * real flight ("VOO REAL: motores serão armados") and bench mode ("Motores desligados").
 */
export const LaunchDial: React.FC<LaunchDialProps> = ({
  ready,
  blockedReason,
  benchMode,
  beneath,
  onLaunch,
}) => {
  const theme = benchMode ? THEME.bench : THEME.flight;
  const launchedRef = useRef(false);

  const handleClick = useCallback(() => {
    if (!ready) return;
    if (launchedRef.current) return;
    launchedRef.current = true;
    onLaunch();
    setTimeout(() => {
      launchedRef.current = false;
    }, 1000);
  }, [ready, onLaunch]);

  return (
    <div className="flex flex-col items-center gap-5">
      <button
        type="button"
        onClick={handleClick}
        disabled={!ready}
        title={blockedReason ?? undefined}
        aria-label={
          ready
            ? benchMode
              ? 'Iniciar missão em bancada, motores desligados'
              : 'Iniciar missão, voo real'
            : `Iniciar missão, bloqueado: ${blockedReason ?? ''}`
        }
        className={cn(
          'group relative grid h-[248px] w-[248px] place-items-center rounded-full select-none',
          'transition-transform duration-300 ease-settle',
          ready
            ? 'cursor-pointer hover:scale-[1.02] active:scale-[0.99]'
            : 'cursor-not-allowed'
        )}
      >
        {/* Outer halo. Present when the command is cleared. */}
        <span
          aria-hidden
          className={cn(
            'absolute inset-[-26px] rounded-full transition-opacity duration-700',
            ready ? 'opacity-100' : benchMode ? 'opacity-40' : 'opacity-0'
          )}
          style={{
            background: `radial-gradient(circle, rgba(${theme.rgb},0.22) 0%, rgba(${theme.rgb},0.06) 45%, transparent 68%)`,
          }}
        />

        {/* The rings. */}
        <span
          aria-hidden
          className={cn(
            'absolute inset-0 rounded-full border transition-colors duration-500',
            ready || benchMode ? theme.ring : 'border-frost/15'
          )}
        />
        <span
          aria-hidden
          className={cn(
            'absolute inset-[13px] rounded-full border transition-colors duration-500',
            ready || benchMode ? theme.ringInner : 'border-frost/[0.07]'
          )}
        />
        {/* Dark interior pane */}
        <span
          aria-hidden
          className={cn(
            'absolute inset-[13px] rounded-full backdrop-blur-md transition-colors duration-500',
            ready ? 'bg-abyss/55' : 'bg-abyss/62'
          )}
          style={{
            boxShadow:
              ready || benchMode
                ? `inset 0 1px 0 0 rgba(${theme.bright},0.2), inset 0 -24px 48px -24px rgba(${theme.rgb},0.35)`
                : 'inset 0 1px 0 0 rgba(232,242,240,0.08)',
          }}
        />

        {/* Sweeping tick */}
        {ready ? (
          <span
            aria-hidden
            className="absolute inset-0 rounded-full"
            style={{
              background: `conic-gradient(from 0deg, transparent 0deg, transparent 300deg, rgba(${theme.rgb},0.55) 352deg, transparent 360deg)`,
              mask: 'radial-gradient(circle, transparent 0 120px, #000 120px 124px, transparent 124px)',
              WebkitMask:
                'radial-gradient(circle, transparent 0 120px, #000 120px 124px, transparent 124px)',
              animation: 'dial-sweep 4.5s linear infinite',
            }}
          />
        ) : null}

        <span className="relative flex w-[176px] flex-col items-center gap-2.5">
          {ready ? (
            <Play
              size={26}
              strokeWidth={1.8}
              fill="currentColor"
              className={cn(theme.icon, 'transition-colors')}
            />
          ) : (
            <Lock size={24} strokeWidth={1.6} className={benchMode ? 'text-cyan/60' : 'text-frost/35'} />
          )}
          <span
            className={cn(
              'font-mono text-xl font-medium tracking-[0.34em] transition-colors duration-500',
              ready ? 'text-frost' : 'text-frost/35'
            )}
            style={{ paddingLeft: '0.34em' }}
          >
            INICIAR
          </span>

          {/* State, inside the dial, directly under the command it qualifies. */}
          <span className="flex min-h-[2.25rem] flex-col items-center gap-1">
            {benchMode ? (
              <span className="font-cond text-2xs font-semibold uppercase tracking-[0.18em] text-cyan">
                Motores desligados
              </span>
            ) : (
              <span
                className={cn(
                  'font-cond text-2xs font-semibold uppercase tracking-[0.18em] transition-colors',
                  ready ? 'text-mint/90' : 'text-frost/40'
                )}
              >
                VOO REAL: motores serão armados
              </span>
            )}
            {blockedReason ? (
              <span
                className={cn(
                  'line-clamp-3 text-center font-mono text-3xs leading-snug',
                  benchMode ? 'text-frost/60' : 'text-amber/90'
                )}
              >
                {blockedReason}
              </span>
            ) : null}
          </span>
        </span>
      </button>

      {beneath}
    </div>
  );
};
