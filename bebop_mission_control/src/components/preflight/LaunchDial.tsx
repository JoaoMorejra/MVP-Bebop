import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { LaunchView } from '../../lib/launchSequence';
import type { PreflightBlockCode } from '../../lib/preflightGate';
import { cn } from '../../lib/format';

interface LaunchDialProps {
  /** The launch sequence on screen, or null when none is running. */
  view: LaunchView | null;
  ready: boolean;
  /** Which pre-flight gate is blocking the command, when any. */
  blockedCode?: PreflightBlockCode | null;
  /** What is holding the flight, in at most 18 characters (`preflightBlockedShort`). */
  blockedShort: string | null;
  /** The full reason (blocking gate or launch error), as the title and the accessible name. */
  blockedReason: string | null;
  /** "Falha ao iniciar" when the last launch did not start. */
  errorShort: string | null;
  benchMode: boolean;
  onLaunch: () => void;
  /**
   * Controls seated directly beneath the dial — the flight parameters and the
   * flight mode. What an operator reaches for immediately after deciding not
   * to press the button yet is the thing that would change their mind.
   */
  beneath?: React.ReactNode;
}

/**
 * The two lighting themes of the dial.
 *
 * A real flight is mint, the brand's live colour. Teste is the brand's
 * electric cyan: the same instrument, visibly a different act. The ring is
 * what tells them apart; the word on the dial is the same.
 */
const THEME = {
  flight: {
    rgb: '1,213,163',
    bright: '92,242,206',
    stroke: '#01D5A3',
    ring: 'border-mint/55',
    ringInner: 'border-mint/25',
  },
  bench: {
    rgb: '0,229,255',
    bright: '122,243,255',
    stroke: '#00E5FF',
    ring: 'border-cyan/60',
    ringInner: 'border-cyan/30',
  },
} as const;

/** Radius of the progress arc, over the outer ring of the 248 px dial. */
const PROGRESS_RADIUS = 124;
const PROGRESS_LENGTH = 2 * Math.PI * PROGRESS_RADIUS;
/** Duration of the leave animation of a step label (`anim-step-out` / `text-states-swap`). */
const LABEL_LEAVE_MS = 180;

/**
 * Angular separation (in degrees) between the two orbiting perimeter beams.
 *
 * Without the driver (`no-driver`) the two beams sit at opposite sides of the
 * circle (`180deg`). As the driver, link, topics and telemetry gates come up,
 * the angular distance between them decreases (`135deg` -> `105deg` -> `70deg`
 * -> `48deg`) until they merge at `0deg` into a single unified beam when the
 * aircraft is ready to launch (`ready`).
 */
function resolveBeamGapDeg(
  dialState: 'ready' | 'no-driver' | 'blocked' | 'running',
  blockedCode?: PreflightBlockCode | null
): number {
  if (dialState === 'ready' || dialState === 'running') return 0;
  if (dialState === 'no-driver') return 180;
  switch (blockedCode) {
    case 'params':
      return 135;
    case 'link':
      return 105;
    case 'topics':
      return 70;
    default:
      return 48;
  }
}

/**
 * Renders `text` as staggered characters for the `texts-reveal` animation
 * (`https://transitions.dev/detail.html?t=texts-reveal`) while preserving the
 * exact `textContent` of the parent span.
 */
const RevealText: React.FC<{ text: string }> = ({ text }) => (
  <>
    {Array.from(text).map((char, index) => (
      <span
        key={`${index}-${char}`}
        className="t-reveal-item"
        style={{ '--i': index } as React.CSSProperties}
      >
        {char}
      </span>
    ))}
  </>
);

/**
 * The command, and the launch it starts.
 *
 * Around its perimeter a single persistent orbital assembly runs continuously
 * without DOM cuts across every state:
 * - `no-driver`: two antipodal beams (`180deg` apart) orbiting the circle
 * - `blocked`: as driver, link and topics initialize, the angular distance
 *   between the two beams decreases and they pull toward each other
 * - `ready`: the two beams fuse at `0deg` into one unified, intense beam
 * - `running`: on click, that single unified beam stretches around the
 *   perimeter proportionally to the `0%..100%` countdown while the clean
 *   interior displays only the large percentage and parameter name.
 */
export const LaunchDial: React.FC<LaunchDialProps> = ({
  view,
  ready,
  blockedCode,
  blockedShort,
  blockedReason,
  errorShort,
  benchMode,
  onLaunch,
  beneath,
}) => {
  const theme = benchMode ? THEME.bench : THEME.flight;
  const launchedRef = useRef(false);
  const running = view !== null;
  const clickable = ready && !running;

  const handleClick = useCallback(() => {
    if (!clickable) return;
    if (launchedRef.current) return;
    launchedRef.current = true;
    onLaunch();
    setTimeout(() => {
      launchedRef.current = false;
    }, 1000);
  }, [clickable, onLaunch]);

  const label = view ? view.message ?? view.label : null;
  const labelKey = view ? view.message ?? view.stepId ?? 'none' : null;
  const [leaving, setLeaving] = useState<{ key: string; text: string } | null>(null);
  const shown = useRef<{ key: string | null; text: string | null }>({ key: null, text: null });
  useEffect(() => {
    const previous = shown.current;
    shown.current = { key: labelKey, text: label };
    if (previous.key === null || previous.key === labelKey || previous.text === null) return;
    setLeaving({ key: previous.key, text: previous.text });
    const id = window.setTimeout(() => setLeaving(null), LABEL_LEAVE_MS);
    return () => window.clearTimeout(id);
  }, [labelKey, label]);

  const percent = view ? Math.max(0, Math.min(100, Math.round(view.percent))) : 0;
  const readyVisual = ready && !errorShort;
  const isNoDriver =
    !readyVisual && (blockedCode === 'driver' || (!blockedCode && !errorShort && blockedShort === 'Sem driver'));
  const dialState = running ? 'running' : readyVisual ? 'ready' : isNoDriver ? 'no-driver' : 'blocked';
  const beamGapDeg = resolveBeamGapDeg(dialState, blockedCode);
  const fillDeg = (percent / 100) * 360;
  const fillRatio = percent / 100;
  const singleBeamGradient = `conic-gradient(from 0deg, transparent 0deg, rgba(${theme.rgb},0.55) 68deg, rgba(${theme.bright},0.96) 88deg, transparent 96deg, transparent 360deg)`;
  const ringMask = 'radial-gradient(circle, transparent 0 119.5px, #000 120px 124px, transparent 124px)';

  return (
    <div className="flex flex-col items-center gap-5">
      <button
        type="button"
        data-launch-dial
        data-theme={benchMode ? 'bench' : 'flight'}
        data-dial-state={dialState}
        onClick={handleClick}
        disabled={!clickable}
        aria-label={
          running
            ? `Iniciando missão, ${label ?? ''}`
            : blockedReason
            ? `Iniciar missão: ${blockedReason}`
            : benchMode
            ? 'Iniciar missão no modo Teste'
            : 'Iniciar missão'
        }
        className={cn(
          'group relative grid h-[248px] w-[248px] select-none place-items-center rounded-full',
          'transition-transform duration-300 ease-settle',
          clickable ? 'cursor-pointer hover:scale-[1.02] active:scale-[0.99]' : running ? 'cursor-default' : 'cursor-not-allowed'
        )}
      >
        <span
          aria-hidden
          data-dial-aura
          className={cn(
            'pointer-events-none absolute inset-[-28px] rounded-full opacity-100 transition-all duration-700',
            !running &&
              (readyVisual
                ? 'dial-aura-ready'
                : isNoDriver
                ? 'dial-aura-nodriver'
                : 'dial-aura-blocked')
          )}
          style={{
            background: running
              ? `radial-gradient(circle, rgba(${theme.bright},${(0.16 + fillRatio * 0.2).toFixed(3)}) 0%, rgba(${theme.rgb},${(0.05 + fillRatio * 0.1).toFixed(3)}) 48%, transparent 70%)`
              : `radial-gradient(circle, rgba(${theme.rgb},0.22) 0%, rgba(${theme.rgb},0.06) 45%, transparent 68%)`,
          }}
        />

        <span
          aria-hidden
          data-dial-outer-ring
          className={cn('pointer-events-none absolute inset-0 rounded-full border transition-colors duration-500', theme.ring)}
        />
        <span
          aria-hidden
          data-dial-inner-ring
          className={cn(
            'pointer-events-none absolute inset-[13px] rounded-full border transition-colors duration-500',
            theme.ringInner
          )}
        />
        <span
          aria-hidden
          data-dial-core
          className="pointer-events-none absolute inset-[13px] rounded-full bg-abyss/55 backdrop-blur-md transition-colors duration-500"
          style={{
            boxShadow: `inset 0 1px 0 0 rgba(${theme.bright},0.2), inset 0 -24px 48px -24px rgba(${theme.rgb},0.35)`,
          }}
        />

        {/* Persistent, continuous perimeter beam assembly:
            2 beams at 180deg (no-driver) -> converging closer (blocked) -> merged into 1 beam at 0deg (ready) */}
        <span
          aria-hidden
          data-perimeter-light={dialState}
          data-beam-gap={beamGapDeg}
          className="pointer-events-none absolute inset-0 rounded-full"
        >
          <span
            data-ready-halo
            className={cn(
              'dial-ready-halo absolute inset-[-6px] rounded-full transition-opacity duration-700',
              readyVisual && !running ? 'opacity-100' : 'opacity-0'
            )}
            style={{
              border: `1px solid rgba(${theme.bright},0.38)`,
              boxShadow: `0 0 22px -2px rgba(${theme.rgb},0.42)`,
            }}
          />
          <span
            data-orbit-carrier
            className={cn(
              'dial-orbit-carrier absolute inset-0 rounded-full transition-opacity duration-500',
              running ? 'opacity-0' : 'opacity-100'
            )}
          >
            <span
              data-beam-primary
              className="dial-beam-primary absolute inset-0 rounded-full"
              style={{
                background: singleBeamGradient,
                mask: ringMask,
                WebkitMask: ringMask,
                filter: readyVisual ? `drop-shadow(0 0 8px rgba(${theme.bright},0.65))` : undefined,
              }}
            />
            <span
              data-beam-gap-carrier
              className="dial-beam-gap-carrier absolute inset-0 rounded-full"
              style={{
                transform: `rotate(${beamGapDeg}deg)`,
              }}
            >
              <span
                data-beam-secondary
                className={cn(
                  'dial-beam-secondary absolute inset-0 rounded-full',
                  dialState === 'blocked' && 'dial-beam-converging'
                )}
                style={{
                  background: singleBeamGradient,
                  mask: ringMask,
                  WebkitMask: ringMask,
                }}
              />
            </span>
          </span>
        </span>

        {/* Unified beam becoming the 0%..100% countdown beam around the perimeter */}
        <span
          aria-hidden
          data-dial-fill-light
          data-fill-progress={percent}
          className={cn(
            'pointer-events-none absolute inset-0 rounded-full transition-opacity duration-500 ease-out',
            running ? 'opacity-100' : 'opacity-0'
          )}
          style={
            {
              '--fill-deg': `${fillDeg.toFixed(2)}deg`,
              '--fill-pct': `${percent}%`,
            } as React.CSSProperties
          }
        >
          <span
            data-dial-perimeter-fill
            className="dial-fill-perimeter absolute inset-[-2px] rounded-full"
            style={{
              background: `conic-gradient(from 0deg, rgba(${theme.bright},0.92) 0deg, rgba(${theme.rgb},0.85) ${Math.max(0, fillDeg - 10).toFixed(1)}deg, rgba(255,255,255,0.98) ${fillDeg.toFixed(1)}deg, transparent ${(fillDeg + 6).toFixed(1)}deg, transparent 360deg)`,
              mask: 'radial-gradient(circle, transparent 0 118.5px, #000 119.5px 126px, transparent 126px)',
              WebkitMask: 'radial-gradient(circle, transparent 0 118.5px, #000 119.5px 126px, transparent 126px)',
              filter: `drop-shadow(0 0 10px rgba(${theme.bright},0.65))`,
            }}
          />
        </span>

        <svg
          aria-hidden
          data-progress={percent}
          viewBox="0 0 248 248"
          className="pointer-events-none absolute inset-0 h-full w-full -rotate-90"
        >
          <circle
            data-progress-arc
            cx="124"
            cy="124"
            r={PROGRESS_RADIUS - 1.5}
            fill="none"
            stroke={theme.stroke}
            strokeWidth="3.5"
            strokeLinecap="round"
            strokeDasharray={PROGRESS_LENGTH}
            strokeDashoffset={PROGRESS_LENGTH * (1 - percent / 100)}
            opacity={running ? 1 : 0}
            className="dial-progress"
          />
        </svg>

        <span className="relative z-10 flex w-[208px] flex-col items-center gap-2">
          {running ? (
            <>
              <span
                data-percent
                className="title-pixel tnum font-pixel text-5xl font-semibold leading-none tracking-tight text-frost"
              >
                {`${percent}%`}
              </span>
              <span
                data-text-swap
                className="relative grid h-8 w-full place-items-center overflow-hidden"
              >
                {leaving ? (
                  <span
                    key={`leaving-${leaving.key}`}
                    data-step-label
                    data-leaving
                    aria-hidden
                    className="anim-step-out t-text-swap-out absolute font-sans text-xl font-medium tracking-[0.02em] text-frost/75"
                  >
                    {leaving.text}
                  </span>
                ) : null}
                {label ? (
                  <span
                    key={labelKey ?? 'none'}
                    data-step-label
                    data-text-anim={view?.message ? 'text-states-swap' : 'texts-reveal'}
                    aria-hidden
                    className={cn(
                      'anim-step-in t-text-swap-in t-texts-reveal absolute text-xl font-semibold tracking-[0.02em]',
                      view?.message
                        ? cn('title-pixel font-pixel', benchMode ? 'text-cyan' : 'text-mint')
                        : 'font-sans text-frost'
                    )}
                  >
                    <RevealText text={label} />
                  </span>
                ) : null}
              </span>
            </>
          ) : readyVisual ? (
            <span
              data-dial-label
              className="title-pixel font-pixel text-xl font-semibold tracking-[0.14em] text-frost"
              style={{ paddingLeft: '0.14em' }}
            >
              Iniciar
            </span>
          ) : (
            <span
              data-dial-label
              className="title-pixel line-clamp-2 text-center font-pixel text-base font-medium leading-tight tracking-[0.05em] text-frost"
              style={{ paddingLeft: '0.05em' }}
            >
              {errorShort ?? blockedShort ?? 'Iniciar'}
            </span>
          )}
        </span>
      </button>

      <span className="sr-only" aria-live="polite">
        {running ? label ?? '' : ''}
      </span>

      {beneath}
    </div>
  );
};
