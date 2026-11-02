import React, { useCallback, useLayoutEffect, useRef } from 'react';
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
  /** The launch sequence is running: neither tab can be taken (D6). */
  frozen?: boolean;
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
 * It sits at the centre of the global top bar, mounted once, and does not
 * move when the tab under it changes. That fixed coordinate is the point —
 * an operator switching between the ground and the air reaches for the same
 * place both times, and never has to find the control again on arrival.
 *
 * The active tab is marked by one pill that slides between them
 * (transitions.dev "Tabs sliding"): its offset and width are measured from the
 * tab and written inline, so the transition tweens from the previous tab to
 * the next. First paint and a resize snap it without animating.
 */
export const TabSwitch: React.FC<TabSwitchProps> = ({
  screen,
  onNavigate,
  live,
  locked,
  frozen = false,
  className,
}) => {
  const pillRef = useRef<HTMLSpanElement | null>(null);
  const tabRefs = useRef<Partial<Record<Screen, HTMLButtonElement | null>>>({});
  const placed = useRef(false);

  const place = useCallback(
    (animate: boolean) => {
      const pill = pillRef.current;
      const tab = tabRefs.current[screen];
      if (!pill || !tab) return;
      const write = () => {
        pill.style.transform = `translateX(${tab.offsetLeft}px)`;
        pill.style.width = `${tab.offsetWidth}px`;
      };
      if (animate) {
        write();
        return;
      }
      const previous = pill.style.transition;
      pill.style.transition = 'none';
      write();
      void pill.offsetWidth;
      pill.style.transition = previous;
    },
    [screen]
  );

  useLayoutEffect(() => {
    place(placed.current);
    placed.current = true;
  }, [place, live, locked]);

  useLayoutEffect(() => {
    const onResize = () => place(false);
    window.addEventListener('resize', onResize);
    // The labels are set in the system face; their width settles once it loads.
    void document.fonts?.ready.then(() => place(false));
    return () => window.removeEventListener('resize', onResize);
  }, [place]);

  return (
    <div role="tablist" aria-label="Áreas da estação" className={cn('tab-slide', className)}>
      <span ref={pillRef} aria-hidden data-tab-pill className="tab-slide-pill" />
      {TABS.map(({ id, label }) => {
        const active = screen === id;
        const blocked = locked && id === 'preflight' && !active;
        const inert = blocked || frozen;
        return (
          <button
            key={id}
            ref={(node) => {
              tabRefs.current[id] = node;
            }}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={inert}
            onClick={() => onNavigate(id)}
            className={cn('tab-slide-tab', inert && 'cursor-not-allowed', blocked && 'opacity-45')}
          >
            {blocked ? <Lock size={11} strokeWidth={2.2} aria-hidden /> : null}
            <span className="uppercase tracking-[0.1em]">{label}</span>
            {id === 'cockpit' && live ? (
              <span aria-label="missão em andamento" className="h-1.5 w-1.5 rounded-full bg-mint anim-breathe" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
};
