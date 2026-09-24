import React, { useEffect, useRef } from 'react';
import { cn } from '../../lib/format';

interface ParticleFieldProps {
  /** Dims and slows the field while something else owns the operator's attention. */
  subdued?: boolean;
  className?: string;
}

/**
 * Two ramps, sampled from the key art: the green crest that dominates the upper
 * field and the blue swell that runs through the lower left. Mixing between
 * them across the width is what stops the surface reading as one flat hue.
 */
const GREEN: [number, number, number][] = [
  [168, 255, 228], // crest highlight
  [46, 232, 182],
  [1, 199, 152],
  [0, 145, 122],
  [0, 92, 98],
  [0, 52, 74], // trough
];

const BLUE: [number, number, number][] = [
  [150, 222, 255],
  [64, 170, 226],
  [26, 118, 182],
  [12, 78, 138],
  [6, 48, 96],
  [2, 30, 62],
];

function rampAt(
  ramp: [number, number, number][],
  t: number
): [number, number, number] {
  const clamped = Math.min(0.999, Math.max(0, t));
  const scaled = clamped * (ramp.length - 1);
  const index = Math.floor(scaled);
  const frac = scaled - index;
  const a = ramp[index];
  const b = ramp[index + 1] ?? a;
  return [
    a[0] + (b[0] - a[0]) * frac,
    a[1] + (b[1] - a[1]) * frac,
    a[2] + (b[2] - a[2]) * frac,
  ];
}

/**
 * The Tech for Humans particle wave, as a live surface.
 *
 * A lattice of points displaced by three layered sine waves and projected with
 * a shallow camera, coloured by height along the brand ramp. It is the identity
 * rebuilt as geometry rather than a screenshot of it: the still art would
 * band at this size and could not respond to anything.
 *
 * Drawn with `fillRect` rather than arcs — at eight thousand points per frame
 * the difference between a rectangle and a circle is invisible and the cost is
 * not. The whole thing holds a single `requestAnimationFrame` and stops dead
 * when the operating system asks for reduced motion.
 */
export const ParticleField: React.FC<ParticleFieldProps> = ({ subdued, className }) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const subduedRef = useRef(subdued);
  subduedRef.current = subdued;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Lattice resolution. Denser toward the horizon, which is what gives the
    // art its moiré without drawing more points than the frame can afford.
    const COLS = 196;
    const ROWS = 104;

    let width = 0;
    let height = 0;
    let dpr = 1;
    let frame = 0;
    let raf = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, Math.floor(rect.width));
      height = Math.max(1, Math.floor(rect.height));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    const draw = (timeMs: number) => {
      const t = timeMs / 1000;
      const quiet = subduedRef.current;

      ctx.fillStyle = '#00131F';
      ctx.fillRect(0, 0, width, height);

      // The horizon sits high so the field reads as a surface receding into
      // depth rather than a texture pinned to the viewport.
      const horizon = height * 0.08;
      const depth = height - horizon;

      for (let row = 0; row < ROWS; row++) {
        // Squared row spacing compresses the lattice toward the horizon, which
        // is the whole perspective cue and the source of the art's moiré.
        const rowT = row / (ROWS - 1);
        const y = horizon + depth * rowT * rowT;
        const scale = 0.28 + rowT * 1.85;
        const rowWidth = width * (1.06 + rowT * 0.78);
        const left = (width - rowWidth) / 2;

        for (let col = 0; col < COLS; col++) {
          const colT = col / (COLS - 1);
          const x = left + rowWidth * colT;
          if (x < -10 || x > width + 10) continue;

          // Three layers at incommensurate frequencies, so the surface never
          // visibly repeats.
          const wave =
            Math.sin(colT * 5.4 + rowT * 2.9 - t * 0.3) * 0.56 +
            Math.sin(colT * 10.3 - rowT * 4.7 + t * 0.19) * 0.29 +
            Math.sin((colT + rowT * 1.4) * 7.8 + t * 0.43) * 0.15;

          // Amplitude grows with proximity, so near rows swell and far ones
          // stay tight against the horizon.
          const lift = wave * 96 * scale * 0.5;
          const py = y - lift;
          if (py < -10 || py > height + 10) continue;

          // Height drives the ramp: crests catch the highlight, troughs sink.
          const crest = wave * 0.5 + 0.5;
          const heightT = Math.pow(1 - crest, 1.35);

          // Blue swells through the lower left, green owns the upper right —
          // the diagonal split the key art is built on, drifting slowly so the
          // two never sit still against each other.
          const mix = Math.min(
            1,
            Math.max(0, colT * 1.45 - 0.16 + rowT * 0.22 + Math.sin(t * 0.11) * 0.07)
          );
          const g = rampAt(GREEN, heightT);
          const b = rampAt(BLUE, heightT);
          const r0 = b[0] + (g[0] - b[0]) * mix;
          const g0 = b[1] + (g[1] - b[1]) * mix;
          const b0 = b[2] + (g[2] - b[2]) * mix;

          // Near rows carry the most light; the horizon dissolves rather than
          // ending. Crests get an extra lift so the wave tops actually glow.
          const distance = Math.min(1, 0.26 + rowT * 1.9);
          const crestBoost = 0.52 + Math.pow(crest, 1.5) * 0.7;
          const alpha = Math.min(1, distance * crestBoost) * (quiet ? 0.3 : 1);

          ctx.fillStyle = `rgba(${r0 | 0}, ${g0 | 0}, ${b0 | 0}, ${alpha.toFixed(3)})`;
          const size = Math.max(0.9, 1.3 * scale);
          ctx.fillRect(x, py, size, size);
        }
      }

      frame += 1;
      if (!reduceMotion) raf = window.requestAnimationFrame(draw);
    };

    if (reduceMotion) {
      draw(0);
    } else {
      raf = window.requestAnimationFrame(draw);
    }

    return () => {
      window.cancelAnimationFrame(raf);
      observer.disconnect();
      void frame;
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className={cn('absolute inset-0 h-full w-full', className)}
    />
  );
};
