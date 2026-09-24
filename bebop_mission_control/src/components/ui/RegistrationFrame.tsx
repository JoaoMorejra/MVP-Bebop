import React from 'react';
import { cn } from '../../lib/format';

/**
 * Photogrammetric registration marks.
 *
 * Used only on frames that are genuinely registered — the live optical feed
 * and the evidence viewer — where they mark the boundary of what the sensor
 * actually recorded. Not decoration, and not applied to ordinary panels.
 */
export const RegistrationFrame: React.FC<{ tone?: 'frost' | 'mint'; inset?: number }> = ({
  tone = 'frost',
  inset = 10,
}) => {
  const color = tone === 'mint' ? 'border-mint' : 'border-frost/70';
  const corners = [
    'left-0 top-0 border-l border-t',
    'right-0 top-0 border-r border-t',
    'left-0 bottom-0 border-l border-b',
    'right-0 bottom-0 border-r border-b',
  ];
  return (
    <div className="pointer-events-none absolute inset-0 z-20" style={{ padding: inset }}>
      <div className="relative h-full w-full">
        {corners.map((placement) => (
          <span
            key={placement}
            className={cn('absolute h-3.5 w-3.5', color, placement)}
            aria-hidden
          />
        ))}
      </div>
    </div>
  );
};
