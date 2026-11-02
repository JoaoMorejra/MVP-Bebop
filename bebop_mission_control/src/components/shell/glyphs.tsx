import React from 'react';
import type { Ros2Tone } from '../../lib/trayState';
import { cn } from '../../lib/format';

/**
 * The tray glyphs. White on the bar, as every other mark in it: colour is
 * reserved for the one reading that needs an operator's eye, a battery in the
 * alarm band.
 */

/** The battery glyph: a cell, a cap, and a fill proportional to the charge. */
export const BatteryGlyph: React.FC<{ pct: number; known: boolean; alert: boolean }> = ({ pct, known, alert }) => (
  <span className="inline-flex items-center gap-[1.5px]" aria-hidden data-alert={alert || undefined}>
    <span
      className={cn(
        'relative block h-[12px] w-[22px] rounded-[3.5px] border-[1.5px] p-[1.5px] transition-colors duration-300',
        alert ? 'border-ember' : 'border-frost/80'
      )}
    >
      <span
        className={cn('block h-full rounded-[1.5px] transition-[width,background-color] duration-500', alert ? 'bg-ember' : 'bg-frost')}
        style={{ width: known ? `${Math.max(6, Math.min(100, pct))}%` : '100%', opacity: known ? 1 : 0.22 }}
      />
    </span>
    <span className={cn('block h-[5px] w-[2px] rounded-r-[1px]', alert ? 'bg-ember' : 'bg-frost/80')} />
  </span>
);

const WIFI_ARCS = [
  'M 4.6 9.6 A 4.8 4.8 0 0 1 11.4 9.6',
  'M 2.3 7.2 A 8.2 8.2 0 0 1 13.7 7.2',
  'M 0.2 4.8 A 11.4 11.4 0 0 1 15.8 4.8',
];

/**
 * The Wi-Fi glyph: a dot and three arcs, lit up to the measured strength
 * (`level` 0 to 4). While `seeking`, the arcs sweep outward from the dot, the
 * way the radio's own search reads.
 */
export const WifiGlyph: React.FC<{ level: number; seeking?: boolean; size?: number }> = ({
  level,
  seeking = false,
  size = 16,
}) => (
  <svg
    width={size}
    height={size * 0.8}
    viewBox="0 0 16 13"
    aria-hidden
    data-seeking={seeking || undefined}
    className={cn('shrink-0', seeking && 'wifi-seek')}
  >
    {WIFI_ARCS.map((d, i) => (
      <path
        key={d}
        d={d}
        fill="none"
        stroke="#E8F2F0"
        strokeOpacity={seeking || level >= i + 2 ? 1 : 0.26}
        strokeWidth="1.7"
        strokeLinecap="round"
        style={{ '--i': i + 1 } as React.CSSProperties}
      />
    ))}
    <circle cx="8" cy="11.6" r="1.35" fill="#E8F2F0" fillOpacity={seeking || level >= 1 ? 1 : 0.26} />
  </svg>
);

/** The 3x3 grid, row by row. */
const ROS2_DOTS: ReadonlyArray<[number, number]> = [
  [3, 3],
  [8, 3],
  [13, 3],
  [3, 8],
  [8, 8],
  [13, 8],
  [3, 13],
  [8, 13],
  [13, 13],
];

/**
 * Order the matrix loader lights the ring in, clockwise from the top left; the
 * centre holds steady (the "orbit" variant of the transitions.dev matrix
 * loader, folded onto the 3x3 grid of this mark).
 */
const ORBIT: Readonly<Record<number, number>> = { 0: 0, 1: 1, 2: 2, 5: 3, 8: 4, 7: 5, 6: 6, 3: 7 };
export const MATRIX_CYCLE_MS = 1200;

/**
 * The ROS 2 indicator: a 3x3 grid of dots echoing the dotted grid of the ROS
 * mark without copying it. `busy` runs the matrix loader around the ring;
 * `off` dims the grid; `ready` draws it solid.
 */
export const Ros2Glyph: React.FC<{ tone: Ros2Tone; size?: number }> = ({ tone, size = 16 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    aria-hidden
    data-tone={tone}
    className={cn('shrink-0', tone === 'busy' && 'ros-matrix')}
  >
    {ROS2_DOTS.map(([cx, cy], index) => {
      const step = ORBIT[index];
      return (
        <circle
          key={`${cx}-${cy}`}
          cx={cx}
          cy={cy}
          r={1.45}
          fill="#E8F2F0"
          fillOpacity={tone === 'off' ? 0.38 : 1}
          data-orbit={step === undefined ? 'centre' : step}
          style={step === undefined ? undefined : ({ '--d': (step * MATRIX_CYCLE_MS) / 8 } as React.CSSProperties)}
        />
      );
    })}
  </svg>
);
