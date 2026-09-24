import React, { useEffect, useState } from 'react';
import { cn } from '../../lib/format';

interface RowProps {
  label: string;
  hint: string;
  changed?: boolean;
  children: React.ReactNode;
}

/** One parameter, its explanation, and its control. */
export const ControlRow: React.FC<RowProps> = ({ label, hint, changed, children }) => (
  <div
    className={cn(
      // Capped rather than fluid: on a wide window a full-width row leaves the
      // control so far from its label that the pair stops reading as one thing.
      'grid max-w-[880px] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-8 gap-y-1 border-l-2 py-3 pl-3.5 pr-1 transition-colors',
      changed ? 'border-l-mint bg-mint/[0.05]' : 'border-l-transparent'
    )}
  >
    <div className="min-w-0">
      <div className="text-sm text-frost">{label}</div>
      <p className="mt-0.5 max-w-[62ch] text-2xs leading-relaxed text-haze-deep">{hint}</p>
    </div>
    <div className="shrink-0">{children}</div>
  </div>
);

interface NumberFieldProps {
  value: number;
  min: number;
  max: number;
  step: number;
  precision: number;
  unit?: string;
  label: string;
  onChange: (value: number) => void;
}

/**
 * A slider paired with the exact figure. The slider is for reaching a value
 * quickly; the figure is for entering one precisely, and it commits on blur so
 * a half-typed number never reaches the mission.
 */
export const NumberField: React.FC<NumberFieldProps> = ({
  value,
  min,
  max,
  step,
  precision,
  unit,
  label,
  onChange,
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
    onChange(Number(clamped.toFixed(precision)));
    setDraft(clamped.toFixed(precision));
  };

  const ratio = Math.max(0, Math.min(1, (value - min) / (max - min || 1)));

  return (
    <div className="flex items-center gap-3">
      <div className="group relative h-6 w-44">
        <input
          type="range"
          aria-label={label}
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
        <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-strut" />
        <div
          className="pointer-events-none absolute left-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-mint transition-[width] duration-100"
          style={{ width: `${ratio * 100}%` }}
        />
        <div
          className={cn(
            'pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full',
            'border-2 border-mint bg-hull-deep transition-shadow duration-150',
            'peer-hover:shadow-live peer-focus-visible:shadow-live'
          )}
          style={{ left: `${ratio * 100}%` }}
        />
      </div>
      <div className="flex w-[108px] items-baseline justify-end gap-1 rounded-bezel border border-strut bg-abyss px-2.5 py-1.5 transition-colors focus-within:border-mint/70">
        <input
          value={draft}
          inputMode="decimal"
          aria-label={`${label}, valor exato`}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
          }}
          className="tnum w-full bg-transparent text-right font-mono text-sm text-frost outline-none"
        />
        {unit ? <span className="shrink-0 font-mono text-3xs text-haze-deep">{unit}</span> : null}
      </div>
    </div>
  );
};

interface ToggleProps {
  checked: boolean;
  label: string;
  onChange: (next: boolean) => void;
}

export const Toggle: React.FC<ToggleProps> = ({ checked, label, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    onClick={() => onChange(!checked)}
    className={cn(
      'relative h-6 w-11 rounded-full border transition-colors duration-200 ease-instrument',
      checked ? 'border-mint bg-mint/25' : 'border-strut bg-abyss'
    )}
  >
    <span
      className={cn(
        'absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full transition-all duration-200 ease-instrument',
        checked ? 'left-[24px] bg-mint' : 'left-[4px] bg-haze-deep'
      )}
    />
  </button>
);

interface TextFieldProps {
  value: string;
  label: string;
  placeholder?: string;
  onChange: (next: string) => void;
}

export const TextField: React.FC<TextFieldProps> = ({ value, label, placeholder, onChange }) => (
  <input
    value={value}
    aria-label={label}
    placeholder={placeholder}
    spellCheck={false}
    onChange={(e) => onChange(e.target.value)}
    className="w-[230px] rounded-bezel border border-strut bg-abyss px-2.5 py-1.5 font-mono text-sm text-frost outline-none transition-colors focus:border-mint/70"
  />
);

interface ClassPickerProps {
  selected: string[];
  options: { id: string; label: string }[];
  onChange: (next: string[]) => void;
}

export const ClassPicker: React.FC<ClassPickerProps> = ({ selected, options, onChange }) => (
  <div className="flex flex-wrap justify-end gap-1.5">
    {options.map((option) => {
      const on = selected.includes(option.id);
      const isLast = on && selected.length === 1;
      return (
        <button
          key={option.id}
          type="button"
          aria-pressed={on}
          disabled={isLast}
          title={isLast ? 'Ao menos uma classe precisa ficar ativa' : undefined}
          onClick={() =>
            onChange(on ? selected.filter((c) => c !== option.id) : [...selected, option.id])
          }
          className={cn(
            'rounded-full border px-3 py-1 text-2xs transition-colors duration-150',
            on
              ? 'border-mint/55 bg-mint/15 text-mint'
              : 'border-strut bg-abyss text-haze hover:border-strut-bright hover:text-frost',
            isLast && 'cursor-not-allowed'
          )}
        >
          {option.label}
        </button>
      );
    })}
  </div>
);
