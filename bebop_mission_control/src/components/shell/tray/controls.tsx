import React from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '../../../lib/format';

/**
 * The quick-settings controls, after the GNOME 46 quick settings menu of
 * Ubuntu 24.04: a slider with its icon, a switch, and a split tile whose body
 * acts and whose arrow opens a submenu. White on the panel; the mint is kept
 * for what is on: a filled track, a switch thrown, an active tile.
 */

interface QsSliderProps {
  /** Accessible name of the range. */
  label: string;
  /** 0 to 100. */
  value: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
  icon: React.ReactNode;
  disabled?: boolean;
  /** Figure drawn at the end of the row, when the value itself is the point. */
  readout?: string;
}

/** A full-width slider: icon, a 4 px track filled in mint, a white knob. */
export const QsSlider: React.FC<QsSliderProps> = ({ label, value, min = 0, max = 100, onChange, icon, disabled, readout }) => {
  const span = max - min || 1;
  const percent = Math.max(0, Math.min(100, ((value - min) / span) * 100));
  return (
    <div className={cn('qs-slider', disabled && 'opacity-45')} data-qs-slider={label}>
      <span className="qs-slider-icon">{icon}</span>
      <div className="relative h-7 flex-1">
        <input
          type="range"
          aria-label={label}
          min={min}
          max={max}
          step={1}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(Number(event.target.value))}
          className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
        />
        <span className="qs-slider-track" />
        <span className="qs-slider-fill" style={{ width: `${percent}%` }} />
        <span className="qs-slider-knob" style={{ left: `${percent}%` }} />
      </div>
      {readout ? <span className="tnum w-10 shrink-0 text-right text-[13px] text-frost">{readout}</span> : null}
    </div>
  );
};

/** A switch: mint track and white knob when on, a quiet track when off. */
export const QsSwitch: React.FC<{
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}> = ({ label, checked, onChange, disabled }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    aria-label={label}
    disabled={disabled}
    onClick={() => onChange(!checked)}
    className="qs-switch"
  >
    <span className="qs-switch-knob" />
  </button>
);

interface QsTileProps {
  /** Which tile, for tests and styling hooks. */
  id: string;
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  /** The thing the tile stands for is on: the tile fills. */
  active: boolean;
  /** Its submenu is the one open below the tiles. */
  expanded: boolean;
  /** The body: what pressing the tile does. */
  onMain: () => void;
  mainLabel: string;
  mainDisabled?: boolean;
  onExpand: () => void;
  expandLabel: string;
}

/**
 * A split pill: the body acts, the arrow opens the submenu. The arrow keeps
 * its own hit area and its own hover, as GNOME draws it, so a press meant for
 * one is never taken by the other.
 */
export const QsTile: React.FC<QsTileProps> = ({
  id,
  icon,
  title,
  subtitle,
  active,
  expanded,
  onMain,
  mainLabel,
  mainDisabled,
  onExpand,
  expandLabel,
}) => (
  <div className="qs-tile" data-qs-tile={id} data-active={active || undefined} data-expanded={expanded || undefined}>
    <button type="button" className="qs-tile-main" onClick={onMain} disabled={mainDisabled} aria-label={mainLabel}>
      <span className="shrink-0">{icon}</span>
      <span className="min-w-0 text-left">
        <span className="title-pixel block truncate font-pixel text-[13.5px] font-medium leading-tight tracking-[0.01em]">{title}</span>
        <span className="block truncate font-sans text-[11.5px] leading-tight text-frost/70">{subtitle}</span>
      </span>
    </button>
    <button type="button" className="qs-tile-arrow" onClick={onExpand} aria-label={expandLabel} aria-expanded={expanded}>
      <ChevronRight size={16} strokeWidth={2} />
    </button>
  </div>
);

/** The ring spinner of the GNOME submenus, drawn in white. */
export const QsSpinner: React.FC<{ size?: number }> = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden data-qs-spinner className="qs-spinner shrink-0">
    <circle cx="8" cy="8" r="6" fill="none" stroke="#E8F2F0" strokeOpacity="0.22" strokeWidth="1.8" />
    <path d="M8 2 A6 6 0 0 1 14 8" fill="none" stroke="#E8F2F0" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

/** The submenu card that opens under the tiles: icon bubble, title, a state mark, the body. */
export const QsSubmenu: React.FC<{
  id: string;
  icon: React.ReactNode;
  title: string;
  /** A spinner, a tick, or nothing, after the title. */
  status?: React.ReactNode;
  active: boolean;
  children: React.ReactNode;
  footer?: React.ReactNode;
}> = ({ id, icon, title, status, active, children, footer }) => (
  <section className="qs-submenu" data-qs-submenu={id} aria-label={title}>
    <header className="flex items-center gap-3 px-1 pb-2">
      <span className={cn('qs-bubble', active && 'qs-bubble-active')}>{icon}</span>
      <h3 className="title-pixel font-pixel text-[16px] font-medium tracking-[0.01em] text-frost">{title}</h3>
      <span className="flex h-4 items-center">{status}</span>
    </header>
    {children}
    {footer ? <footer className="mt-1.5 border-t border-frost/10 pt-1.5">{footer}</footer> : null}
  </section>
);
