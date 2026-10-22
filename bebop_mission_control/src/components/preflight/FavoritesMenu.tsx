import React, { useEffect, useId, useRef, useState } from 'react';
import { Check, Pencil, Star, Trash2, X } from 'lucide-react';
import { Button } from '../ui/Button';
import { cn } from '../../lib/format';
import { MAX_PRESET_NAME, MAX_PRESETS, type OperatorPreset } from '../../lib/operatorPresets';

export interface FavoritesMenuProps {
  /** The favourites of the mode on screen, oldest first; the other mode's never appear. */
  presets: readonly OperatorPreset[];
  /** The favourite the working values hold (`operatorPresets.activePresetId`). */
  activePresetId: string | null;
  /** Save the working values under `name`; resolves why it was refused, or `null`. */
  onSave: (name: string) => string | null;
  onApply: (id: string) => void;
  /** Rename; resolves why it was refused, or `null`. */
  onRename: (id: string, name: string) => string | null;
  onDelete: (id: string) => void;
  /** Injected for tests; the wall clock otherwise. */
  now?: () => Date;
}

/** `iso` relative to `now`, as the operator reads it: "agora", "há 5 min", "há 3 dias" or the date. */
export function relativeDate(iso: string, now: Date): string {
  const then = new Date(iso);
  const seconds = (now.getTime() - then.getTime()) / 1000;
  if (!Number.isFinite(seconds)) return '';
  if (seconds < 60) return 'agora';
  if (seconds < 3600) return `há ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `há ${Math.floor(seconds / 3600)} h`;
  const days = Math.floor(seconds / 86400);
  if (days < 30) return days === 1 ? 'há 1 dia' : `há ${days} dias`;
  return then.toLocaleDateString('pt-BR');
}

type Editing = { kind: 'new' } | { kind: 'rename'; id: string } | { kind: 'delete'; id: string } | null;

/**
 * The operator's favourites of the current mode, as a disclosure menu.
 *
 * The trigger reads "Favoritos (N)". The panel lists each favourite with its
 * name, when it was saved, and apply, rename and delete actions; the one the
 * working values hold is marked. "Salvar configuração atual" opens a name
 * field in place. Delete asks first. Escape closes the panel and returns the
 * focus to the trigger; the arrow keys move between the favourites.
 */
export const FavoritesMenu: React.FC<FavoritesMenuProps> = ({
  presets,
  activePresetId,
  onSave,
  onApply,
  onRename,
  onDelete,
  now = () => new Date(),
}) => {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const full = presets.length >= MAX_PRESETS;

  const close = () => {
    setOpen(false);
    setEditing(null);
    setError(null);
    triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>('[data-favorite-apply], [data-favorite-new]')?.focus();
  }, [open]);

  const startEditing = (next: Editing, initial = '') => {
    setEditing(next);
    setDraft(initial);
    setError(null);
  };

  const commit = () => {
    if (editing?.kind === 'new') {
      const refused = onSave(draft);
      if (refused) return setError(refused);
    } else if (editing?.kind === 'rename') {
      const refused = onRename(editing.id, draft);
      if (refused) return setError(refused);
    }
    setEditing(null);
    setError(null);
  };

  const onPanelKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (editing) setEditing(null);
      else close();
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const targets = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('[data-favorite-apply]') ?? []);
    if (targets.length === 0) return;
    event.preventDefault();
    const index = targets.indexOf(document.activeElement as HTMLElement);
    const step = event.key === 'ArrowDown' ? 1 : -1;
    targets[(index + step + targets.length) % targets.length].focus();
  };

  const nameField = (label: string) => (
    <form
      className="flex items-center gap-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        commit();
      }}
    >
      <input
        autoFocus
        type="text"
        value={draft}
        maxLength={MAX_PRESET_NAME}
        aria-label={label}
        aria-invalid={error !== null}
        onChange={(event) => setDraft(event.target.value)}
        className="min-w-0 flex-1 rounded-bezel border border-strut-soft bg-abyss px-2 py-1 font-sans text-xs text-frost focus-visible:outline focus-visible:outline-2 focus-visible:outline-mint"
      />
      <Button type="submit" variant="ghost" icon={<Check size={12} />} aria-label="Confirmar nome" />
      <Button type="button" variant="quiet" icon={<X size={12} />} aria-label="Cancelar" onClick={() => setEditing(null)} />
    </form>
  );

  return (
    <div className="relative">
      <Button
        ref={triggerRef}
        variant="ghost"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
        icon={<Star size={13} fill={activePresetId ? 'currentColor' : 'none'} data-preset-star={activePresetId ? 'filled' : 'outline'} />}
        className={activePresetId ? 'text-amber hover:text-amber' : undefined}
      >
        Favoritos ({presets.length})
      </Button>
      {open ? (
        <div
          id={panelId}
          ref={panelRef}
          role="dialog"
          aria-label="Favoritos deste modo"
          onKeyDown={onPanelKeyDown}
          className="absolute bottom-full left-0 z-30 mb-2 w-[340px] rounded-panel border border-strut-soft bg-hull-deep p-2 shadow-xl"
        >
          {presets.length === 0 ? (
            <p className="px-2 py-1.5 font-sans text-2xs text-haze">Nenhum favorito salvo neste modo.</p>
          ) : (
            <ul className="flex max-h-[260px] flex-col gap-1 overflow-y-auto">
              {presets.map((preset) => {
                const active = preset.id === activePresetId;
                return (
                  <li
                    key={preset.id}
                    data-favorite={preset.id}
                    aria-current={active ? 'true' : undefined}
                    className={cn(
                      'rounded-bezel border px-2 py-1.5',
                      active ? 'border-amber/50 bg-amber/[0.06]' : 'border-transparent'
                    )}
                  >
                    {editing?.kind === 'rename' && editing.id === preset.id ? (
                      nameField(`Novo nome de ${preset.name}`)
                    ) : editing?.kind === 'delete' && editing.id === preset.id ? (
                      <div className="flex items-center gap-1.5">
                        <span className="min-w-0 flex-1 truncate font-sans text-xs text-frost">Excluir “{preset.name}”?</span>
                        <Button
                          variant="ghost"
                          className="text-ember"
                          onClick={() => {
                            onDelete(preset.id);
                            setEditing(null);
                          }}
                        >
                          Excluir
                        </Button>
                        <Button variant="quiet" onClick={() => setEditing(null)}>
                          Cancelar
                        </Button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          data-favorite-apply
                          onClick={() => {
                            onApply(preset.id);
                            close();
                          }}
                          className="flex min-w-0 flex-1 flex-col items-start rounded-bezel px-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-mint"
                          aria-label={`Aplicar ${preset.name}${active ? ' (ativo)' : ''}`}
                        >
                          <span className="flex w-full items-center gap-1.5 truncate font-sans text-xs text-frost">
                            {active ? <Star size={11} fill="currentColor" className="shrink-0 text-amber" aria-hidden /> : null}
                            <span className="truncate">{preset.name}</span>
                          </span>
                          <span className="font-sans text-3xs text-haze-deep">{relativeDate(preset.savedAt, now())}</span>
                        </button>
                        <Button
                          variant="quiet"
                          icon={<Pencil size={12} />}
                          aria-label={`Renomear ${preset.name}`}
                          onClick={() => startEditing({ kind: 'rename', id: preset.id }, preset.name)}
                        />
                        <Button
                          variant="quiet"
                          icon={<Trash2 size={12} />}
                          aria-label={`Excluir ${preset.name}`}
                          onClick={() => startEditing({ kind: 'delete', id: preset.id })}
                        />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-2 border-t border-strut-soft pt-2">
            {editing?.kind === 'new' ? (
              nameField('Nome do favorito')
            ) : (
              <Button
                variant="ghost"
                data-favorite-new
                disabled={full}
                title={full ? `Limite de ${MAX_PRESETS} favoritos por modo` : undefined}
                onClick={() => startEditing({ kind: 'new' })}
              >
                Salvar configuração atual
              </Button>
            )}
            {error ? (
              <p role="alert" className="mt-1.5 px-1 font-sans text-2xs text-ember">
                {error}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};
