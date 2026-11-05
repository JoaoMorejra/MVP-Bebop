import React from 'react';
import { Camera, Check, Plane, RotateCcw, ScanLine, Siren } from 'lucide-react';
import type { MissionState } from '../../types/mission';
import type { FillState } from '../../lib/stageProgress';
import { cn } from '../../lib/format';
import { stageLabel, type MissionMode } from '../../lib/missionMode';

interface StageBarProps {
  stage: number;
  state: MissionState;
  /**
   * Bench mode. The stage pills launch a single routine rather than commanding
   * a jump within one already running.
   */
  benchMode: boolean;
  /** Which stage the bench is running now, if any. */
  benchStage: number | null;
  onRunStage: (stage: number) => void;
  /** Ask the running mission to continue at this stage. */
  onGotoStage: (stage: number) => void;
  /** A jump has been asked for and the current step has not unwound yet. */
  pendingStage: number | null;
  /**
   * Seconds left before the bench routine of `benchStage` spawns, shown inside
   * its pill (D7); `null` or zero shows nothing.
   */
  benchCountdown?: number | null;
  /** Relabels the last stage; the sheet is locked during flight, so it matches the launched mode. */
  missionMode?: MissionMode;
  /** Return plan of the flight (`rtl.enabled`): relabels the last stage. */
  rtlEnabled?: boolean;
  /**
   * How far each stage has filled (`useStageProgress`). Without it each pill
   * is empty or full by the stage the mission reports.
   */
  fill?: FillState;
}

/**
 * The five deterministic stages, named as the demonstration names them.
 *
 * The same five names are in `STEP_NAMES` (`electron/main.cjs`) and in
 * `MISSION_STAGES` (`types/mission.ts`), so the strip and the log say the
 * same word for the same stage.
 */
const STAGES = [
  { step: 1, label: 'Decolagem', icon: Plane },
  { step: 2, label: 'Varredura', icon: ScanLine },
  { step: 3, label: 'Acidente detectado', icon: Siren },
  { step: 4, label: 'Inspeção', icon: Camera },
  { step: 5, label: 'Retornando base', icon: RotateCcw },
] as const;

type Tone = 'flight' | 'bench' | 'halted';

const FILL_TONE: Record<Tone, string> = {
  flight: 'bg-mint',
  bench: 'bg-cyan',
  halted: 'bg-ember',
};

/**
 * One pill's content. Drawn twice, on top of each other: once light for the
 * part not yet filled, once dark and clipped to the fill, so the label reads
 * on either side of the edge as it moves across it.
 */
const PillContent: React.FC<{
  step: number;
  label: string;
  Icon: React.ElementType;
  done: boolean;
  onFill: boolean;
  dim: boolean;
  /** Colour of the fill the dark copy sits on, so its icon ring covers the light one beneath. */
  tone: Tone;
  trailing?: React.ReactNode;
}> = ({ step, label, Icon, done, onFill, dim, tone, trailing }) => (
  <>
    <span
      className={cn(
        'grid h-7 w-7 shrink-0 place-items-center rounded-full border',
        onFill ? cn('border-abyss/35 text-abyss', FILL_TONE[tone]) : 'border-strut bg-abyss/60 text-haze'
      )}
    >
      {done ? <Check size={14} strokeWidth={3} /> : <Icon size={14} strokeWidth={onFill ? 2.4 : 2} />}
    </span>
    <span className="flex min-w-0 items-baseline gap-1.5">
      <span className={cn('tnum font-mono text-sm font-bold', onFill ? 'text-abyss' : dim ? 'text-haze' : 'text-frost/85')}>
        {step}
      </span>
      <span className={cn('text-sm', onFill ? 'text-abyss/60' : 'text-haze-deep')}>·</span>
      <span
        className={cn(
          'truncate font-pixel text-[13.5px] font-semibold tracking-[0.02em]',
          onFill ? 'text-abyss' : dim ? 'text-haze' : 'text-frost/85'
        )}
      >
        {label}
      </span>
    </span>
    {trailing}
  </>
);

/**
 * Where the aircraft is in its own sequence.
 *
 * Each pill fills from the left, in the colour of the flight, as the stage
 * runs (`lib/stageProgress.ts`): from what the mission reports for it, never
 * on a timer, and only to the end once the mission has moved on. A stage the
 * mission skips is still filled in full, in its turn, before the next one
 * starts. The stage being filled breathes; the pills behind it hold their full
 * fill with a tick. An operator abort is not shown as one: the strip runs on
 * to the end in the colour of the flight, as a flight that is ending
 * (`hooks/useStageProgress.ts`). Only a fault stops the fill where it is and
 * turns it ember. On the bench it fills in the same green gradient.
 *
 * The strip opens the cockpit on its own, with no header above it and no rule
 * under it: the fills are the progress. On the bench the pills become
 * buttons, and in flight they ask the mission to continue at that stage.
 */
export const StageBar: React.FC<StageBarProps> = ({
  stage,
  state,
  benchMode,
  benchStage,
  onRunStage,
  onGotoStage,
  pendingStage,
  benchCountdown = null,
  missionMode = 'advanced',
  rtlEnabled = true,
  fill,
}) => {
  const aborted = state === 'faulted';
  const running = state === 'running' || state === 'arming';
  /**
   * On the bench every stage is always a button — the point of a bench is that
   * the operator tries one routine, sees what it does, and tries another.
   * Picking a different stage while one is up stops it first.
   */
  const launchable = benchMode;
  /**
   * A running mission can be redirected. The pill publishes on the mission's
   * control topic and marks itself pending until the mission reports the new
   * stage; the mission is the authority on what it will accept.
   */
  const redirectable = running;
  const interactive = launchable || redirectable;
  const cursor = fill ? fill.cursor : stage;

  return (
    <nav aria-label="Modos de voo" className="shrink-0">
      <ol className="grid grid-cols-5 gap-1.5">
        {STAGES.map(({ step, label: stepLabel, icon: Icon }) => {
          const amount = fill ? fill.fills[step - 1] : stage > step ? 1 : 0;
          const done = amount >= 1;
          const current = cursor === step && !done;
          const active = current && !aborted;
          const halted = current && aborted;
          const rehearsing = benchStage === step;
          const pending = pendingStage === step && !current;
          // The strip fills in the flight's green gradient in every mode, the
          // bench included: a rehearsal is presented exactly as the flight.
          const tone: Tone = halted ? 'halted' : 'flight';
          const percent = Math.round(Math.max(0, Math.min(1, amount)) * 1000) / 10;
          const label = stageLabel(missionMode, step, stepLabel, rtlEnabled);

          const trailing = (onFill: boolean) => (
            <>
              {rehearsing && benchCountdown !== null && benchCountdown > 0 && !onFill ? (
                <span className="ml-auto shrink-0">
                  <span data-bench-countdown aria-hidden="true" className="tnum font-mono text-3xs text-cyan">
                    {benchCountdown}
                  </span>
                  <span className="sr-only">{`${benchCountdown} segundos para iniciar`}</span>
                </span>
              ) : null}
              {active ? (
                <span
                  aria-hidden
                  className={cn('ml-auto h-2 w-2 shrink-0 rounded-full anim-breathe', onFill ? 'bg-abyss' : 'bg-mint')}
                />
              ) : null}
            </>
          );

          const body = (
            <>
              <span
                aria-hidden
                data-stage-fill
                data-stage={step}
                className={cn('stage-fill', FILL_TONE[tone], tone === 'flight' && 'stage-fill-flight')}
                style={{ width: `${percent}%` }}
              />
              <span className="stage-face">
                <PillContent
                  step={step}
                  label={label}
                  Icon={Icon}
                  done={done}
                  onFill={false}
                  dim={!interactive && amount === 0}
                  tone={tone}
                  trailing={trailing(false)}
                />
              </span>
              <span aria-hidden className="stage-face stage-face-on" style={{ clipPath: `inset(0 ${100 - percent}% 0 0)` }}>
                <PillContent step={step} label={label} Icon={Icon} done={done} onFill dim={false} tone={tone} trailing={trailing(true)} />
              </span>
            </>
          );

          const shell = cn(
            'stage-pill',
            active && 'stage-pill-active',
            halted && 'border-ember shadow-abort',
            rehearsing && !current && 'border-cyan shadow-bench',
            pending && 'border-amber anim-breathe',
            done && !halted && !pending && 'border-mint/45',
            !done && !current && !rehearsing && !pending && 'border-transparent'
          );
          const dataProps = {
            'data-stage': step,
            'data-fill': percent,
            'data-state': done ? 'done' : active ? 'active' : halted ? 'halted' : 'idle',
            'aria-current': active ? ('step' as const) : undefined,
          };

          return (
            <li key={step} className="min-w-0">
              {interactive ? (
                <button
                  type="button"
                  {...dataProps}
                  onClick={() => (launchable ? onRunStage(step) : onGotoStage(step))}
                  className={cn(
                    shell,
                    !active && (launchable ? 'hover:border-cyan hover:bg-cyan/10' : 'hover:border-frost/40 hover:bg-frost/[0.04]')
                  )}
                >
                  {body}
                </button>
              ) : (
                <span {...dataProps} className={shell}>
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
};
