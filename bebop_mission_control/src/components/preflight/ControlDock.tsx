import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { cn } from '../../lib/format';
import { AbortControl } from '../cockpit/AbortControl';

interface ControlDockProps {
  /** The launch sequence is running: the pair fuses into the abort. */
  fused: boolean;
  onAbort: () => void;
  parameters: React.ReactNode;
  mode: React.ReactNode;
}

/** Where the dock is in its fusion. `fusing` and `splitting` run for {@link FUSE_MS} and {@link SPLIT_MS}. */
export type DockPhase = 'idle' | 'fusing' | 'fused' | 'splitting';

/** Length of the fusion: the pills join, the red spreads from the seam, the abort appears. */
export const FUSE_MS = 1400;
/** Length of the reverse: the abort goes, the red withdraws to the seam, the pills part. */
export const SPLIT_MS = 1200;

/** Keep the focusable descendants of a slot out of the tab order while it is hidden. */
function useInertSlot(ref: React.RefObject<HTMLDivElement>, inert: boolean) {
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (inert) node.setAttribute('inert', '');
    else node.removeAttribute('inert');
    for (const control of Array.from(node.querySelectorAll<HTMLElement>('button, input, select, [tabindex]'))) {
      if (inert) {
        if (!control.hasAttribute('data-dock-tabindex')) {
          control.setAttribute('data-dock-tabindex', control.getAttribute('tabindex') ?? '');
        }
        control.tabIndex = -1;
      } else if (control.hasAttribute('data-dock-tabindex')) {
        const previous = control.getAttribute('data-dock-tabindex');
        if (previous) control.setAttribute('tabindex', previous);
        else control.removeAttribute('tabindex');
        control.removeAttribute('data-dock-tabindex');
      }
    }
  }, [ref, inert]);
}

/**
 * Parâmetros and the flight mode, side by side under the dial, fusing into the
 * cockpit's own "Abortar Missão" while the launch is prepared.
 *
 * The fusion: the labels blur out toward the middle, the two pills reach for
 * each other and join through a liquid neck, then the red spreads from the
 * seam to both ends and the abort settles over it at the footprint of the
 * pair. The liquid is two blobs under an SVG threshold filter ("gooey"),
 * shown only while the dock changes. Cancelling the launch -- the button, or
 * the abort keys (`useLaunchAbortShortcut`) -- clears `fused`, which plays it
 * backwards: the abort goes, the red withdraws to the seam, the pills part.
 * Driven by `fused` alone, so a cancel from anywhere looks the same.
 *
 * While not split the slots are `inert`, hidden from assistive technology and
 * out of the tab order; the abort is the only control, and only while fused.
 */
export const ControlDock: React.FC<ControlDockProps> = ({ fused, onAbort, parameters, mode }) => {
  const parametersRef = useRef<HTMLDivElement>(null);
  const modeRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<HTMLDivElement>(null);
  const filterId = `dock-goo-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const [phase, setPhase] = useState<DockPhase>(fused ? 'fused' : 'idle');

  useEffect(() => {
    setPhase((previous) => {
      if (fused) return previous === 'fused' ? previous : 'fusing';
      return previous === 'idle' ? previous : 'splitting';
    });
  }, [fused]);

  useEffect(() => {
    if (phase !== 'fusing' && phase !== 'splitting') return;
    const timer = window.setTimeout(
      () => setPhase(phase === 'fusing' ? 'fused' : 'idle'),
      phase === 'fusing' ? FUSE_MS : SPLIT_MS
    );
    return () => window.clearTimeout(timer);
  }, [phase]);

  const split = phase === 'idle';
  useInertSlot(parametersRef, !split);
  useInertSlot(modeRef, !split);
  // Inert rather than disabled: a disabled abort turns grey, and it is still on
  // screen, fading, while the pair splits.
  useInertSlot(abortRef, !fused);

  return (
    <div
      data-fused={fused ? 'true' : 'false'}
      data-phase={phase}
      className="dock-fuse dock-motion"
      style={{ width: 'calc(var(--pair-w) * 2 + 12px)' }}
    >
      <svg width="0" height="0" aria-hidden className="absolute">
        <filter id={filterId}>
          <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
          <feColorMatrix in="blur" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 20 -8" />
        </filter>
      </svg>
      <div aria-hidden className="dock-fuse-goo" style={{ filter: `url(#${filterId})` }}>
        <span className="dock-fuse-base dock-fuse-base-l" />
        <span className="dock-fuse-base dock-fuse-base-r" />
        <span className="dock-fuse-red" />
      </div>
      <div ref={parametersRef} data-dock-slot="parameters" aria-hidden={!split || undefined} className="dock-fuse-slot dock-fuse-slot-l">
        {parameters}
      </div>
      <div ref={modeRef} data-dock-slot="mode" aria-hidden={!split || undefined} className="dock-fuse-slot dock-fuse-slot-r">
        {mode}
      </div>
      <div ref={abortRef} aria-hidden={fused ? undefined : true} className={cn('dock-fuse-abort', !fused && 'pointer-events-none')}>
        <AbortControl onAbort={onAbort} fill />
      </div>
    </div>
  );
};
