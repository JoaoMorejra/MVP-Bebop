import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Check } from 'lucide-react';
import { FLIGHT_MODES, type FlightMode } from '../../lib/flightMode';
import { cn } from '../../lib/format';

interface MissionModeSelectorProps {
  /** The flight mode of the working document (`readFlightMode`). */
  value: FlightMode;
  /** Called with a mode other than `value`; never with the active one. */
  onChange: (mode: FlightMode) => void;
  /**
   * The selection is frozen: the trigger stays readable and focusable, but
   * it does not open. Set from the click on the launch dial until the mission
   * ends.
   */
  disabled?: boolean;
}

/** Height the open capsule needs below the trigger before it grows upwards instead. */
const OPEN_ROOM_PX = 168;

/**
 * Where a mode sits on the selector, drawn as a gear selector draws it: three
 * stops on a short vertical track, the one at `index` lit.
 */
const ModeTrack: React.FC<{ index: number }> = ({ index }) => (
  <span className="mode-track" aria-hidden>
    {FLIGHT_MODES.map((mode, stop) => (
      <span key={mode.id} className="mode-track-stop" data-lit={stop === index || undefined} />
    ))}
  </span>
);

/** A label with a band of light running across its letters (transitions.dev "Shimmer text"). */
const Shimmer: React.FC<{ text: string; delayMs?: number; className?: string }> = ({ text, delayMs = 0, className }) => (
  <span
    className={cn('mode-shimmer', className)}
    data-text={text}
    style={delayMs ? ({ '--shimmer-delay': `${delayMs}ms` } as React.CSSProperties) : undefined}
  >
    {text}
  </span>
);

/**
 * The flight mode, as a selector of three positions.
 *
 * Closed, it is a glass capsule the size of Parâmetros beside it: the track
 * with the current position lit, the mode's name, a chevron. Opened, the
 * capsule itself grows in place to hold all three positions (transitions.dev
 * "Accordion": the body unfolds on `grid-template-rows` with a blur, and the
 * chevron flips), and folds back on a pick. The track lives on the trigger
 * only; in the open list a tick marks the chosen mode, which stays still, and
 * the modes on offer carry the shimmer. Flight modes are lit in mint; Teste in the cyan the launch dial
 * takes for it.
 *
 * The list follows the WAI-ARIA listbox pattern: it opens on the selected
 * option, the arrow keys move the focus and wrap, Enter or a click picks, and
 * Escape or a click outside closes it with the focus back on the trigger. It
 * grows upwards when the viewport has no room below. Folded, the list is inert
 * and carries no listbox role. Locking uses `aria-disabled` so the mode already
 * sent stays focusable and announced while it cannot change.
 */
export const MissionModeSelector: React.FC<MissionModeSelectorProps> = ({ value, onChange, disabled = false }) => {
  const [open, setOpen] = useState(false);
  const [upwards, setUpwards] = useState(false);
  const [focusIndex, setFocusIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  const listId = useId();
  const selectedIndex = Math.max(
    0,
    FLIGHT_MODES.findIndex((mode) => mode.id === value)
  );
  const active = FLIGHT_MODES[selectedIndex];

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }, []);

  const toggle = useCallback(() => {
    if (disabled) return;
    if (open) {
      close(false);
      return;
    }
    const rect = triggerRef.current?.getBoundingClientRect();
    setUpwards(rect !== undefined && window.innerHeight - rect.bottom < OPEN_ROOM_PX && rect.top > OPEN_ROOM_PX);
    setFocusIndex(selectedIndex);
    setOpen(true);
  }, [close, disabled, open, selectedIndex]);

  const pick = useCallback(
    (mode: FlightMode) => {
      close(true);
      if (disabled || mode === value) return;
      onChange(mode);
    },
    [close, disabled, onChange, value]
  );

  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  // React 18 has no `inert` prop: the folded list is taken out of reach by hand.
  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body) return;
    if (open) body.removeAttribute('inert');
    else body.setAttribute('inert', '');
  }, [open]);

  useEffect(() => {
    if (open) optionRefs.current[focusIndex]?.focus();
  }, [open, focusIndex]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current && event.target instanceof Node && rootRef.current.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, [open]);

  const onListKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLUListElement>) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close(true);
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        pick(FLIGHT_MODES[focusIndex].id);
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
      if (step === 0) return;
      event.preventDefault();
      setFocusIndex((index) => (index + step + FLIGHT_MODES.length) % FLIGHT_MODES.length);
    },
    [close, focusIndex, pick]
  );

  return (
    <div
      ref={rootRef}
      className="mode-select"
      data-open={open || undefined}
      data-dir={upwards ? 'up' : 'down'}
      data-theme={value === 'test' ? 'test' : 'flight'}
      data-disabled={disabled || undefined}
    >
      <div className="mode-select-shell">
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-disabled={disabled || undefined}
          onClick={toggle}
          className="mode-select-head"
        >
          <ModeTrack index={selectedIndex} />
          <span className="mode-select-label">{active.label}</span>
          <span className="mode-select-chevron" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 6.5L8 10.5L12 6.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
        </button>

        <div className="mode-select-body">
          <div ref={bodyRef} className="mode-select-body-inner" aria-hidden={!open || undefined}>
            <ul
              id={listId}
              role={open ? 'listbox' : undefined}
              aria-label={open ? 'Modo de voo' : undefined}
              onKeyDown={onListKeyDown}
              className="mode-select-list"
            >
              {FLIGHT_MODES.map((mode, index) => {
                const selected = mode.id === value;
                return (
                  <li
                    key={mode.id}
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    role={open ? 'option' : undefined}
                    aria-selected={open ? selected : undefined}
                    data-mode={mode.id}
                    tabIndex={-1}
                    onClick={() => pick(mode.id)}
                    className="mode-select-option"
                  >
                    <span aria-hidden />
                    {selected ? (
                      <span className="mode-select-label">{mode.label}</span>
                    ) : (
                      <Shimmer text={mode.label} delayMs={index * 140} className="mode-select-label" />
                    )}
                    <span className="mode-select-tick" aria-hidden>
                      {selected ? <Check size={15} strokeWidth={2.2} /> : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};
