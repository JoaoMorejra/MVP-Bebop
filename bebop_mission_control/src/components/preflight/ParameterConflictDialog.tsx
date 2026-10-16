import React, { useEffect } from 'react';
import { AlertOctagon, AlertTriangle } from 'lucide-react';
import { ALL_PARAMETERS } from '../../lib/parameterSchema';
import type { ParameterConflict } from '../../lib/parameterConflicts';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { Button } from '../ui/Button';
import { cn } from '../../lib/format';

/** Names of the fields a conflict can involve that the sheet's sliders do not show. */
const EXTRA_LABELS: Readonly<Record<string, { label: string; unit?: string }>> = {
  'rtl.reverse_cruise_velocity': { label: 'Velocidade de retorno', unit: 'm/s' },
  'kinematics.max_horizontal_speed': { label: 'Teto de velocidade horizontal', unit: 'm/s' },
  'rtl.target_aruco_id': { label: 'ID do marcador' },
  'rtl.marker_dict': { label: 'Dicionário do marcador' },
  'rtl.tag_size': { label: 'Tamanho da tag', unit: 'm' },
  'rtl.centering_timeout_sec': { label: 'Janela de centragem', unit: 's' },
};

/** Label, current value and unit of one involved field, as the operator reads it. */
export function describeField(working: ParamsDoc, path: string): { label: string; value: string } {
  const spec = ALL_PARAMETERS.find((item) => item.path === path);
  const raw = getPath(working, path);
  const label = spec?.label ?? EXTRA_LABELS[path]?.label ?? path;
  const unit = spec?.unit ?? EXTRA_LABELS[path]?.unit ?? '';
  const value =
    typeof raw === 'number' && Number.isFinite(raw)
      ? `${spec ? raw.toFixed(spec.precision) : String(raw)}${unit}`
      : raw === undefined
        ? 'ausente'
        : String(raw);
  return { label, value };
}

interface ParameterConflictDialogProps {
  /** `blocked`: an error is present and the save is refused. `confirm`: warnings only. */
  mode: 'blocked' | 'confirm';
  conflicts: ParameterConflict[];
  working: ParamsDoc;
  onBack: () => void;
  onConfirm: () => void;
}

/**
 * Why the parameters cannot be saved as they are, or what saving them costs.
 *
 * Opened by "Salvar". Focus starts on "Voltar e ajustar" and Escape answers
 * it, as in the sheet's other dialogs, so a reflexive Enter never saves a
 * degraded combination; with an error there is no way to save at all.
 */
export const ParameterConflictDialog: React.FC<ParameterConflictDialogProps> = ({
  mode,
  conflicts,
  working,
  onBack,
  onConfirm,
}) => {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      onBack();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onBack]);

  const blocked = mode === 'blocked';
  const Icon = blocked ? AlertOctagon : AlertTriangle;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-8">
      <div aria-hidden className="absolute inset-0 bg-abyss/60 backdrop-blur-[2px]" />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="parameter-conflict-title"
        aria-describedby="parameter-conflict-body"
        data-mode={mode}
        className="anim-rise relative flex max-h-full w-full max-w-[560px] flex-col rounded-panel border border-strut bg-hull p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span
            className={cn(
              'grid h-8 w-8 shrink-0 place-items-center rounded-bezel border',
              blocked ? 'border-ember/45 bg-ember/10 text-ember' : 'border-amber/40 bg-amber/10 text-amber'
            )}
          >
            <Icon size={15} strokeWidth={1.8} />
          </span>
          <div>
            <h3 id="parameter-conflict-title" className="font-cond text-base font-semibold tracking-wide text-frost">
              {blocked ? 'Combinação de parâmetros inexecutável' : 'Parâmetros com ressalvas'}
            </h3>
            <p id="parameter-conflict-body" className="mt-1 text-sm leading-relaxed text-haze">
              {blocked
                ? 'Os parâmetros não foram salvos. Corrija os itens abaixo para salvar e lançar.'
                : 'A missão executa, mas com os riscos abaixo. Salve mesmo assim apenas se for intencional.'}
            </p>
          </div>
        </div>

        <ul className="scroll-thin mt-4 flex min-h-0 flex-col gap-2.5 overflow-y-auto">
          {conflicts.map((conflict) => (
            <li
              key={conflict.id}
              data-conflict-id={conflict.id}
              className={cn(
                'rounded-bezel border px-3.5 py-2.5',
                conflict.severity === 'error' ? 'border-ember/50 bg-ember/[0.06]' : 'border-amber/45 bg-amber/[0.06]'
              )}
            >
              <div className="flex items-baseline gap-2">
                <span
                  className={cn(
                    'tnum font-mono text-3xs',
                    conflict.severity === 'error' ? 'text-ember' : 'text-amber'
                  )}
                >
                  {conflict.id}
                </span>
                <span className="font-sans text-sm font-medium text-frost">{conflict.title}</span>
              </div>
              <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-2xs text-frost-dim">
                {conflict.paths.map((path) => {
                  const field = describeField(working, path);
                  return (
                    <div key={path} className="flex gap-1.5">
                      <dt className="font-sans text-haze">{field.label}</dt>
                      <dd className="tnum">{field.value}</dd>
                    </div>
                  );
                })}
              </dl>
              <p className="mt-1.5 text-2xs leading-relaxed text-haze">{conflict.message}</p>
              <p className="mt-1 text-2xs leading-relaxed text-frost">{conflict.fix}</p>
            </li>
          ))}
        </ul>

        <div className="mt-5 flex justify-end gap-2">
          <Button autoFocus variant="quiet" onClick={onBack}>
            Voltar e ajustar
          </Button>
          {blocked ? null : (
            <Button variant="primary" onClick={onConfirm}>
              Salvar mesmo assim
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
