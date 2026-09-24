import React, { useEffect, useState } from 'react';
import { Maximize2, ShieldCheck, Square } from 'lucide-react';
import type { RawEvidence } from '../../types/bmg';
import type { Finding } from '../../lib/forensics';
import { TOPIC_LABEL } from '../../lib/forensics';
import { cn, stampLabel } from '../../lib/format';

interface ForensicPanelProps {
  latest: RawEvidence | null;
  count: number;
  /** 1–5. The nadir capture happens in stage 4. */
  stage: number;
  nadirTilt: number;
  missionOver: boolean;
  /** The aircraft is down from a flight that completed: the report may be read. */
  landed: boolean;
  /** This flight's findings, in this flight's order. */
  report: Finding[] | null;
  /** How many of them the copilot has read so far. */
  reportRevealed: number;
  onOpenLibrary: () => void;
  onFinish: () => void;
}

/** One HUD corner bracket. */
const Corner: React.FC<{ position: 'tl' | 'tr' | 'bl' | 'br'; live: boolean }> = ({ position, live }) => (
  <span
    aria-hidden
    className={cn(
      'pointer-events-none absolute h-7 w-7 transition-colors duration-500',
      live ? 'border-mint' : 'border-mint/60',
      position === 'tl' && 'left-2 top-2 border-l-2 border-t-2',
      position === 'tr' && 'right-2 top-2 border-r-2 border-t-2',
      position === 'bl' && 'bottom-2 left-2 border-b-2 border-l-2',
      position === 'br' && 'bottom-2 right-2 border-b-2 border-r-2'
    )}
  />
);

/**
 * The evidence wall before the capture: an optical HUD on standby.
 *
 * Corner brackets, a technical grid, a scan line and a reticle make it read as
 * the sensor it is waiting on rather than as an empty box. One sentence and
 * nothing else — the operator needs to know what arrives here, not a paragraph
 * about how.
 */
const EvidenceStandby: React.FC<{ inspecting: boolean; stage: number; nadirTilt: number }> = ({
  inspecting,
  stage,
  nadirTilt,
}) => (
  <div className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-bezel border border-strut-soft bg-abyss/70">
    {/* Technical grid: fine lattice under a coarse rule. */}
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 opacity-70"
      style={{
        backgroundImage:
          'linear-gradient(rgba(1,213,163,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(1,213,163,0.07) 1px, transparent 1px), linear-gradient(rgba(124,153,164,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(124,153,164,0.05) 1px, transparent 1px)',
        backgroundSize: '64px 64px, 64px 64px, 16px 16px, 16px 16px',
        backgroundPosition: 'center center',
      }}
    />
    {/* Vignette, so the centre carries the eye. */}
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0"
      style={{ background: 'radial-gradient(60% 60% at 50% 50%, transparent 40%, rgba(0,19,31,0.85) 100%)' }}
    />
    {/* Scan line. */}
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="anim-scan absolute inset-x-0 top-0 h-full"
        style={{
          background:
            'linear-gradient(180deg, transparent 0%, transparent 88%, rgba(1,213,163,0.07) 97%, rgba(92,242,206,0.35) 99.6%, transparent 100%)',
        }}
      />
    </div>

    <Corner position="tl" live={inspecting} />
    <Corner position="tr" live={inspecting} />
    <Corner position="bl" live={inspecting} />
    <Corner position="br" live={inspecting} />

    {/* Sensor status, top edge. */}
    <div className="pointer-events-none absolute inset-x-11 top-3 flex items-center justify-between font-mono text-3xs tracking-[0.14em]">
      <span className={cn('flex items-center gap-1.5', inspecting ? 'text-mint' : 'text-mint/70')}>
        <span className={cn('h-1.5 w-1.5 rounded-full', inspecting ? 'bg-mint anim-breathe' : 'bg-mint/50')} />
        SENSOR RAW 14MP // {inspecting ? 'ARMED' : 'STANDBY'}
      </span>
      <span className="text-haze-deep">EO-CAM · NADIR {nadirTilt.toFixed(0)}°</span>
    </div>

    {/* Readouts, bottom edge. */}
    <div className="pointer-events-none absolute inset-x-11 bottom-3 flex items-center justify-between font-mono text-3xs tracking-[0.14em] text-haze-deep">
      <span>ETAPA {stage > 0 ? stage : '—'}/5</span>
      <span>PNG LOSSLESS · JPG ANOTADO</span>
      <span>{inspecting ? 'CAPTURA IMINENTE' : 'AGUARDANDO'}</span>
    </div>

    {/* Reticle. */}
    <div className="relative flex flex-col items-center gap-5">
      <div className="relative grid h-32 w-32 place-items-center">
        <svg viewBox="0 0 128 128" className="absolute inset-0 h-full w-full" aria-hidden>
          <circle cx="64" cy="64" r="60" fill="none" stroke="rgba(1,213,163,0.25)" strokeWidth="1" />
          <circle
            cx="64"
            cy="64"
            r="46"
            fill="none"
            stroke={inspecting ? '#01D5A3' : 'rgba(1,213,163,0.55)'}
            strokeWidth="1.5"
            strokeDasharray="6 5"
          />
          {[0, 90, 180, 270].map((deg) => (
            <line
              key={deg}
              x1="64"
              y1="2"
              x2="64"
              y2="16"
              stroke={inspecting ? '#5CF2CE' : 'rgba(92,242,206,0.7)'}
              strokeWidth="1.5"
              transform={`rotate(${deg} 64 64)`}
            />
          ))}
          <line x1="44" y1="64" x2="58" y2="64" stroke="#5CF2CE" strokeWidth="1.2" />
          <line x1="70" y1="64" x2="84" y2="64" stroke="#5CF2CE" strokeWidth="1.2" />
          <line x1="64" y1="44" x2="64" y2="58" stroke="#5CF2CE" strokeWidth="1.2" />
          <line x1="64" y1="70" x2="64" y2="84" stroke="#5CF2CE" strokeWidth="1.2" />
          <circle cx="64" cy="64" r="2" fill="#5CF2CE" />
        </svg>
        <svg viewBox="0 0 128 128" className="anim-spin-slow absolute inset-0 h-full w-full" aria-hidden>
          <path
            d="M 64 4 A 60 60 0 0 1 124 64"
            fill="none"
            stroke={inspecting ? '#5CF2CE' : 'rgba(92,242,206,0.55)'}
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </div>
      <p
        className={cn(
          'hud-legible font-mono text-base tracking-[0.12em]',
          inspecting ? 'text-mint anim-breathe' : 'text-frost/90'
        )}
      >
        Aguardo foto da evidencia
      </p>
    </div>
  </div>
);

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
  nadirTilt,
  missionOver,
  landed,
  report,
  reportRevealed,
  onOpenLibrary,
  onFinish,
}) => {
  const [arrived, setArrived] = useState(false);

  useEffect(() => {
    if (!latest) return;
    setArrived(false);
    const id = window.requestAnimationFrame(() => setArrived(true));
    return () => window.cancelAnimationFrame(id);
  }, [latest]);

  const inspecting = stage === 4;
  const presenting = (landed || missionOver) && Boolean(latest) && Boolean(report);

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
              {presenting ? 'Laudo preliminar de inspeção' : 'Painel forense de evidências'}
            </h2>
          </span>
          <span className="font-cond text-3xs tracking-wide text-haze-deep">
            {presenting && report
              ? `${reportRevealed}/${report.length} constatações`
              : latest
              ? `${count} ${count === 1 ? 'captura nesta missão' : 'capturas nesta missão'}`
              : 'aguardando sobrevoo nadir'}
          </span>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-2.5 p-3">
          {latest ? (
            <button
              type="button"
              onClick={onOpenLibrary}
              title="Abrir no dossiê"
              className={cn(
                'group relative block w-full shrink-0 overflow-hidden rounded-bezel border border-strut',
                'transition-all duration-[900ms] ease-settle hover:border-mint/60',
                // On touchdown the frame yields the column to the findings.
                presenting ? 'h-[38%]' : 'h-full',
                arrived && 'anim-land'
              )}
            >
              <img
                // The original photograph, not the annotated copy: the boxes are
                // the detector's opinion, and the assessment is of the scene.
                src={latest.rawUrl ?? latest.url}
                alt="Captura pericial da visada nadir"
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
                <span className="hud-legible flex shrink-0 items-center gap-1.5 text-2xs text-mint">
                  <Maximize2 size={11} strokeWidth={2} />
                  Abrir dossiê
                </span>
              </div>
            </button>
          ) : (
            <EvidenceStandby inspecting={inspecting} stage={stage} nadirTilt={nadirTilt} />
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
                      'mb-2 flex items-start gap-2.5 rounded-bezel border px-3 py-2.5 transition-all duration-500 ease-settle',
                      shown
                        ? 'translate-y-0 border-mint/40 bg-mint/[0.06] opacity-100'
                        : 'pointer-events-none translate-y-2 border-strut-soft bg-abyss/30 opacity-0'
                    )}
                  >
                    <span
                      className={cn(
                        'mt-[2px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
                        shown ? 'border-mint/60 bg-mint/15' : 'border-strut'
                      )}
                    >
                      <ShieldCheck
                        size={9}
                        strokeWidth={2.5}
                        className={shown ? 'text-mint' : 'text-haze-deep'}
                        aria-hidden
                      />
                    </span>
                    <span className="min-w-0">
                      <span className="block font-cond text-3xs tracking-wide text-haze-deep">
                        {TOPIC_LABEL[finding.topic]}
                      </span>
                      <span className="block text-sm leading-snug text-frost">{finding.card}</span>
                    </span>
                  </li>
                );
              })}
              {reportRevealed >= report.length ? (
                <li className="anim-rise px-1 pt-1 text-2xs text-haze">
                  Relatório pericial emitido e pronto para exportação.
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        <button
          type="button"
          onClick={onFinish}
          className={cn(
            'ml-auto flex items-center gap-2 rounded-bezel border px-4 py-2 text-sm font-semibold transition-colors',
            missionOver
              ? 'border-mint bg-mint text-abyss hover:bg-mint-bright'
              : 'border-ember/70 bg-ember/15 text-ember hover:bg-ember hover:text-abyss'
          )}
        >
          <Square size={12} strokeWidth={2.5} fill="currentColor" />
          Finalizar missão
        </button>
      </div>
    </section>
  );
};
