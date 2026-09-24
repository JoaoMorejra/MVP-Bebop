import React from 'react';
import { cn } from '../../lib/format';

/**
 * The Tech for Humans marks.
 *
 * Both are the official artwork rendered as white-on-transparent, so they carry
 * the real letterforms rather than a redrawing.
 *
 * The artwork is flattened to one solid colour rather than shown as supplied.
 * Over the pre-flight film the original's soft edges and partial transparency
 * read as a watermark — the mark appearing to dissolve into whatever frame is
 * behind it — and an identity that changes weight with the footage is not an
 * identity. `brightness(0)` collapses every pixel to black, keeping only the
 * alpha channel, and `invert` then lifts it back to a flat tone; `drop-shadow`
 * tints that tone without touching the letterforms.
 */

type Tone = 'frost' | 'mint';

/** Flatten the artwork to one solid colour, keeping only its silhouette. */
const SOLID: Record<Tone, string> = {
  frost: 'brightness(0) invert(1) drop-shadow(0 0 0 #E8F2F0)',
  mint: 'brightness(0) saturate(100%) invert(72%) sepia(52%) saturate(2400%) hue-rotate(118deg) brightness(97%) contrast(101%)',
};

export const Wordmark: React.FC<{ height?: number; tone?: Tone; className?: string }> = ({
  height = 22,
  tone = 'frost',
  className,
}) => (
  <img
    src="/assets/t4h-wordmark.png"
    alt="Tech for Humans"
    height={height}
    style={{ height, filter: SOLID[tone] }}
    className={cn('w-auto select-none', className)}
    draggable={false}
  />
);

export const Monogram: React.FC<{ height?: number; tone?: Tone; className?: string }> = ({
  height = 20,
  tone = 'frost',
  className,
}) => (
  <img
    src="/assets/t4h-monogram.png"
    alt="Tech for Humans"
    height={height}
    style={{ height, filter: SOLID[tone] }}
    className={cn('w-auto select-none', className)}
    draggable={false}
  />
);
