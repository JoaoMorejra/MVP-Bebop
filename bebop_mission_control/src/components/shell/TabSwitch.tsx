import React from 'react';
import { Lock } from 'lucide-react';
import type { Screen } from '../../types/mission';
import { cn } from '../../lib/format';

interface TabSwitchProps {
  screen: Screen;
  onNavigate: (screen: Screen) => void;
  /** A mission is in the air; the cockpit tab carries a live marker. */
  live: boolean;
  /**
   * The aircraft is flying, arming or coming down. Pre-flight is locked for the
   * duration: it is the screen whose one control commands a launch, and the
   * cockpit holds the only control that stops one.
   */
  locked: boolean;
  className?: string;
}

/** Typography only: the lock and the live marker are the only glyphs the dock carries. */
const TABS: { id: Screen; label: string }[] = [
  { id: 'preflight', label: 'Inicio' },
  { id: 'cockpit', label: 'Cabine' },
];

/**
 * The dock: the two tabs the station is organised around.
 *
 * It is mounted once, by `App`, at the top centre of the window, and it does
 * not move when the tab under it changes. That fixed coordinate is the point —
 * an operator switching between the ground and the air reaches for the same
 * place both times, and never has to find the control again on arrival. It
 * floats as frosted glass over whatever surface it is on, so it reads as part
 * of the window rather than of the screen beneath it.
 */
export const TabSwitch: React.FC<TabSwitchProps> = ({
  screen,
  onNavigate,
  live,
  locked,
  className,
}) => (
  <div
    role="tablist"
    aria-label="Áreas da estação"
    className={cn(
      'flex items-center gap-0.5 rounded-full border border-frost/15 bg-abyss/60 p-1 shadow-lg backdrop-blur-md',
      className
    )}
  >
    {TABS.map(({ id, label }) => {
      const active = screen === id;
      const blocked = locked && id === 'preflight' && !active;
      return (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={active}
          disabled={blocked}
          onClick={() => onNavigate(id)}
          title={blocked ? 'Indisponível com a aeronave em voo' : undefined}
          className={cn(
            'relative flex items-center gap-1.5 rounded-full px-5 py-1.5 transition-colors duration-200',
            active ? 'bg-mint/15 text-frost' : 'text-haze hover:text-frost',
            blocked && 'cursor-not-allowed text-haze-deep hover:text-haze-deep'
          )}
        >
          {blocked ? <Lock size={11} strokeWidth={2} className="text-haze-deep" /> : null}
          <span className={cn('font-cond text-xs tracking-[0.08em]', active && 'font-semibold')}>
            {label}
          </span>
          {id === 'cockpit' && live ? (
            <span
              aria-label="missão em andamento"
              className="h-1.5 w-1.5 rounded-full bg-mint anim-breathe"
            />
          ) : null}
        </button>
      );
    })}
  </div>
);
