import React, { useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '../ui/Button';

interface DiscardChangesDialogProps {
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Asked when the parameter sheet is closed with edits not yet saved.
 *
 * Focus starts on "Continuar editando", and Escape answers it, so a reflexive
 * Enter or Escape keeps the draft: losing edits takes a deliberate click.
 */
export const DiscardChangesDialog: React.FC<DiscardChangesDialogProps> = ({ onConfirm, onCancel }) => {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      onCancel();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onCancel]);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-8">
      <div aria-hidden className="absolute inset-0 bg-abyss/60 backdrop-blur-[2px]" />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="discard-changes-title"
        aria-describedby="discard-changes-body"
        className="anim-rise relative w-full max-w-[420px] rounded-panel border border-strut bg-hull p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-bezel border border-amber/40 bg-amber/10 text-amber">
            <AlertTriangle size={15} strokeWidth={1.8} />
          </span>
          <div>
            <h3 id="discard-changes-title" className="font-cond text-base font-semibold tracking-wide text-frost">
              Fechar sem salvar
            </h3>
            <p id="discard-changes-body" className="mt-1 text-sm leading-relaxed text-haze">
              As alterações não salvas serão descartadas. Deseja continuar?
            </p>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button autoFocus variant="quiet" onClick={onCancel}>
            Continuar editando
          </Button>
          <Button variant="danger" onClick={onConfirm}>
            Descartar alterações
          </Button>
        </div>
      </div>
    </div>
  );
};
