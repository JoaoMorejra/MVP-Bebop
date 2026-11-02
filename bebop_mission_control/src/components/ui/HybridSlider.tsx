import React, { useEffect, useRef, useState } from 'react';
import { cn } from '../../lib/format';

/**
 * Accent of a slider's fill. Mint is a flight parameter; amber is the battery,
 * whose colour it already is on the status bar.
 */
export type SliderAccent = 'mint' | 'amber';

const FILL: Record<SliderAccent, string> = {
  mint: 'bg-mint',
  amber: 'bg-amber',
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
 * A native range input, invisible, over a hand-drawn track, fill and thumb: a
 * 4 px track filled in the accent and a white knob, as the sliders of the top
 * bar menu draw it.
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
  const ratio = toRatio(value, min, max);

  return (
    <div className={cn('group relative h-6', className)}>
      <input
        type="range"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(Number(e.target.value).toFixed(precision)))}
        className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
      />
      <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-frost/[0.14]" />
      <div
        className={cn(
          'pointer-events-none absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full transition-colors',
          disabled ? 'bg-frost/30' : cn(FILL[accent], accent === 'mint' && 'slider-fill-flight')
        )}
        style={{ width: `${ratio * 100}%` }}
      />
      {marker !== undefined ? (
        <div
          aria-hidden
          className="pointer-events-none absolute top-1/2 h-3 w-[2px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-frost/45"
          style={{ left: `${toRatio(marker, min, max) * 100}%` }}
        />
      ) : null}
      <div
        className={cn(
          'pointer-events-none absolute top-1/2 h-[18px] w-[18px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-white',
          'shadow-[0_1px_3px_rgba(0,8,16,0.6)] transition-[transform,box-shadow] duration-150 ease-instrument',
          'peer-hover:scale-110 peer-hover:shadow-[0_0_0_6px_rgba(232,242,240,0.1),0_1px_3px_rgba(0,8,16,0.6)]',
          'peer-focus-visible:shadow-[0_0_0_6px_rgba(232,242,240,0.16),0_1px_3px_rgba(0,8,16,0.6)] peer-active:scale-110',
          'peer-disabled:scale-90 peer-disabled:bg-frost/50 peer-disabled:shadow-none'
        )}
        style={{ left: `${ratio * 100}%` }}
      />
    </div>
  );
};

/** How long a refused entry keeps its message and red edge before reverting, ms. */
export const REJECT_HOLD_MS = 3000;

/** Why an entry was refused or corrected, in the operator's words; `null` when it went in as typed. */
export function rejectionReason(
  raw: string,
  min: number,
  max: number,
  precision: number,
  unit = ''
): string | null {
  const parsed = Number.parseFloat(raw.replace(',', '.'));
  const figure = (value: number) => `${value.toFixed(precision).replace('.', ',')}${unit ? ` ${unit}` : ''}`;
  if (!Number.isFinite(parsed)) return 'Valor inválido';
  if (parsed > max) return `Máximo ${figure(max)}`;
  if (parsed < min) return `Mínimo ${figure(min)}`;
  return null;
}

/**
 * Width of a figure set in tabular digits, in em: a digit is about 0.56 em, a
 * point, comma or minus narrower. The display field is sized to its draft so
 * the unit sits right after the number instead of at the end of a box.
 */
function figureWidthEm(text: string): number {
  let width = 0.28;
  for (const char of text) width += /[0-9]/.test(char) ? 0.62 : /[.,]/.test(char) ? 0.3 : char === '-' ? 0.45 : 0.62;
  return Math.max(1.2, Number(width.toFixed(2)));
}

interface ExactValueFieldProps {
  value: number;
  min: number;
  max: number;
  precision: number;
  unit?: string;
  label: string;
  onCommit: (value: number) => void;
  /**
   * `box`, a small framed field; `display`, the figure itself in large type,
   * editable in place, as the parameter tiles show it.
   */
  variant?: 'box' | 'display';
  /** An entry was refused (unparseable) or corrected (out of range), with the reason. */
  onReject?: (reason: string) => void;
  /**
   * Drag the figure sideways to change it (kinetics "value scrubber"): one
   * `step` every {@link SCRUB_PX_PER_STEP} pixels, clamped to the range. A
   * press that does not travel opens the field for typing instead.
   */
  scrubStep?: number;
  className?: string;
}

/** Pointer travel for one step of a scrub, px. */
export const SCRUB_PX_PER_STEP = 2;
/** Travel below which a press is a click, not a scrub, px. */
const SCRUB_THRESHOLD_PX = 3;

/** `start` moved by `dx` pixels of scrub, in steps of `step`, clamped and rounded to `precision`. */
export function scrubValue(start: number, dx: number, step: number, min: number, max: number, precision: number): number {
  const steps = Math.trunc(dx / SCRUB_PX_PER_STEP);
  const next = Math.min(max, Math.max(min, start + steps * step));
  return Number(next.toFixed(precision));
}

/**
 * The other half of the hybrid control: the exact figure. It holds a draft
 * while the operator types and commits on blur or Enter, clamped to the range
 * and rounded to `precision`, so a half-typed number never reaches the mission.
 * An unparseable entry falls back to the last committed value.
 *
 * Either case is said rather than done silently: the field shakes
 * (transitions.dev "Error state shake") and takes a red edge for
 * {@link REJECT_HOLD_MS}, and `onReject` hands the reason to the caller.
 */
export const ExactValueField: React.FC<ExactValueFieldProps> = ({
  value,
  min,
  max,
  precision,
  unit,
  label,
  onCommit,
  variant = 'box',
  onReject,
  scrubStep,
  className,
}) => {
  const [draft, setDraft] = useState(() => value.toFixed(precision));
  const [error, setError] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const shellRef = useRef<HTMLLabelElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const press = useRef<{ x: number; start: number; moved: boolean; id: number } | null>(null);

  useEffect(() => {
    setDraft(value.toFixed(precision));
  }, [value, precision]);

  useEffect(() => {
    if (!error) return;
    const id = window.setTimeout(() => setError(false), REJECT_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [error]);

  const shake = (reason: string) => {
    const shell = shellRef.current;
    if (shell) {
      shell.classList.remove('is-shaking');
      void shell.offsetWidth;
      shell.classList.add('is-shaking');
    }
    setError(true);
    onReject?.(reason);
  };

  const commit = (raw: string) => {
    const reason = rejectionReason(raw, min, max, precision, unit);
    const parsed = Number.parseFloat(raw.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      setDraft(value.toFixed(precision));
      if (raw.trim() !== value.toFixed(precision)) shake(reason ?? 'Valor inválido');
      return;
    }
    const clamped = Math.min(max, Math.max(min, parsed));
    const rounded = Number(clamped.toFixed(precision));
    onCommit(rounded);
    setDraft(rounded.toFixed(precision));
    if (reason) shake(reason);
  };

  const display = variant === 'display';
  const scrubs = display && scrubStep !== undefined && scrubStep > 0;
  const editing = () => typeof document !== 'undefined' && document.activeElement === inputRef.current;

  const scrubHandlers = scrubs
    ? {
        onPointerDown: (event: React.PointerEvent<HTMLLabelElement>) => {
          if (event.button !== 0 || editing()) return;
          event.preventDefault();
          press.current = { x: event.clientX, start: value, moved: false, id: event.pointerId };
          event.currentTarget.setPointerCapture?.(event.pointerId);
        },
        onPointerMove: (event: React.PointerEvent<HTMLLabelElement>) => {
          const current = press.current;
          if (!current) return;
          const dx = event.clientX - current.x;
          if (!current.moved && Math.abs(dx) < SCRUB_THRESHOLD_PX) return;
          if (!current.moved) {
            current.moved = true;
            setScrubbing(true);
          }
          const next = scrubValue(current.start, dx, scrubStep ?? 1, min, max, precision);
          if (next !== value) onCommit(next);
        },
        onPointerUp: (event: React.PointerEvent<HTMLLabelElement>) => {
          const current = press.current;
          press.current = null;
          event.currentTarget.releasePointerCapture?.(event.pointerId);
          setScrubbing(false);
          if (current && !current.moved) {
            inputRef.current?.focus();
            inputRef.current?.select();
          }
        },
        onPointerCancel: () => {
          press.current = null;
          setScrubbing(false);
        },
        // The press is the scrub's; the field takes the focus only on a click.
        onMouseDown: (event: React.MouseEvent<HTMLLabelElement>) => {
          if (!editing()) event.preventDefault();
        },
      }
    : {};

  // The input is the box itself, so the app-wide :focus-visible outline lands
  // on the visible field. The unit sits inside its right padding.
  return (
    <label
      ref={shellRef}
      data-error={error || undefined}
      data-scrub={scrubs || undefined}
      data-scrubbing={scrubbing || undefined}
      onAnimationEnd={() => shellRef.current?.classList.remove('is-shaking')}
      className={cn('exact-field relative block', display ? 'exact-field-display' : 'exact-field-box', className)}
      {...scrubHandlers}
    >
      <input
        ref={inputRef}
        value={draft}
        inputMode="decimal"
        aria-label={`${label}, valor exato`}
        aria-invalid={error || undefined}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        style={
          display
            ? { width: `${figureWidthEm(draft)}em` }
            : unit
            ? { paddingRight: `${0.75 + unit.length * 0.4}rem` }
            : undefined
        }
        className={cn(
          'tnum bg-transparent',
          display
            ? 'exact-field-input py-0 text-left'
            : 'w-full rounded-bezel border border-strut bg-abyss py-1 pl-2 pr-2 text-right font-mono text-base text-frost'
        )}
      />
      {unit ? (
        <span
          className={cn(
            'pointer-events-none',
            display ? 'exact-field-unit' : 'absolute bottom-[0.45rem] right-2 font-mono text-3xs text-haze-deep'
          )}
        >
          {unit}
        </span>
      ) : null}
      {scrubs ? (
        <svg aria-hidden viewBox="0 0 24 12" width="22" height="11" className="exact-field-scrub-hint">
          <path d="M7 2 3 6l4 4M17 2l4 4-4 4M3 6h18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : null}
    </label>
  );
};
