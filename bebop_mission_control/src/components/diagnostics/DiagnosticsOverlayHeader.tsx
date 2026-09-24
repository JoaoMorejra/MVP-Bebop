import React from 'react';
import { X } from 'lucide-react';

/** The window-control red the terminal's own title bar used. */
const CLOSE_RED = '#ff5f57';

interface DiagnosticsOverlayHeaderProps {
  title: string;
  onClose: () => void;
}

/**
 * The diagnostics overlay's header: the close control as a red circle at the
 * top-left, where a terminal window keeps it, and the title beside it.
 *
 * It is the only way to close the overlay by pointer; typing `exit` in the
 * shell remains the keyboard route. The circle is small, as a window control
 * is, but the button around it is a full 24 px target, and the cross appears
 * on hover and keyboard focus so the control says what it does before it is
 * pressed.
 */
export const DiagnosticsOverlayHeader: React.FC<DiagnosticsOverlayHeaderProps> = ({ title, onClose }) => (
  <header className="flex h-12 shrink-0 items-center justify-start gap-3 border-b border-strut-soft px-4">
    <button
      type="button"
      onClick={onClose}
      aria-label="Fechar diagnóstico"
      title="Fechar"
      className="group grid h-6 w-6 shrink-0 place-items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-[#ff5f57]/60"
    >
      <span
        data-close-circle
        className="grid h-3.5 w-3.5 place-items-center rounded-full shadow-[inset_0_0_0_0.5px_rgba(0,0,0,0.25)] transition-transform group-hover:scale-110"
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
    <h2 className="font-cond text-base tracking-wide text-frost">{title}</h2>
  </header>
);
