import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, FileText, Maximize2, Minimize2, ScanSearch, ShieldCheck, Square, X } from 'lucide-react';
import type { RawEvidence } from '../../types/bmg';
import { useBridge } from '../../hooks/useBridge';
import type { Finding } from '../../lib/forensics';
import { TOPIC_LABEL } from '../../lib/forensics';
import { PHRASE_POOLS } from '../../lib/copilotPhrases';
import { cn, stampLabel } from '../../lib/format';
import { FINISH_HOLD_MS, type FinishLockResult } from '../../lib/finishLock';

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

/** One corner of the autofocus frame, as an L drawn from its outer point. */
const FOCUS_CORNERS: readonly string[] = [
  'M 18 38 L 18 18 L 38 18',
  'M 90 18 L 110 18 L 110 38',
  'M 110 90 L 110 110 L 90 110',
  'M 38 110 L 18 110 L 18 90',
];

/**
 * The evidence wall before the capture: a camera finding its focus.
 *
 * One gesture instead of the four the panel used to layer (grid, vignette,
 * static reticle, turning arc): an autofocus frame that hunts, closing and
 * opening around the camera glyph. While the aircraft is over the target
 * (`inspecting`) the frame locks: it sits tighter, brightens and beats
 * faster, which is the moment the capture is about to happen. It stops under
 * `prefers-reduced-motion`.
 */
const EvidenceStandby: React.FC<{ inspecting: boolean }> = ({ inspecting }) => (
  <div
    data-focus-state={inspecting ? 'locking' : 'hunting'}
    className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-bezel border border-strut-soft bg-abyss/70"
  >
    <div className="relative flex flex-col items-center gap-5">
      <div className="relative grid h-32 w-32 place-items-center">
        <svg viewBox="0 0 128 128" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden>
          <g
            fill="none"
            stroke={inspecting ? '#5CF2CE' : 'rgba(1,213,163,0.75)'}
            strokeWidth={inspecting ? 2.4 : 2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className={cn(inspecting ? 'anim-focus-lock' : 'anim-focus-hunt')}
          >
            {FOCUS_CORNERS.map((d) => (
              <path key={d} d={d} />
            ))}
          </g>
        </svg>
        <Camera
          size={36}
          strokeWidth={1.5}
          aria-hidden
          className={cn('relative', inspecting ? 'text-mint anim-breathe' : 'text-mint/80')}
        />
      </div>
      <p
        className={cn(
          'hud-legible text-base tracking-wide',
          inspecting ? 'text-mint anim-breathe' : 'text-frost/90'
        )}
      >
        Aguardando foto da evidência
      </p>
    </div>
  </div>
);

/** Pull `YYYYMMDD_HHMMSS` back out of the filename the mission wrote. */
function stampOf(evidence: RawEvidence): string {
  return evidence.filename.replace(/^accident_(raw|inspected)_/, '').replace(/\.(png|jpg)$/, '');
}

/** How often the open report modal asks the host whether the report has landed. */
export const REPORT_POLL_MS = 3000;

/** The agent's report name for a capture without a valid identifier (`docs/PROJECT.md` §6). */
export const REPORT_WITHOUT_IDENTIFIER = 'relatorio_inspecao_sem_identificador.html';

/**
 * Where the inference agent's report for this capture is served once written.
 *
 * The name is the one the mission recorded in the inspection package; without
 * a package it follows the agent's rule. The identifier is never rebuilt from
 * the file stamp, which is local time and would not match the UTC id.
 */
export function expectedReportUrl(evidence: RawEvidence): string {
  const name =
    evidence.inferencePackage?.expected_report ??
    (evidence.occurrenceId ? `relatorio_${evidence.occurrenceId}.html` : REPORT_WITHOUT_IDENTIFIER);
  return `/evidence/${name}`;
}

interface ReportModalProps {
  /** Null when the capture carries no identifier. */
  occurrenceId: string | null;
  /** Null until the report is on disk. */
  reportUrl: string | null;
  onClose: () => void;
}

/**
 * The inference agent's report, inside the cockpit.
 *
 * It never leaves the station's window: no new tab, no system browser (the
 * host refuses every `window.open`). Expanding fills the window instead.
 *
 * The report is HTML written by an external agent and served from the
 * cockpit's own origin, so the frame is sandboxed without `allow-same-origin`:
 * with both that and `allow-scripts` a same-origin document can lift its own
 * sandbox and reach `window.parent.bmgAPI`. The report's scripts still run,
 * under an opaque origin; it is self-contained, the photograph embedded.
 */
export const ReportModal: React.FC<ReportModalProps> = ({ occurrenceId, reportUrl, onClose }) => {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className={cn('fixed inset-0 z-[70] flex items-center justify-center', expanded ? 'p-0' : 'p-6')}
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-modal-title"
    >
      <div aria-hidden className="absolute inset-0 bg-abyss/85 backdrop-blur-md" onClick={onClose} />
      <div
        data-report-size={expanded ? 'expanded' : 'framed'}
        className={cn(
          'anim-rise relative flex w-full flex-col overflow-hidden border border-strut bg-hull-deep shadow-2xl',
          expanded ? 'h-full rounded-none' : 'h-full max-h-[92vh] max-w-6xl rounded-panel'
        )}
      >
        <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-strut-soft px-4">
          <span className="flex min-w-0 items-center gap-3">
            <FileText size={16} strokeWidth={2} className="shrink-0 text-mint" aria-hidden />
            <h2 id="report-modal-title" className="truncate font-cond text-sm tracking-wide text-frost">
              Laudo Pericial — Inspeção Aérea Autônoma
            </h2>
            <span
              data-testid="report-occurrence-id"
              className="shrink-0 rounded-full border border-mint/50 bg-mint/10 px-2.5 py-0.5 font-mono text-2xs text-mint-bright"
            >
              {occurrenceId ?? 'sem identificador'}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              data-testid="report-expand"
              title={expanded ? 'Restaurar' : 'Expandir'}
              aria-label={expanded ? 'Restaurar tamanho do laudo' : 'Expandir laudo'}
              aria-pressed={expanded}
              className="flex h-8 w-8 items-center justify-center rounded-bezel border border-strut text-haze transition-colors hover:border-mint/60 hover:text-mint-bright"
            >
              {expanded ? (
                <Minimize2 size={14} strokeWidth={2} aria-hidden />
              ) : (
                <Maximize2 size={14} strokeWidth={2} aria-hidden />
              )}
            </button>
            <button
              type="button"
              onClick={onClose}
              title="Fechar"
              aria-label="Fechar laudo"
              className="flex h-8 w-8 items-center justify-center rounded-bezel border border-strut text-haze transition-colors hover:border-mint/60 hover:text-frost"
            >
              <X size={15} strokeWidth={2} aria-hidden />
            </button>
          </span>
        </header>

        <div className="min-h-0 flex-1 p-3">
          {reportUrl ? (
            <iframe
              src={reportUrl}
              className="h-full w-full rounded-bezel border border-strut bg-hull-deep"
              sandbox="allow-scripts"
              title="Relatório de Inspeção"
            />
          ) : (
            <div
              data-testid="report-waiting"
              className="flex h-full w-full flex-col items-center justify-center gap-4 rounded-bezel border border-strut-soft bg-abyss/60"
            >
              <FileText size={36} strokeWidth={1.5} className="anim-breathe text-mint" aria-hidden />
              <p className="hud-legible text-base tracking-wide text-frost/90">
                Aguardando emissão do laudo pelo agente de inferência visual...
              </p>
              <p className="font-mono text-2xs text-haze-deep">
                verificação automática a cada {REPORT_POLL_MS / 1000} s
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

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
  reportAnalyzing = false,
  reportClosing,
  onFinish,
  lock,
}) => {
  const [arrived, setArrived] = useState(false);
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

  useEffect(() => {
    if (!latest) return;
    setArrived(false);
    const id = window.requestAnimationFrame(() => setArrived(true));
    return () => window.cancelAnimationFrame(id);
  }, [latest]);

  const inspecting = stage === 4;
  const presenting = (landed || missionOver) && Boolean(latest) && Boolean(report);
  const analyzing = (landed || missionOver) && Boolean(latest) && !report && reportAnalyzing;
  const allTopicsSpoken = presenting && report !== null && reportRevealed >= report.length;

  const bridge = useBridge();
  const occurrenceId = latest?.occurrenceId ?? null;
  const expectedUrl = latest ? expectedReportUrl(latest) : null;
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [isModalOpen, setModalOpen] = useState(false);
  const closeModal = useCallback(() => setModalOpen(false), []);

  // Only this capture's own report is accepted, polled or announced: the host
  // falls back to the newest report on disk, which may belong to an earlier
  // flight.
  const resolveReport = useCallback(async () => {
    if (!bridge || !expectedUrl) return;
    try {
      const url = await bridge.getReportUrl(occurrenceId ?? undefined);
      if (url === expectedUrl) setReportUrl(url);
    } catch {
      // The host is unreachable; the next poll or `bmg:report-ready` retries.
    }
  }, [bridge, expectedUrl, occurrenceId]);

  useEffect(() => {
    setReportUrl(null);
    void resolveReport();
  }, [resolveReport]);

  useEffect(() => {
    if (!bridge || !expectedUrl) return;
    return bridge.onReportReady((event) => {
      if (event.url === expectedUrl) setReportUrl(event.url);
    });
  }, [bridge, expectedUrl]);

  useEffect(() => {
    if (!isModalOpen || reportUrl) return;
    const id = window.setInterval(() => void resolveReport(), REPORT_POLL_MS);
    return () => window.clearInterval(id);
  }, [isModalOpen, reportUrl, resolveReport]);

  return (
    <section className="flex min-h-0 flex-col gap-2.5">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-panel border border-strut-soft bg-hull-deep/80">
        <header className="flex h-10 shrink-0 items-center justify-between gap-3 border-b border-strut-soft px-3.5">
          <span className="flex items-center gap-2">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                latest ? 'bg-mint' : inspecting ? 'bg-mint anim-breathe' : 'bg-haze-deep'
              )}
            />
            <h2 className="font-cond text-sm tracking-wide text-frost">
              {presenting || analyzing ? 'Laudo preliminar de inspeção' : 'Painel forense de evidências'}
            </h2>
          </span>
          <span className="font-cond text-3xs tracking-wide text-haze-deep">
            {presenting && report
              ? `${reportRevealed}/${report.length} constatações`
              : analyzing
              ? 'analisando imagem'
              : latest
              ? `${count} ${count === 1 ? 'captura nesta missão' : 'capturas nesta missão'}`
              : 'aguardando câmera na posição vertical'}
          </span>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-2.5 p-3">
          {latest ? (
            <div
              className={cn(
                'relative block w-full shrink-0 overflow-hidden rounded-bezel border border-strut',
                'transition-all duration-[900ms] ease-settle',
                // On touchdown the frame yields the column to the findings.
                presenting || analyzing ? 'h-[38%]' : 'h-full',
                arrived && 'anim-land'
              )}
            >
              <img
                // The original photograph, not the annotated copy: the boxes are
                // the detector's opinion, and the assessment is of the scene.
                src={latest.rawUrl ?? latest.url}
                alt="Captura pericial com a câmera na posição vertical"
                className="h-full w-full object-contain"
              />
              <div className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 bg-gradient-to-t from-black/90 to-transparent px-4 pb-3 pt-10">
                <div className="min-w-0 text-left">
                  <div className="hud-legible font-cond text-3xs tracking-wide text-frost/55">
                    capturada em
                  </div>
                  <div className="hud-legible truncate font-mono text-xs text-frost">
                    {stampLabel(stampOf(latest))}
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <EvidenceStandby inspecting={inspecting} />
          )}

          {/* The findings, in the space the image gave up. */}
          {presenting && report ? (
            <ul aria-live="polite" className="scroll-thin min-h-0 flex-1 overflow-y-auto pr-0.5">
              {report.map((finding, index) => {
                const shown = index < reportRevealed;
                return (
                  <li
                    key={finding.topic}
                    className={cn(
                      'mb-2 flex items-start gap-3 rounded-bezel border px-3.5 py-3 transition-all duration-500 ease-settle',
                      shown
                        ? 'anim-finding-reveal translate-y-0 border-mint/55 bg-mint/[0.09] opacity-100 shadow-[inset_3px_0_0_#01D5A3]'
                        : 'pointer-events-none translate-y-2 border-strut-soft bg-abyss/30 opacity-0'
                    )}
                  >
                    <span
                      className={cn(
                        'mt-[1px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full border',
                        shown ? 'border-mint/70 bg-mint/20' : 'border-strut'
                      )}
                    >
                      <ShieldCheck
                        size={11}
                        strokeWidth={2.5}
                        className={shown ? 'text-mint-bright' : 'text-haze-deep'}
                        aria-hidden
                      />
                    </span>
                    <span className="min-w-0">
                      <span className="flex items-baseline gap-1.5 font-cond text-2xs uppercase tracking-[0.12em] text-mint/85">
                        <span className="tnum font-mono">{String(index + 1).padStart(2, '0')}</span>
                        <span aria-hidden className="text-mint/40">·</span>
                        <span>{TOPIC_LABEL[finding.topic]}</span>
                      </span>
                      <span className="mt-0.5 block text-[15px] font-medium leading-snug text-frost">
                        {finding.card}
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
              <li
                data-finding-state="analyzing"
                className="mb-2 flex items-start gap-3 rounded-bezel border border-strut-soft bg-abyss/30 px-3.5 py-3"
              >
                <span className="mt-[1px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-strut">
                  <ScanSearch size={11} strokeWidth={2.5} className="text-mint/70 anim-breathe" aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="font-cond text-2xs uppercase tracking-[0.12em] text-mint/60">
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
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        {allTopicsSpoken ? (
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            data-testid="open-report"
            className="anim-rise flex items-center gap-2 rounded-bezel border border-mint/70 bg-mint/10 px-4 py-2 text-sm font-semibold text-mint-bright transition-colors hover:bg-mint/20"
          >
            <FileText size={14} strokeWidth={2.25} aria-hidden />
            Visualizar Laudo Pericial
          </button>
        ) : null}
        <button
          type="button"
          onClick={click}
          onPointerDown={startHold}
          onPointerUp={cancelHold}
          onPointerLeave={cancelHold}
          onPointerCancel={cancelHold}
          disabled={!lock.enabled}
          aria-disabled={!lock.enabled}
          data-finish-state={lock.state}
          title={lock.reason || undefined}
          className={cn(
            'ml-auto flex items-center gap-2 rounded-bezel border px-4 py-2 text-sm font-semibold transition-colors',
            !lock.enabled
              ? 'cursor-not-allowed border-strut bg-hull text-haze-deep'
              : lock.requiresConfirm
              ? cn('border-ember/70 bg-ember/15 text-ember', holding && 'bg-ember/40')
              : missionOver
              ? 'border-mint bg-mint text-abyss hover:bg-mint-bright'
              : 'border-ember/70 bg-ember/15 text-ember hover:bg-ember hover:text-abyss'
          )}
        >
          <Square size={12} strokeWidth={2.5} fill="currentColor" />
          {lock.requiresConfirm ? 'Segure para finalizar' : 'Finalizar missão'}
        </button>
      </div>

      {isModalOpen && latest ? (
        <ReportModal occurrenceId={occurrenceId} reportUrl={reportUrl} onClose={closeModal} />
      ) : null}
    </section>
  );
};
