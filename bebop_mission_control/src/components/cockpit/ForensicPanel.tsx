import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Ambulance, CarFront, HeartPulse, ScanSearch, Siren, Square, type LucideIcon } from 'lucide-react';
import type { RawEvidence } from '../../types/bmg';
import type { Finding, FindingTopic } from '../../lib/forensics';
import { TOPIC_LABEL } from '../../lib/forensics';
import { PHRASE_POOLS } from '../../lib/copilotPhrases';
import { cn, stampLabel } from '../../lib/format';
import { FINISH_HOLD_MS, type FinishLockResult } from '../../lib/finishLock';
import type { FindingReading } from '../../hooks/useCopilot';
import { prefersReducedMotion } from '../shell/motion';
import { EvidenceGrid } from './EvidenceGrid';

interface ForensicPanelProps {
  latest: RawEvidence | null;
  count: number;
  /** 1–5. The nadir capture happens in stage 4. */
  stage: number;
  missionOver: boolean;
  /** The aircraft is down from a flight that completed: the report may be read. */
  landed: boolean;
  /** This flight's findings, in this flight's order. */
  report: Finding[] | null;
  /** How many of them the copilot has read so far. */
  reportRevealed: number;
  /**
   * How many of them the copilot has begun to read: a card appears as its
   * sentence starts. Defaults to `reportRevealed`.
   */
  reportAnnounced?: number;
  /** When each card's sentence stops sounding (`useForensicNarration`): its typed text ends then. */
  reportReadings?: ReadonlyArray<FindingReading | undefined>;
  /**
   * How far the flight is through its last two stages, 0 to 1, from the stage
   * strip's measured fill (`lib/stageProgress.ts`). The photograph forms out of
   * the grid on it, from where it stood at the capture to touchdown.
   */
  flightProgress?: number;
  /** How far the flight is through its first four stages, 0 to 1: how far the waiting grid has split. */
  waitProgress?: number;
  /**
   * The aircraft is down and the report waits for the victim assessment of the
   * capture: the victim card holds a visual placeholder and nothing is read.
   */
  reportAnalyzing?: boolean;
  /** The closing line as the copilot said it, so screen and voice agree. */
  reportClosing?: string | null;
  onFinish: () => void;
  /** The finish lock (`lib/finishLock.ts`): whether, why not, and how. */
  lock: FinishLockResult;
}

/** The four thin corner marks over the capture, 10 px legs. */
const FRAME_MARKS: readonly string[] = [
  'left-1.5 top-1.5 border-l border-t',
  'right-1.5 top-1.5 border-r border-t',
  'bottom-1.5 right-1.5 border-b border-r',
  'bottom-1.5 left-1.5 border-b border-l',
];

/**
 * A finding's sentence typed out while the copilot reads it.
 *
 * One character at a time behind a blinking caret, after the Kinetics
 * typewriter, paced on the voice rather than on a fixed interval: the text
 * waits while `endsAt` is null (the sentence is not sounding yet), then
 * advances linearly to arrive at its last character at `endsAt`, the moment
 * the copilot stops speaking. A later `endsAt` (the voice reported its real
 * length) only re-paces what is left, so the text never jumps or goes back.
 * The full sentence reserves its lines from the first frame, so typing never
 * moves the cards below it, and is what a screen reader gets.
 */
const TypewriterText: React.FC<{ text: string; endsAt: number | null }> = ({ text, endsAt }) => {
  const typed = useRef(prefersReducedMotion() ? text.length : 0);
  const [count, setCount] = useState(typed.current);

  useEffect(() => {
    if (prefersReducedMotion()) {
      typed.current = text.length;
      setCount(text.length);
      return;
    }
    if (endsAt === null || typed.current >= text.length) return;
    let frame = 0;
    let last = Date.now();
    const tick = () => {
      const now = Date.now();
      const left = endsAt - last;
      typed.current =
        left <= 0 ? text.length : Math.min(text.length, typed.current + ((text.length - typed.current) * (now - last)) / left);
      last = now;
      setCount(Math.floor(typed.current));
      if (typed.current < text.length) frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [endsAt, text.length]);

  const typing = count < text.length;
  return (
    <span data-typing={typing || undefined} className="grid">
      <span aria-hidden className="invisible col-start-1 row-start-1">
        {text}
      </span>
      <span aria-hidden className="col-start-1 row-start-1">
        {text.slice(0, count)}
        {typing ? <span className="type-caret" /> : null}
      </span>
      <span className="sr-only">{text}</span>
    </span>
  );
};

/** The glyph of each finding: the victim's pulse, the vehicle, the police siren, the ambulance. */
const TOPIC_ICON: Record<FindingTopic, LucideIcon> = {
  victim: HeartPulse,
  vehicle: CarFront,
  police: Siren,
  samu: Ambulance,
};

/**
 * The badge of a finding: its glyph in a beacon turned on its point.
 *
 * As the card appears the beacon lands -- turning in from a quarter turn
 * back, small, to sit square on its point with a slight overshoot -- and the
 * glyph is traced in after it. Still under `prefers-reduced-motion`.
 */
const FindingBadge: React.FC<{ topic: FindingTopic }> = ({ topic }) => {
  const Icon = TOPIC_ICON[topic];
  return (
    <span data-finding-badge={topic} aria-hidden className="finding-badge">
      <svg className="finding-badge-frame" viewBox="0 0 44 44" fill="none">
        <rect className="finding-badge-diamond" x="9" y="9" width="26" height="26" rx="5" />
      </svg>
      <span className="finding-badge-glyph">
        <Icon size={18} strokeWidth={1.7} />
      </span>
    </span>
  );
};

/** Pull `YYYYMMDD_HHMMSS` back out of the filename the mission wrote. */
function stampOf(evidence: RawEvidence): string {
  return evidence.filename.replace(/^accident_(raw|inspected)_/, '').replace(/\.(png|jpg)$/, '');
}

/**
 * The forensic wall: the capture, and what was concluded from it.
 *
 * The whole flight exists to produce one frame, so it holds a column of its own
 * rather than a notification that can be missed: the panel waits visibly
 * through the first three stages, says exactly what it is waiting for, and the
 * capture drops into it the moment `bmg:raw-evidence-ready` fires.
 *
 * It stays in this column for the rest of the flight. An earlier build floated
 * the capture over the centre of the cockpit and held it there through the
 * return leg, which put the one thing the flight produced on top of the two
 * things the operator flies it with — the feed and the map. The evidence has a
 * place; covering the aircraft to show it off is not respecting that place.
 *
 * The frame shown here is the **original**, not the annotated one. Boxes are
 * what the detector believed; the photograph is what the camera saw, and an
 * assessment presented over a machine's own annotations is an assessment of the
 * machine. The annotated copy is one click away in the library.
 *
 * On touchdown the image gives up its height to the findings, which appear one
 * at a time as the copilot reads them.
 */
export const ForensicPanel: React.FC<ForensicPanelProps> = ({
  latest,
  count,
  stage,
  missionOver,
  landed,
  report,
  reportRevealed,
  reportAnnounced,
  reportReadings = [],
  flightProgress,
  waitProgress = 0,
  reportAnalyzing = false,
  reportClosing,
  onFinish,
  lock,
}) => {
  const holdTimer = useRef<number | null>(null);
  const [holding, setHolding] = useState(false);

  const cancelHold = useCallback(() => {
    if (holdTimer.current !== null) window.clearTimeout(holdTimer.current);
    holdTimer.current = null;
    setHolding(false);
  }, []);

  // A press on a lost link only counts once held for the full duration. Each
  // pointer-down restarts the one timer rather than adding another, so a
  // bouncing press cannot finish twice.
  const startHold = useCallback(() => {
    if (!lock.enabled || !lock.requiresConfirm) return;
    cancelHold();
    setHolding(true);
    holdTimer.current = window.setTimeout(() => {
      holdTimer.current = null;
      setHolding(false);
      onFinish();
    }, FINISH_HOLD_MS);
  }, [cancelHold, lock.enabled, lock.requiresConfirm, onFinish]);

  useEffect(() => cancelHold, [cancelHold]);
  useEffect(() => {
    if (!lock.requiresConfirm) cancelHold();
  }, [cancelHold, lock.requiresConfirm]);

  const click = useCallback(() => {
    if (!lock.enabled || lock.requiresConfirm) return;
    onFinish();
  }, [lock.enabled, lock.requiresConfirm, onFinish]);

  // Keyed on which capture it is, not on the object carrying it: a parent
  // re-creating the same record must not replay its arrival.
  const captureKey = latest ? `${latest.filename}|${latest.timestamp}` : null;
  const source = latest ? latest.rawUrl ?? latest.url : null;
  // The original photograph, not the annotated copy: the boxes are the
  // detector's opinion, and the assessment is of the scene.
  const capture = captureKey !== null && source !== null ? { key: captureKey, src: source } : null;

  // The photograph forms out of the grid from the capture to touchdown, on the
  // flight's own progress over that leg. The panel settles -- the photograph
  // rises, its time of capture appears -- once the picture is whole.
  const [revealFrom, setRevealFrom] = useState<{ key: string; from: number } | null>(null);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  useEffect(() => {
    if (captureKey === null) {
      setRevealFrom(null);
      setRevealedKey(null);
      return;
    }
    setRevealFrom((previous) => (previous?.key === captureKey ? previous : { key: captureKey, from: flightProgress ?? 0 }));
    // Only the capture changing starts a new reveal; the progress moving does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [captureKey]);
  const revealFromHere = revealFrom !== null && revealFrom.key === captureKey ? revealFrom.from : null;
  const reveal =
    revealFromHere !== null && flightProgress !== undefined && revealFromHere < 1
      ? (flightProgress - revealFromHere) / (1 - revealFromHere)
      : 0;
  const onRevealed = useCallback(() => setRevealedKey(captureKey), [captureKey]);
  const settled = captureKey !== null && revealedKey === captureKey;

  const inspecting = stage === 4;
  const announced = Math.max(reportAnnounced ?? reportRevealed, reportRevealed);
  const onGround = landed || missionOver;
  const down = settled && onGround;
  const scanningInFlight = settled && !onGround && Boolean(latest);
  const presenting = down && Boolean(latest) && Boolean(report);
  const analyzing = down && Boolean(latest) && !report && reportAnalyzing;

  // Only when the mission may be ended: never in flight, never with nothing to end.
  const finishShown = lock.enabled;

  return (
    <section className="flex min-h-0 flex-col">
      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel bg-hull-deep">
        <div className="flex min-h-0 flex-1 flex-col gap-2.5 p-3">
          <div
            data-evidence-frame
            className={cn(
              'relative block w-full shrink-0 overflow-hidden rounded-bezel',
              'transition-all duration-[600ms] ease-settle',
              // On touchdown the frame yields the column to the findings.
              presenting || analyzing ? 'h-[38%]' : 'h-full'
            )}
          >
            <EvidenceGrid
              waitProgress={waitProgress}
              capture={capture}
              reveal={reveal}
              closing={onGround}
              inspecting={inspecting}
              onRevealed={onRevealed}
            />
              {scanningInFlight ? (
                <div data-scan-sweep aria-hidden className="evgrid-scan">
                  <span className="evgrid-scan-beam" />
                </div>
              ) : null}
              {/* The photograph is revealed immediately on capture; once the aircraft lands the scan ends and the capture stamp appears. */}
              {down && latest ? (
                <>
                  {FRAME_MARKS.map((position) => (
                    <span
                      key={position}
                      aria-hidden
                      className={cn('anim-rise pointer-events-none absolute h-2.5 w-2.5 border-mint/60', position)}
                    />
                  ))}
                  <div
                    data-capture-stamp
                    className="anim-rise absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 bg-gradient-to-t from-black/80 to-transparent px-4 pb-2.5 pt-6"
                  >
                    <div className="min-w-0 text-left">
                      <div className="hud-legible font-cond text-3xs tracking-wide text-frost/55">
                        capturada em
                      </div>
                      <div className="hud-legible truncate font-mono text-xs text-frost">
                        {stampLabel(stampOf(latest))}
                      </div>
                    </div>
                  </div>
                </>
              ) : null}
          </div>

          {/* The findings, in the space the image gave up. */}
          {presenting && report ? (
            <ul aria-live="polite" className={cn('scroll-thin min-h-0 flex-1 overflow-y-auto pr-0.5', finishShown && 'pb-12')}>
              {report.map((finding, index) => {
                const shown = index < announced;
                const reading = reportReadings[index];
                return (
                  <li
                    key={finding.topic}
                    className={cn(
                      'mb-2 flex items-center gap-3 rounded-bezel border px-3.5 py-3 transition-all duration-500 ease-settle',
                      shown
                        ? 'finding-shown anim-finding-reveal translate-y-0 border-mint/55 bg-mint/[0.09] opacity-100 shadow-[inset_3px_0_0_#12B382]'
                        : 'pointer-events-none translate-y-2 border-strut-soft bg-abyss/30 opacity-0'
                    )}
                  >
                    <FindingBadge topic={finding.topic} />
                    <span className="min-w-0 flex-1">
                      <span className="title-pixel block font-pixel text-[13px] font-semibold uppercase tracking-[0.16em] text-mint-bright">
                        {TOPIC_LABEL[finding.topic]}
                      </span>
                      <span className="mt-0.5 block text-[15px] font-medium leading-snug text-frost">
                        {shown && reading ? <TypewriterText text={finding.card} endsAt={reading.endsAt} /> : finding.card}
                      </span>
                    </span>
                  </li>
                );
              })}
              {reportRevealed >= report.length ? (
                <li className="anim-rise px-1 pt-1 text-2xs text-haze">
                  {reportClosing ?? PHRASE_POOLS['inspection.outro'][0]}
                </li>
              ) : null}
            </ul>
          ) : analyzing ? (
            <ul aria-live="polite" aria-busy="true" className="min-h-0 flex-1 pr-0.5">
              <li data-finding-state="analyzing" className="mb-3 flex items-start gap-3 pl-1">
                <ScanSearch size={11} strokeWidth={2.5} className="mt-[3px] shrink-0 text-mint/70 anim-breathe" aria-hidden />
                <span className="min-w-0">
                  <span className="title-pixel font-pixel text-2xs uppercase tracking-[0.12em] text-mint/60">
                    {TOPIC_LABEL.victim}
                  </span>
                  <span className="mt-0.5 block text-[15px] font-medium leading-snug text-haze anim-breathe">
                    Analisando imagem
                  </span>
                </span>
              </li>
            </ul>
          ) : null}
        </div>

        {finishShown ? (
          <button
            type="button"
            onClick={click}
            onPointerDown={startHold}
            onPointerUp={cancelHold}
            onPointerLeave={cancelHold}
            onPointerCancel={cancelHold}
            data-finish-state={lock.state}
            data-finish-reason={lock.reason || undefined}
            className={cn(
              'finish-rise font-pixel absolute bottom-3 right-3 z-30 flex items-center gap-2 rounded-bezel border bg-hull-deep/85 px-4 py-2 text-sm font-semibold tracking-[0.04em] backdrop-blur-sm transition-colors',
              lock.requiresConfirm
                ? cn('border-ember/70 text-ember', holding && 'bg-ember/40')
                : missionOver
                ? 'border-mint/80 text-mint hover:bg-mint hover:text-abyss'
                : 'border-ember/70 text-ember hover:bg-ember hover:text-abyss'
            )}
          >
            <Square size={12} strokeWidth={2.5} fill="currentColor" />
            {lock.requiresConfirm ? 'Segure para finalizar' : 'Finalizar missão'}
          </button>
        ) : null}
      </div>
    </section>
  );
};
