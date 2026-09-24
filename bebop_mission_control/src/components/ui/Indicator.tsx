import React from 'react';
import { cn } from '../../lib/format';

export type IndicatorState = 'pass' | 'warn' | 'fail' | 'idle';

const DOT: Record<IndicatorState, string> = {
  pass: 'bg-mint',
  warn: 'bg-amber',
  fail: 'bg-ember',
  idle: 'bg-haze-deep',
};

const GLOW: Record<IndicatorState, string> = {
  pass: 'shadow-[0_0_8px_0_rgba(1,213,163,0.7)]',
  warn: 'shadow-[0_0_8px_0_rgba(255,194,75,0.6)]',
  fail: 'shadow-[0_0_8px_0_rgba(255,106,69,0.7)]',
  idle: '',
};

export const Dot: React.FC<{ state: IndicatorState; pulse?: boolean; className?: string }> = ({
  state,
  pulse,
  className,
}) => (
  <span
    aria-hidden
    className={cn(
      'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
      DOT[state],
      GLOW[state],
      pulse && 'anim-breathe',
      className
    )}
  />
);

/**
 * Link strength as four rising bars.
 *
 * Bars are the measurement made glanceable; the dBm figure beside them is the
 * measurement itself. Both are shown because the operator needs one to decide
 * and the other to report.
 */
export const RfBars: React.FC<{ bars: number; className?: string }> = ({ bars, className }) => (
  <span className={cn('inline-flex items-end gap-[2px]', className)} aria-hidden>
    {[0, 1, 2, 3].map((i) => (
      <span
        key={i}
        className={cn(
          'w-[3px] rounded-[1px] transition-colors duration-300',
          i < bars ? 'bg-mint' : 'bg-strut'
        )}
        style={{ height: 4 + i * 3 }}
      />
    ))}
  </span>
);

/** Charge as a filled cell. Amber below a third, ember below a sixth. */
export const BatteryCell: React.FC<{ pct: number; known?: boolean; className?: string }> = ({
  pct,
  known = true,
  className,
}) => {
  const safe = Math.max(0, Math.min(100, pct));
  const tone = !known ? 'bg-haze-deep' : safe <= 15 ? 'bg-ember' : safe <= 33 ? 'bg-amber' : 'bg-mint';
  return (
    <span className={cn('inline-flex items-center gap-[2px]', className)} aria-hidden>
      <span className="relative block h-3.5 w-7 rounded-[2px] border border-strut-bright p-[2px]">
        <span
          className={cn('block h-full rounded-[1px] transition-all duration-500', tone)}
          style={{ width: known ? `${safe}%` : '100%', opacity: known ? 1 : 0.25 }}
        />
      </span>
      <span className="block h-1.5 w-[2px] rounded-r-[1px] bg-strut-bright" />
    </span>
  );
};
