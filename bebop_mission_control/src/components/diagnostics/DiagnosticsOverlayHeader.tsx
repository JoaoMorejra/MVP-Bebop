import React from 'react';
import { X } from 'lucide-react';

/** macOS window-control colours, in their fixed left-to-right order. */
const CLOSE_RED = '#ff5f57';
const MINIMIZE_YELLOW = '#febc2e';
const ZOOM_GREEN = '#28c840';

const CIRCLE = 'h-3.5 w-3.5 rounded-full shadow-[inset_0_0_0_0.5px_rgba(0,0,0,0.25)]';

interface DiagnosticsOverlayHeaderProps {
  title: string;
  onClose: () => void;
}

/**
 * The diagnostics overlay's header: the three window controls at the top-left,
 * where a macOS terminal keeps them, and the title beside them.
 *
 * Only the red one acts: it closes the overlay and, with it, every shell tab.
 * The overlay already fills the station's window and the Electron shell has
 * no minimise or zoom state for it, so yellow and green are drawn for fidelity
 * and are not controls: they take no focus and announce nothing.
 *
 * The red circle is small, as a window control is, but the button around it is
 * a full 24 px target, and the cross appears on hover and keyboard focus so the
 * control says what it does before it is pressed.
 */
export const DiagnosticsOverlayHeader: React.FC<DiagnosticsOverlayHeaderProps> = ({ title, onClose }) => (
  <header className="flex h-12 shrink-0 items-center justify-start gap-3 border-b border-strut-soft px-4">
    <div data-traffic-lights className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        onClick={onClose}
        aria-label="Fechar diagnóstico"
        title="Fechar"
        className="group grid h-6 w-6 shrink-0 place-items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[#ff5f57]/60"
      >
        <span
          data-close-circle
          className={`grid place-items-center transition-transform group-hover:scale-110 ${CIRCLE}`}
          style={{ backgroundColor: CLOSE_RED }}
        >
          <X
            size={9}
            strokeWidth={3}
            aria-hidden
            className="text-[#4d0000] opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          />
        </span>
      </button>
      <span aria-hidden className="grid h-6 w-6 place-items-center">
        <span data-minimize-circle className={CIRCLE} style={{ backgroundColor: MINIMIZE_YELLOW }} />
      </span>
      <span aria-hidden className="grid h-6 w-6 place-items-center">
        <span data-zoom-circle className={CIRCLE} style={{ backgroundColor: ZOOM_GREEN }} />
      </span>
    </div>
    <h2 className="font-cond text-base tracking-wide text-frost">{title}</h2>
  </header>
);
