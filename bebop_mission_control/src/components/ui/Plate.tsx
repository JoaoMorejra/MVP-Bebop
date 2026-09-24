import React from 'react';
import { cn } from '../../lib/format';

interface PlateProps {
  children: React.ReactNode;
  className?: string;
  /** Sits one tone above the field. For content that must read first. */
  raised?: boolean;
  /** Something on this panel is currently true and moving. */
  live?: boolean;
  alert?: boolean;
  /** Drop the border and background: the panel is only a layout box. */
  bare?: boolean;
}

/**
 * The one panel primitive.
 *
 * Depth is tone and a hairline — an instrument bezel, not a card. No shadows:
 * on a field this dark they read as smudges, and the whole screen would end up
 * with the same soft grey blur under every box.
 */
export const Plate: React.FC<PlateProps> = ({
  children,
  className,
  raised,
  live,
  alert,
  bare,
}) => (
  <div
    className={cn(
      'relative rounded-panel transition-colors duration-200',
      !bare && 'border',
      !bare && (raised ? 'bg-hull-deck' : 'bg-hull/80'),
      !bare && (alert ? 'border-ember/45' : live ? 'border-mint/40' : 'border-strut-soft'),
      className
    )}
  >
    {children}
  </div>
);

interface PlateHeadProps {
  title: string;
  aside?: React.ReactNode;
  className?: string;
}

/**
 * A panel's name. Small, quiet, sentence case — the content underneath is what
 * the operator is reading, and a shouted label above every box is noise.
 */
export const PlateHead: React.FC<PlateHeadProps> = ({ title, aside, className }) => (
  <div
    className={cn(
      'flex h-10 shrink-0 items-center justify-between gap-3 border-b border-strut-soft px-3.5',
      className
    )}
  >
    <h2 className="font-cond text-xs font-medium tracking-wide text-haze">{title}</h2>
    {aside ? <div className="flex items-center gap-2">{aside}</div> : null}
  </div>
);
