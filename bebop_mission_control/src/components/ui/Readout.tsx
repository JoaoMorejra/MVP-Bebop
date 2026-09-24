import React from 'react';
import { cn } from '../../lib/format';

type Size = 'sm' | 'md' | 'lg' | 'xl';

const SIZE: Record<Size, { value: string; unit: string; label: string }> = {
  sm: { value: 'text-base', unit: 'text-3xs', label: 'text-3xs' },
  md: { value: 'text-xl', unit: 'text-2xs', label: 'text-2xs' },
  lg: { value: 'text-2xl', unit: 'text-xs', label: 'text-2xs' },
  xl: { value: 'text-3xl', unit: 'text-sm', label: 'text-xs' },
};

interface ReadoutProps {
  label: string;
  value: string;
  unit?: string;
  size?: Size;
  /** The measurement is current and changing. */
  live?: boolean;
  /** Older than the link allows; the value on screen is not the aircraft's. */
  stale?: boolean;
  tone?: 'default' | 'mint' | 'amber' | 'ember';
  className?: string;
}

const TONE: Record<NonNullable<ReadoutProps['tone']>, string> = {
  default: 'text-frost',
  mint: 'text-mint',
  amber: 'text-amber',
  ember: 'text-ember',
};

/**
 * A labelled measurement.
 *
 * The number is the largest thing in the cell and the label stays small, which
 * is the reverse of the usual dashboard tile. An operator glancing up from the
 * aircraft is looking for a value, not for what to call it.
 */
export const Readout: React.FC<ReadoutProps> = ({
  label,
  value,
  unit,
  size = 'md',
  live,
  stale,
  tone = 'default',
  className,
}) => (
  <div className={cn('min-w-0', className)}>
    <div className={cn('truncate font-cond tracking-wide text-haze-deep', SIZE[size].label)}>
      {label}
    </div>
    <div className="flex items-baseline gap-1">
      <span
        className={cn(
          'tnum font-mono font-medium tracking-tight transition-colors duration-300',
          SIZE[size].value,
          stale ? 'text-haze-deep' : live ? 'text-mint' : TONE[tone]
        )}
      >
        {stale ? '—' : value}
      </span>
      {unit ? <span className={cn('font-mono text-haze', SIZE[size].unit)}>{unit}</span> : null}
    </div>
  </div>
);
