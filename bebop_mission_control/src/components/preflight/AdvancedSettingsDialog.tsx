import React, { useEffect, useState } from 'react';
import { RotateCcw, Settings2 } from 'lucide-react';
import {
  ADVANCED_PATHS,
  CENTERING_TIMEOUT_RANGE_SEC,
  MARKER_DICTIONARIES,
  isKnownMarkerDictionary,
  markerCount,
} from '../../lib/parameterSchema';
import { advancedFieldErrors } from '../../lib/launchDocument';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import type { FieldConflict } from './ParameterSheet';
import { getPath } from '../../lib/paths';
import { Button } from '../ui/Button';
import { cn } from '../../lib/format';

const ID_PATH = 'rtl.target_aruco_id';
const DICT_PATH = 'rtl.marker_dict';
const TAG_PATH = 'rtl.tag_size';
const CENTERING_PATH = 'rtl.centering_timeout_sec';

/** Printed pad sizes the team uses, in metres. */
const TAG_SHORTCUTS: readonly number[] = [0.16, 0.2];
const TAG_STEP = 0.005;
const TAG_PRECISION = 3;

interface AdvancedSettingsDialogProps {
  working: ParamsDoc;
  /** `MissionParameters.factory()`; `null` until the mission has served it. */
  defaults: ParamsDoc | null;
  changedPaths: Set<string>;
  /** The conflict marking each path, once a save was attempted. */
  conflicts?: Partial<Record<string, FieldConflict>>;
  onEdit: (path: string, value: unknown) => void;
  onClose: () => void;
}

/** Text of a field as the operator types it; the document only receives a number. */
function useDraft(value: unknown): [string, (next: string) => void] {
  const text = typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
  const [draft, setDraft] = useState(text);
  useEffect(() => setDraft(text), [text]);
  return [draft, setDraft];
}

const parseNumber = (text: string): number | null => {
  if (text.trim() === '') return null;
  const value = Number(text.replace(',', '.'));
  return Number.isFinite(value) ? value : null;
};

const Field: React.FC<{
  label: string;
  hint: string;
  changed: boolean;
  error?: string;
  conflict?: FieldConflict;
  path: string;
  children: React.ReactNode;
}> = ({ label, hint, changed, error, conflict, path, children }) => (
  <div
    data-conflict={conflict?.severity}
    className={cn(
      'rounded-bezel border px-3.5 py-2.5 transition-colors duration-200',
      error || conflict?.severity === 'error'
        ? 'border-ember/60 bg-ember/[0.06]'
        : conflict?.severity === 'warning'
          ? 'border-amber/55 bg-amber/[0.06]'
          : changed
            ? 'border-mint/45 bg-mint/[0.06]'
            : 'border-strut-soft bg-abyss/40'
    )}
  >
    <div className="flex items-center gap-2">
      <span className="font-sans text-sm font-medium text-frost">{label}</span>
      {changed ? <span className="h-1.5 w-1.5 rounded-full bg-mint" aria-label="alterado" /> : null}
    </div>
    <p className="mt-0.5 font-sans text-2xs leading-relaxed text-haze">{hint}</p>
    <div className="mt-2">{children}</div>
    {error ? (
      <p data-field-error={path} className="mt-1.5 font-sans text-2xs leading-relaxed text-ember">
        {error}
      </p>
    ) : conflict ? (
      <p
        data-field-conflict={path}
        className={cn('mt-1.5 font-sans text-2xs leading-relaxed', conflict.severity === 'error' ? 'text-ember' : 'text-amber')}
      >
        {conflict.title}
      </p>
    ) : null}
  </div>
);

const inputClass =
  'tnum w-full rounded-bezel border border-strut bg-abyss/70 px-2.5 py-1.5 font-mono text-sm text-frost outline-none focus-visible:border-mint';

/**
 * "Configurações Avançadas": the identity of the landing pad Stage 5 looks
 * for, and how long it may spend centering over it. Edits go straight to the working document, so the sheet's own "Salvar"
 * commits them with everything else; nothing here saves on its own.
 *
 * Focus starts on "Fechar" and Escape answers it, as in the sheet's other
 * dialog. A dictionary change never rewrites the ID: an ID left outside the
 * new dictionary is shown as an error and blocks the launch until corrected.
 */
export const AdvancedSettingsDialog: React.FC<AdvancedSettingsDialogProps> = ({
  working,
  defaults,
  changedPaths,
  conflicts = {},
  onEdit,
  onClose,
}) => {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      onClose();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const errors = advancedFieldErrors(working);
  const dict = getPath(working, DICT_PATH);
  const count = markerCount(dict);
  const [idDraft, setIdDraft] = useDraft(getPath(working, ID_PATH));
  const [tagDraft, setTagDraft] = useDraft(getPath(working, TAG_PATH));
  const [centeringDraft, setCenteringDraft] = useDraft(getPath(working, CENTERING_PATH));
  const dictKnown = isKnownMarkerDictionary(dict);

  const restore = () => {
    if (!defaults) return;
    for (const path of ADVANCED_PATHS) {
      const value = getPath(defaults, path);
      if (value !== undefined) onEdit(path, value);
    }
  };

  const commitNumber = (path: string, text: string, setDraft: (next: string) => void) => {
    setDraft(text);
    const value = parseNumber(text);
    if (value !== null) onEdit(path, path === TAG_PATH ? Number(value.toFixed(TAG_PRECISION)) : value);
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-8">
      <div aria-hidden className="absolute inset-0 bg-abyss/60 backdrop-blur-[2px]" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="advanced-settings-title"
        className="anim-rise relative flex max-h-full w-full max-w-[460px] flex-col rounded-panel border border-strut bg-hull p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-bezel border border-strut-soft bg-hull-raise/60 text-frost-dim">
            <Settings2 size={15} strokeWidth={1.8} />
          </span>
          <div>
            <h3 id="advanced-settings-title" className="font-cond text-base font-semibold tracking-wide text-frost">
              Configurações Avançadas
            </h3>
            <p className="mt-1 text-2xs leading-relaxed text-haze">
              Marca física da base de pouso e janela de centragem, lidas pela etapa 5. As alterações entram no mesmo Salvar da ficha.
            </p>
          </div>
        </div>

        <div className="scroll-thin mt-4 flex min-h-0 flex-col gap-2.5 overflow-y-auto">
          <Field
            label="ID do marcador"
            hint={count !== null ? `IDs válidos: 0 a ${count - 1}` : 'IDs válidos: inteiros a partir de 0'}
            changed={changedPaths.has(ID_PATH)}
            error={errors[ID_PATH]}
            conflict={conflicts[ID_PATH]}
            path={ID_PATH}
          >
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              aria-label="ID do marcador"
              value={idDraft}
              onChange={(event) => commitNumber(ID_PATH, event.target.value, setIdDraft)}
              className={inputClass}
            />
          </Field>

          <Field
            label="Dicionário"
            hint="Família ArUco ou AprilTag impressa na base."
            changed={changedPaths.has(DICT_PATH)}
            error={errors[DICT_PATH]}
            conflict={conflicts[DICT_PATH]}
            path={DICT_PATH}
          >
            <select
              aria-label="Dicionário do marcador"
              value={dictKnown ? dict : '__current'}
              onChange={(event) => {
                if (event.target.value !== '__current') onEdit(DICT_PATH, event.target.value);
              }}
              className={inputClass}
            >
              {!dictKnown ? (
                <option value="__current">{`${String(dict)} (não reconhecido pela ficha)`}</option>
              ) : null}
              {MARKER_DICTIONARIES.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {`${entry.name} (${entry.markers} marcas)`}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="Tamanho da tag"
            hint="Aresta externa da borda preta, em metros. Um erro aqui escala toda distância estimada até a base."
            changed={changedPaths.has(TAG_PATH)}
            error={errors[TAG_PATH]}
            conflict={conflicts[TAG_PATH]}
            path={TAG_PATH}
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                inputMode="decimal"
                min={TAG_STEP}
                max={1}
                step={TAG_STEP}
                aria-label="Tamanho da tag"
                value={tagDraft}
                onChange={(event) => commitNumber(TAG_PATH, event.target.value, setTagDraft)}
                className={cn(inputClass, 'flex-1')}
              />
              <span className="font-mono text-2xs text-haze">m</span>
              {TAG_SHORTCUTS.map((size) => (
                <Button key={size} variant="quiet" onClick={() => onEdit(TAG_PATH, size)}>
                  {size.toFixed(2)}
                </Button>
              ))}
            </div>
          </Field>

          <Field
            label="Janela de centragem"
            hint="Tempo máximo para centralizar sobre a marca antes do pouso, separado do timeout de retorno. Ao expirar, a aeronave pousa onde estiver."
            changed={changedPaths.has(CENTERING_PATH)}
            error={errors[CENTERING_PATH]}
            conflict={conflicts[CENTERING_PATH]}
            path={CENTERING_PATH}
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                inputMode="decimal"
                min={CENTERING_TIMEOUT_RANGE_SEC.min}
                max={CENTERING_TIMEOUT_RANGE_SEC.max}
                step={1}
                aria-label="Janela de centragem"
                value={centeringDraft}
                onChange={(event) => commitNumber(CENTERING_PATH, event.target.value, setCenteringDraft)}
                className={cn(inputClass, 'flex-1')}
              />
              <span className="font-mono text-2xs text-haze">s</span>
            </div>
          </Field>
        </div>

        <div className="mt-5 flex justify-between gap-2">
          <Button
            variant="quiet"
            onClick={restore}
            disabled={!defaults}
            title={defaults ? undefined : 'Padrões da missão indisponíveis'}
            icon={<RotateCcw size={13} />}
          >
            Restaurar padrões avançados
          </Button>
          <Button autoFocus variant="ghost" onClick={onClose}>
            Fechar
          </Button>
        </div>
      </div>
    </div>
  );
};
