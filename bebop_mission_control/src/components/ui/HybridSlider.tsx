import React, { useEffect, useState } from 'react';
import { cn } from '../../lib/format';

/**
 * Accent of a slider's fill and thumb. Mint is a flight parameter; amber is the
 * battery, whose colour it already is on the status bar.
 */
export type SliderAccent = 'mint' | 'amber';

const ACCENT: Record<SliderAccent, { fill: string; thumb: string }> = {
  mint: {
    fill: 'bg-gradient-to-r from-kelp to-mint',
    thumb: 'border-mint peer-hover:shadow-live peer-focus-visible:shadow-live peer-active:shadow-live',
  },
  // No amber glow exists in the token set, so the battery thumb answers focus
  // and drag by filling instead.
  amber: {
    fill: 'bg-amber',
    thumb: 'border-amber peer-focus-visible:bg-amber peer-active:bg-amber',
  },
};

const toRatio = (value: number, min: number, max: number): number =>
  Math.max(0, Math.min(1, (value - min) / (max - min || 1)));

interface HybridSliderProps {
  value: number;
  min: number;
  max: number;
  step: number;
  precision: number;
  label: string;
  accent?: SliderAccent;
  /** A reference value drawn as a tick on the track, such as the default. */
  marker?: number;
  disabled?: boolean;
  onChange: (value: number) => void;
  className?: string;
}

/**
 * A native range input, invisible, over a hand-drawn track, fill and thumb.
 *
 * The native input keeps pointer, keyboard and screen-reader behaviour; the
 * drawing underneath follows it through `peer` state. Values are rounded to
 * `precision` on the way out so a drag never emits a float the exact-value
 * field would print differently.
 */
export const HybridSlider: React.FC<HybridSliderProps> = ({
  value,
  min,
  max,
  step,
  precision,
  label,
  accent = 'mint',
  marker,
  disabled = false,
  onChange,
  className,
}) => {
  const tone = ACCENT[accent];
  const ratio = toRatio(value, min, max);

  return (
    <div className={cn('group relative h-5', className)}>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(Number(e.target.value).toFixed(precision)))}
        className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
      />
      <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-strut transition-colors duration-150 peer-hover:bg-strut-bright peer-disabled:bg-strut" />
      <div
        className={cn('pointer-events-none absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full', tone.fill)}
        style={{ width: `${ratio * 100}%` }}
      />
      {marker !== undefined ? (
        <div
          aria-hidden
          className="pointer-events-none absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-frost/40"
          style={{ left: `${toRatio(marker, min, max) * 100}%` }}
        />
      ) : null}
      <div
        className={cn(
          'pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full',
          'border-2 bg-hull-deep transition-[box-shadow,background-color,transform] duration-150 ease-instrument',
          'peer-active:scale-110',
          tone.thumb,
          'peer-disabled:scale-100 peer-disabled:bg-hull-deep peer-disabled:shadow-none'
        )}
        style={{ left: `${ratio * 100}%` }}
      />
    </div>
  );
};

interface ExactValueFieldProps {
  value: number;
  min: number;
  max: number;
  precision: number;
  unit?: string;
  label: string;
  onCommit: (value: number) => void;
  className?: string;
}

/**
 * The other half of the hybrid control: the exact figure. It holds a draft
 * while the operator types and commits on blur or Enter, clamped to the range
 * and rounded to `precision`, so a half-typed number never reaches the mission.
 * An unparseable entry falls back to the last committed value.
 */
export const ExactValueField: React.FC<ExactValueFieldProps> = ({
  value,
  min,
  max,
  precision,
  unit,
  label,
  onCommit,
  className,
}) => {
  const [draft, setDraft] = useState(() => value.toFixed(precision));

  useEffect(() => {
    setDraft(value.toFixed(precision));
  }, [value, precision]);

  const commit = (raw: string) => {
    const parsed = Number.parseFloat(raw.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      setDraft(value.toFixed(precision));
      return;
    }
    const clamped = Math.min(max, Math.max(min, parsed));
    const rounded = Number(clamped.toFixed(precision));
    onCommit(rounded);
    setDraft(rounded.toFixed(precision));
  };

  return (
    <label
      className={cn(
        'flex items-baseline justify-end gap-1 rounded-bezel border border-strut bg-abyss px-2 py-1 transition-colors focus-within:border-mint/70',
        className
      )}
    >
      <input
        value={draft}
        inputMode="decimal"
        aria-label={`${label}, valor exato`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        className="tnum w-full bg-transparent text-right font-mono text-base text-frost outline-none"
      />
      {unit ? <span className="shrink-0 font-mono text-3xs text-haze-deep">{unit}</span> : null}
    </label>
  );
};
