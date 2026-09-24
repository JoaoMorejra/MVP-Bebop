import React from 'react';
import { Camera, Check, Plane, RotateCcw, ScanLine, Siren, Wrench } from 'lucide-react';
import type { MissionState } from '../../types/mission';
import type { VoiceLevel } from '../../types/bmg';
import { VoiceControl } from '../shell/StatusBar';
import { cn } from '../../lib/format';

interface StageBarProps {
  stage: number;
  stageName: string;
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
   * The copilot's level, controlled from here as well as from pre-flight.
   *
   * A flight is exactly when an operator wants the voice down — it is talking,
   * and there is someone in the room — and pre-flight is not on screen then.
   */
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
}

/**
 * The five deterministic stages, named as the demonstration names them.
 *
 * The same five names are in `STEP_NAMES` (`electron/main.cjs`) and in
 * `MISSION_STAGES` (`types/mission.ts`), so the strip, the header and the log
 * all say the same word for the same stage.
 */
const STAGES = [
  { step: 1, label: 'Decolagem', icon: Plane },
  { step: 2, label: 'Varredura', icon: ScanLine },
  { step: 3, label: 'Acidente detectado', icon: Siren },
  { step: 4, label: 'Inspeção', icon: Camera },
  { step: 5, label: 'Retornando base', icon: RotateCcw },
] as const;

/**
 * States worth naming. Idle is absent on purpose: a bar announcing "aguardando
 * comando" told the operator what the unlit stage chips beneath it already say.
 */
const STATE_LABEL: Partial<Record<MissionState, string>> = {
  arming: 'Armando',
  running: 'Missão autônoma Bebop 2',
  aborting: 'Abortando',
  finished: 'Missão concluída',
  faulted: 'Missão interrompida',
};

/**
 * Where the aircraft is in its own sequence.
 *
 * A stage only lights when `mission.py` has logged its `[STEP N:` marker, so
 * the strip reports the pipeline rather than predicting it. Stages behind the
 * current one are marked done; stages ahead are drawn but unlit, which is what
 * makes the remaining flight legible at a glance.
 *
 * The strip is the centrepiece of a demonstration, so it has a row of its own
 * across the full width of the cockpit: five large numbered pills, the active
 * one lit solid mint with a breathing glow, and a progress rule under them that
 * moves with the pipeline. An evaluator across the room reads the stage from
 * here before they read anything else on the screen.
 *
 * On the bench the same pills become buttons. Running one routine is the point
 * of bench mode, and the strip is already the map of which routines exist.
 *
 * The header's middle column is empty by construction: the station's dock
 * floats at the top centre of the window in both tabs, directly over it.
 */
export const StageBar: React.FC<StageBarProps> = ({
  stage,
  stageName,
  state,
  benchMode,
  benchStage,
  onRunStage,
  onGotoStage,
  pendingStage,
  voice,
  onVolume,
  onToggleMute,
}) => {
  const aborted = state === 'aborting' || state === 'faulted';
  const running = state === 'running' || state === 'arming';
  const label = STATE_LABEL[state];
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
  const progress = Math.max(0, Math.min(5, stage)) / 5;

  return (
    <div className="flex shrink-0 flex-col gap-2">
      <header className="grid h-12 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center rounded-panel border border-strut-soft bg-hull-deep/80 px-4">
        <div className="flex min-w-0 items-center gap-2.5">
          {label ? (
            <>
              <span
                className={cn(
                  'h-2 w-2 shrink-0 rounded-full',
                  aborted ? 'bg-ember' : running ? 'bg-mint anim-breathe' : 'bg-haze-deep'
                )}
              />
              <span className="shrink-0 font-cond text-sm tracking-wide text-frost">{label}</span>
              {running && stageName ? (
                <>
                  <span aria-hidden className="text-haze-deep">
                    /
                  </span>
                  <span className="truncate font-cond text-sm font-semibold tracking-wide text-mint">
                    {stage > 0 ? `${stage} · ` : ''}
                    {stageName}
                  </span>
                </>
              ) : null}
            </>
          ) : launchable ? (
            <span className="flex items-center gap-2 font-cond text-sm tracking-wide text-cyan">
              <Wrench size={13} strokeWidth={2} />
              Bancada · escolha uma etapa
            </span>
          ) : (
            <span className="font-cond text-sm tracking-wide text-haze">Aguardando missão</span>
          )}
          {launchable && label ? (
            <span className="shrink-0 font-cond text-2xs tracking-wide text-cyan">· bancada</span>
          ) : null}
          {pendingStage !== null ? (
            <span className="shrink-0 font-cond text-2xs tracking-wide text-amber anim-breathe">
              · salto para {pendingStage} pedido
            </span>
          ) : null}
        </div>

        {/* Reserved for the dock. */}
        <span aria-hidden className="w-[230px]" />

        <div className="flex items-center justify-end">
          <VoiceControl voice={voice} onVolume={onVolume} onToggleMute={onToggleMute} align="right" />
        </div>
      </header>

      <nav
        aria-label="Modos de voo"
        className="relative rounded-panel border border-strut-soft bg-hull-deep/80 px-2.5 pb-3 pt-2.5"
      >
        <ol className="grid grid-cols-5 gap-2">
          {STAGES.map(({ step, label: stepLabel, icon: Icon }) => {
            const done = stage > step;
            const active = stage === step && !aborted;
            const halted = stage === step && aborted;
            const rehearsing = benchStage === step;
            const pending = pendingStage === step && !active;

            const body = (
              <>
                <span
                  className={cn(
                    'grid h-7 w-7 shrink-0 place-items-center rounded-full border transition-all duration-500',
                    active
                      ? 'border-abyss/30 bg-abyss text-mint'
                      : halted
                      ? 'border-ember/60 bg-ember/15 text-ember'
                      : done
                      ? 'border-mint/50 bg-mint/15 text-mint'
                      : rehearsing
                      ? 'border-cyan/60 bg-cyan/15 text-cyan'
                      : pending
                      ? 'border-amber/60 bg-amber/15 text-amber'
                      : 'border-strut bg-abyss/60 text-haze'
                  )}
                >
                  {done && !active && !halted ? (
                    <Check size={14} strokeWidth={3} />
                  ) : (
                    <Icon size={14} strokeWidth={active ? 2.6 : 2} />
                  )}
                </span>
                <span className="flex min-w-0 items-baseline gap-1.5">
                  <span
                    className={cn(
                      'tnum font-mono text-sm font-bold',
                      active ? 'text-abyss' : halted ? 'text-ember' : done ? 'text-mint' : 'text-haze'
                    )}
                  >
                    {step}
                  </span>
                  <span className={cn('text-sm', active ? 'text-abyss/60' : 'text-haze-deep')}>·</span>
                  <span
                    className={cn(
                      'truncate font-cond text-base font-semibold tracking-wide',
                      halted
                        ? 'text-ember'
                        : active
                        ? 'text-abyss'
                        : done
                        ? 'text-frost'
                        : rehearsing
                        ? 'text-cyan'
                        : pending
                        ? 'text-amber'
                        : interactive
                        ? 'text-frost/85'
                        : 'text-haze'
                    )}
                  >
                    {stepLabel}
                  </span>
                </span>
                {active ? (
                  <span aria-hidden className="ml-auto h-2 w-2 shrink-0 rounded-full bg-abyss anim-breathe" />
                ) : null}
              </>
            );

            const shell = cn(
              'flex h-12 w-full min-w-0 items-center gap-2.5 rounded-panel border px-3 text-left',
              'transition-all duration-500 ease-settle',
              active && 'scale-[1.02] border-mint-bright bg-mint anim-stage-glow',
              halted && 'border-ember bg-ember/15 shadow-abort',
              rehearsing && !active && 'border-cyan bg-cyan/10 shadow-bench',
              pending && !active && 'border-amber bg-amber/15 anim-breathe',
              done && !active && !halted && !pending && 'border-mint/35 bg-mint/[0.07]',
              !done && !active && !halted && !rehearsing && !pending && 'border-strut bg-abyss/40'
            );

            return (
              <li key={step} className="min-w-0">
                {interactive ? (
                  <button
                    type="button"
                    aria-current={active ? 'step' : undefined}
                    onClick={() => (launchable ? onRunStage(step) : onGotoStage(step))}
                    title={
                      launchable
                        ? rehearsing
                          ? `A etapa ${step} está em execução na bancada`
                          : `Executar a etapa ${step} na bancada, com os motores inertes${
                              running ? ' (interrompe a rotina atual)' : ''
                            }`
                        : `Comandar a missão a seguir na etapa ${step}`
                    }
                    className={cn(
                      shell,
                      !active &&
                        (launchable
                          ? 'hover:border-cyan hover:bg-cyan/15'
                          : 'hover:border-frost/40 hover:bg-frost/[0.06]')
                    )}
                  >
                    {body}
                  </button>
                ) : (
                  <span aria-current={active ? 'step' : undefined} className={shell}>
                    {body}
                  </span>
                )}
              </li>
            );
          })}
        </ol>

        {/* The pipeline's progress, under the pills. */}
        <div aria-hidden className="absolute inset-x-2.5 bottom-1 h-[3px] overflow-hidden rounded-full bg-strut/60">
          <div
            className={cn(
              'h-full rounded-full transition-[width] duration-700 ease-settle',
              aborted ? 'bg-ember' : 'bg-gradient-to-r from-kelp via-mint to-mint-bright'
            )}
            style={{ width: `${progress * 100}%` }}
          />
        </div>
      </nav>
    </div>
  );
};
