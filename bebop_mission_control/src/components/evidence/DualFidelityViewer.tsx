import React, { useCallback, useRef, useState } from 'react';
import type { EvidenceItem } from '../../types/bmg';
import { RegistrationFrame } from '../ui/RegistrationFrame';
import { cn } from '../../lib/format';

interface DualFidelityViewerProps {
  item: EvidenceItem;
}

type Mode = 'split' | 'raw' | 'annotated';

/**
 * The two fidelities of one capture, on one frame.
 *
 * `accident_raw_*.png` is written losslessly at capture time; the annotated
 * JPEG carries the network's boxes. A wipe between them, rather than two
 * images side by side, keeps the same pixels under the same coordinates, which
 * is what makes it possible to tell whether a box sits on the right object.
 */
export const DualFidelityViewer: React.FC<DualFidelityViewerProps> = ({ item }) => {
  const [mode, setMode] = useState<Mode>('split');
  const [split, setSplit] = useState(0.5);
  const frameRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const updateFromPointer = useCallback((clientX: number) => {
    const el = frameRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setSplit(Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)));
  }, []);

  const hasBoth = Boolean(item.rawUrl && item.annotatedUrl);
  const base = item.rawUrl ?? item.annotatedUrl;
  const overlay = item.annotatedUrl ?? item.rawUrl;

  if (!base) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-haze">
        Esta captura não tem imagem em disco.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex items-center gap-1">
        {(
          [
            { id: 'split', label: 'Comparar' },
            { id: 'raw', label: 'Bruta' },
            { id: 'annotated', label: 'Anotada' },
          ] as { id: Mode; label: string }[]
        ).map((option) => (
          <button
            key={option.id}
            type="button"
            disabled={option.id === 'split' && !hasBoth}
            onClick={() => setMode(option.id)}
            className={cn(
              'rounded-bezel border px-3 py-1.5 text-xs transition-colors',
              mode === option.id
                ? 'border-strut-bright bg-hull-raise text-frost'
                : 'border-transparent text-haze hover:bg-hull-deck hover:text-frost',
              option.id === 'split' && !hasBoth && 'cursor-not-allowed opacity-40'
            )}
          >
            {option.label}
          </button>
        ))}
        <span className="ml-auto font-mono text-2xs text-haze-deep">
          {mode === 'split' ? `${Math.round(split * 100)}% bruta` : ''}
        </span>
      </div>

      <div
        ref={frameRef}
        className="relative min-h-0 flex-1 select-none overflow-hidden bg-black"
        onPointerDown={(e) => {
          if (mode !== 'split') return;
          draggingRef.current = true;
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
          updateFromPointer(e.clientX);
        }}
        onPointerMove={(e) => {
          if (mode === 'split' && draggingRef.current) updateFromPointer(e.clientX);
        }}
        onPointerUp={() => {
          draggingRef.current = false;
        }}
      >
        <img
          src={mode === 'annotated' ? overlay! : base}
          alt={mode === 'annotated' ? 'Imagem anotada pela rede neural' : 'Imagem bruta do sensor'}
          className="absolute inset-0 h-full w-full object-contain"
        />

        {mode === 'split' && hasBoth ? (
          <>
            <div
              className="absolute inset-0 overflow-hidden"
              style={{ clipPath: `inset(0 0 0 ${split * 100}%)` }}
            >
              <img
                src={item.annotatedUrl!}
                alt="Imagem anotada pela rede neural"
                className="absolute inset-0 h-full w-full object-contain"
              />
            </div>
            <div
              className="absolute inset-y-0 z-30 w-px cursor-col-resize bg-mint"
              style={{ left: `${split * 100}%` }}
            >
              <span className="absolute left-1/2 top-1/2 h-8 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-mint" />
            </div>
            <span className="absolute bottom-3 left-3 z-30 bg-hull-deep/85 px-2 py-1 text-2xs text-frost">
              Bruta · PNG sem perdas
            </span>
            <span className="absolute bottom-3 right-3 z-30 bg-hull-deep/85 px-2 py-1 text-2xs text-frost">
              Anotada · YOLOv8
            </span>
          </>
        ) : null}

        <RegistrationFrame tone="frost" inset={8} />
      </div>
    </div>
  );
};
