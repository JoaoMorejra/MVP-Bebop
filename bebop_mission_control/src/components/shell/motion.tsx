import React, { useEffect, useRef, useState } from 'react';
import { SCRAMBLE_FRAME_MS, scrambleFrame, scrambleRevealFrames } from '../../lib/trayState';
import { cn } from '../../lib/format';
import { Wordmark } from '../brand/Wordmark';

/** Whether the operator asked the system for less motion. Read once per effect. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

/**
 * A name that churns while what it names is loading, and resolves left to
 * right the moment it is up (`lib/trayState.ts:scrambleFrame`). Mounted already
 * loaded, it shows the plain name with no reveal. Screen readers get the name
 * only.
 */
export const ScrambleText: React.FC<{ text: string; scrambling: boolean; className?: string }> = ({
  text,
  scrambling,
  className,
}) => {
  const [shown, setShown] = useState(text);
  const wasScrambling = useRef(false);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(text);
      wasScrambling.current = scrambling;
      return;
    }
    let id: number | undefined;
    if (scrambling) {
      wasScrambling.current = true;
      setShown(scrambleFrame(text, null));
      id = window.setInterval(() => setShown(scrambleFrame(text, null)), SCRAMBLE_FRAME_MS);
    } else if (wasScrambling.current) {
      wasScrambling.current = false;
      const end = scrambleRevealFrames(text);
      let frame = 0;
      id = window.setInterval(() => {
        frame += 1;
        if (frame >= end) {
          window.clearInterval(id);
          setShown(text);
        } else {
          setShown(scrambleFrame(text, frame));
        }
      }, SCRAMBLE_FRAME_MS);
    } else {
      setShown(text);
    }
    return () => window.clearInterval(id);
  }, [text, scrambling]);

  return (
    <span className={className} data-scrambling={scrambling || undefined}>
      <span aria-hidden>{shown}</span>
      <span className="sr-only">{text}</span>
    </span>
  );
};

/**
 * The confirmation tick: fades in rotating from 80 degrees, out of a blur, with
 * a small bob, while its stroke draws (transitions.dev "Success check"). It
 * plays once, on mount; remount it (a new `key`) to play it again.
 */
export const SuccessCheck: React.FC<{ size?: number; className?: string }> = ({ size = 14, className }) => (
  <span
    className={cn('t-success-check', className)}
    data-state="in"
    aria-hidden
    style={{ '--check-y-amount': `${Math.round(size * 0.6)}px`, '--check-blur-from': `${Math.max(2, size / 4)}px` } as React.CSSProperties}
  >
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
      <path d="M5 12.5l4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </span>
);

/** Largest lean of the wordmark toward the pointer, degrees. */
const LOGO_TILT_MAX = 26;

/**
 * The Tech for Humans wordmark, leaning toward the pointer in 3D with a glare
 * that follows it (transitions.dev "3D tilt"). The glare is cut to the
 * letterforms by the artwork's own alpha, so the light runs across the mark
 * and never over the bar behind it. The pointer is tracked on the outer box,
 * which never transforms, so the leaning mark cannot slide out from under it.
 */
export const TiltLogo: React.FC<{ height: number }> = ({ height }) => {
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const cardRef = useRef<HTMLSpanElement | null>(null);

  const onMove = (event: React.PointerEvent) => {
    const wrap = wrapRef.current;
    const card = cardRef.current;
    if (!wrap || !card || prefersReducedMotion()) return;
    const box = wrap.getBoundingClientRect();
    if (!(box.width > 0) || !(box.height > 0)) return;
    const px = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    const py = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
    wrap.classList.add('is-hover');
    card.classList.add('is-tilting');
    card.style.setProperty('--tilt-ry', `${((px - 0.5) * LOGO_TILT_MAX).toFixed(2)}deg`);
    card.style.setProperty('--tilt-rx', `${((0.5 - py) * LOGO_TILT_MAX).toFixed(2)}deg`);
    card.style.setProperty('--tilt-gx', `${(px * 100).toFixed(1)}%`);
    card.style.setProperty('--tilt-gy', `${(py * 100).toFixed(1)}%`);
  };

  const onLeave = () => {
    wrapRef.current?.classList.remove('is-hover');
    const card = cardRef.current;
    if (!card) return;
    card.classList.remove('is-tilting');
    card.style.setProperty('--tilt-rx', '0deg');
    card.style.setProperty('--tilt-ry', '0deg');
  };

  return (
    <span ref={wrapRef} className="logo-tilt" data-logo-tilt onPointerMove={onMove} onPointerLeave={onLeave}>
      <span ref={cardRef} className="logo-tilt-card">
        <Wordmark height={height} />
        <span className="logo-tilt-glare" aria-hidden />
      </span>
    </span>
  );
};
