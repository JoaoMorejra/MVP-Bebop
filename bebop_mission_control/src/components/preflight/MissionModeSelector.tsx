import React, { useCallback, useId, useRef } from 'react';
import { MISSION_MODES, type MissionMode } from '../../lib/missionMode';
import { cn } from '../../lib/format';

interface MissionModeSelectorProps {
  /** The mode of the working document. */
  value: MissionMode;
  /** Called with a mode other than `value`; never with the active one. */
  onChange: (mode: MissionMode) => void;
  /**
   * The selection is frozen: the group stays readable and focusable, but
   * clicks and arrow keys change nothing. Set from the click on the launch
   * dial until the mission ends.
   */
  disabled?: boolean;
}

const NEXT_KEYS: ReadonlySet<string> = new Set(['ArrowRight', 'ArrowDown']);
const PREVIOUS_KEYS: ReadonlySet<string> = new Set(['ArrowLeft', 'ArrowUp']);

/**
 * The mission profile, as a segmented radio group.
 *
 * Three pills in the order of increasing ambition, the active one in the
 * brand's live mint, with the one-line summary of the active profile beneath
 * them. The group follows the WAI-ARIA radio pattern: only the active pill is
 * in the tab order (roving tabindex) and the arrow keys move the selection,
 * wrapping at both ends.
 *
 * Locking uses `aria-disabled` rather than the native attribute so the
 * profile already sent stays focusable and announced while it cannot change.
 */
export const MissionModeSelector: React.FC<MissionModeSelectorProps> = ({ value, onChange, disabled = false }) => {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const summaryId = useId();
  const active = MISSION_MODES.find((mode) => mode.id === value) ?? MISSION_MODES[MISSION_MODES.length - 1];

  const pick = useCallback(
    (mode: MissionMode) => {
      if (disabled || mode === value) return;
      onChange(mode);
    },
    [disabled, onChange, value]
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
      const step = NEXT_KEYS.has(event.key) ? 1 : PREVIOUS_KEYS.has(event.key) ? -1 : 0;
      if (step === 0) return;
      event.preventDefault();
      if (disabled) return;
      const target = (index + step + MISSION_MODES.length) % MISSION_MODES.length;
      buttons.current[target]?.focus();
      pick(MISSION_MODES[target].id);
    },
    [disabled, pick]
  );

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        role="radiogroup"
        aria-label="Modo de missão"
        aria-describedby={summaryId}
        aria-disabled={disabled || undefined}
        title={disabled ? 'Modo travado: o perfil já faz parte da missão enviada.' : undefined}
        className="flex items-center gap-2"
      >
        {MISSION_MODES.map((mode, index) => {
          const checked = mode.id === active.id;
          return (
            <button
              key={mode.id}
              ref={(node) => {
                buttons.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-disabled={disabled || undefined}
              tabIndex={checked ? 0 : -1}
              onClick={() => pick(mode.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={cn(
                'rounded-full border px-5 py-2 font-cond text-sm tracking-wide backdrop-blur-md transition-colors',
                checked
                  ? 'border-mint/45 bg-mint/[0.08] text-frost'
                  : 'border-frost/15 bg-abyss/45 text-frost/85',
                disabled
                  ? 'cursor-not-allowed opacity-60'
                  : checked
                    ? 'hover:border-mint/70'
                    : 'hover:border-frost/35 hover:text-frost'
              )}
            >
              {mode.label}
            </button>
          );
        })}
      </div>
      <p id={summaryId} className="max-w-[46ch] text-center text-2xs text-haze">
        {active.summary}
      </p>
    </div>
  );
};
