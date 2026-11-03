import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Camera, Check, Plane, QrCode, RotateCcw, Save, Star, Timer, X } from 'lucide-react';
import {
  ADVANCED_PATHS,
  CENTERING_TIMEOUT_RANGE_SEC,
  PARAMETER_GROUPS,
  groupsForMode,
  markerCount,
  type BooleanParameter,
  type NumberParameter,
  type ParameterGroup,
  type ParameterSpec,
} from '../../lib/parameterSchema';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { MISSION_MODES, readMissionMode, readRtlEnabled } from '../../lib/missionMode';
import { TAKEOFF_FROM_GROUND_PATH } from '../../lib/altitudeEnvelope';
import { MAX_TAG_SIZE_M, advancedFieldErrors } from '../../lib/launchDocument';
import { DirectionalButton } from '../ui/DirectionalButton';
import { ExactValueField, HybridSlider, REJECT_HOLD_MS } from '../ui/HybridSlider';
import { cn } from '../../lib/format';
import { activeConflicts, type ConflictSeverity } from '../../lib/parameterConflicts';

interface ParameterSheetProps {
  working: ParamsDoc;
  /** `MissionParameters.factory()`; `null` until the mission has served it. */
  defaults: ParamsDoc | null;
  changedPaths: Set<string>;
  dirty: boolean;
  saving: boolean;
  /** The working values equal the favourite of the mode on screen; the star is filled. */
  favoriteActive: boolean;
  /** The mode on screen has a favourite; "Restaurar Padrões" reapplies it instead of the factory values. */
  hasFavorite: boolean;
  onEdit: (path: string, value: unknown) => void;
  onSave: () => void;
  onSaveFavorite: () => void;
  onRestoreFavorite: () => void;
  /** Field of the last launch document the mission refused (`bmg:launch-refused`); marked as an error. */
  refusedPath?: string | null;
  /** An altitude the envelope of the launch surface moved, shown above the cards. */
  altitudeNotice?: string | null;
  /**
   * Bumped by the screen when the operator tries to leave with edits pending:
   * Salvar shakes once for each bump, asking to be pressed before leaving.
   */
  nudgeSave?: number;
  /** Leave the sheet: the screen decides whether pending edits shake Salvar first. */
  onClose?: () => void;
}

/** The conflict a field is marked with: its most severe one, by title. */
export type FieldConflict = { severity: ConflictSeverity; title: string };

/** A section of the sheet: a schema group, or the pad marker fields of the return. */
interface SheetSection {
  id: string;
  title: string;
  icon: React.ElementType;
  items: ParameterSpec[];
}

const GROUP_ICON: Record<string, React.ElementType> = {
  envelope: Plane,
  gimbal: Camera,
  timeouts: Timer,
};

/** Names the sheet gives a field instead of the schema's, where the control says it differently. */
const LABEL_OVERRIDE: Readonly<Record<string, string>> = {
  [TAKEOFF_FROM_GROUND_PATH]: 'Ponto de Decolagem',
};

/** What the RTL toggle says it is, on and off. */
const RTL_STATES: readonly [on: string, off: string] = ['Ativo', 'Desligado'];

const MARKER_ID_PATH = 'rtl.target_aruco_id';
const MARKER_DICT_PATH = 'rtl.marker_dict';
const TAG_SIZE_PATH = 'rtl.tag_size';
const CENTERING_PATH = 'rtl.centering_timeout_sec';

/**
 * Figures typed or scrubbed, without a slider: a marker ID and a printed size
 * are read off the pad, not swept across a range.
 */
const SCRUB_PATHS: ReadonlySet<string> = new Set([MARKER_ID_PATH, TAG_SIZE_PATH]);

/** IDs a marker can have when the dictionary is one the sheet does not know. */
const FALLBACK_MARKER_COUNT = 1000;

/**
 * The pad marker of the return, as tiles like every other number: its ID in
 * the dictionary the document holds, its printed size and the centering
 * window. The dictionary itself is not offered; the ID is bounded by it.
 */
function arucoSpecs(doc: unknown): NumberParameter[] {
  const count = markerCount(getPath(doc, MARKER_DICT_PATH)) ?? FALLBACK_MARKER_COUNT;
  return [
    { kind: 'number', path: MARKER_ID_PATH, label: 'ID do Marcador', hint: '', min: 0, max: count - 1, step: 1, precision: 0 },
    { kind: 'number', path: TAG_SIZE_PATH, label: 'Tamanho da Tag', hint: '', min: 0.05, max: MAX_TAG_SIZE_M, step: 0.005, precision: 3, unit: 'm' },
    {
      kind: 'number',
      path: CENTERING_PATH,
      label: 'Janela de Centragem',
      hint: '',
      min: CENTERING_TIMEOUT_RANGE_SEC.min,
      max: CENTERING_TIMEOUT_RANGE_SEC.max,
      step: 1,
      precision: 0,
      unit: 's',
    },
  ];
}

/**
 * The figure at `spec.path`, or `null`. Never a default: a missing value shown
 * as one would be launched as one, and the operator never chose it.
 */
const readNumber = (doc: ParamsDoc | null, spec: ParameterSpec): number | null => {
  const raw = getPath(doc, spec.path);
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
};

/** The boolean at `spec.path`, or `null` when the document holds no boolean there. */
const readBoolean = (doc: ParamsDoc | null, spec: ParameterSpec): boolean | null => {
  const raw = getPath(doc, spec.path);
  return typeof raw === 'boolean' ? raw : null;
};

/** The tile's state for its edge and fill: an error outranks an unsaved change. */
function tileState(conflict: FieldConflict | undefined, changed: boolean): string | undefined {
  if (conflict) return conflict.severity;
  return changed ? 'changed' : undefined;
}

const ChangedDot: React.FC<{ changed: boolean }> = ({ changed }) =>
  changed ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-mint" aria-label="alterado" /> : null;

const TileLabel: React.FC<{ spec: ParameterSpec; changed: boolean }> = ({ spec, changed }) => (
  <span className="flex items-start gap-2">
    <span className="param-label">{LABEL_OVERRIDE[spec.path] ?? spec.label}</span>
    <ChangedDot changed={changed} />
  </span>
);

const MissingValue: React.FC<{ spec: ParameterSpec }> = ({ spec }) => (
  <div data-missing-value={spec.path} className="param-tile" data-state="warning">
    <span className="param-label">{LABEL_OVERRIDE[spec.path] ?? spec.label}</span>
    <p className="mt-auto text-[13px] text-amber">Valor ausente</p>
  </div>
);

/**
 * Where the aircraft takes off from, as two options side by side with one
 * thumb sliding to the chosen one. Radio inputs underneath, so the group is a
 * real radiogroup to the keyboard and to a screen reader; the document still
 * receives the boolean it always did (`platform.takeoff_from_ground`).
 */
const TakeoffPointTile: React.FC<{
  spec: BooleanParameter;
  value: boolean | null;
  changed: boolean;
  conflict?: FieldConflict;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, changed, conflict, onEdit }) => {
  const name = `parameter-${spec.path.replace(/\./g, '-')}`;
  const options = [
    { label: 'Solo', value: true },
    { label: 'Totem', value: false },
  ] as const;
  return (
    <div
      data-parameter={spec.path}
      data-conflict={conflict?.severity}
      data-conflict-reason={conflict?.title}
      data-state={tileState(conflict, changed)}
      className="param-tile"
    >
      <TileLabel spec={spec} changed={changed} />
      <div role="radiogroup" aria-label={LABEL_OVERRIDE[spec.path]} className="param-options" data-choice={value ? 'first' : 'second'}>
        <span className="param-options-thumb" aria-hidden />
        {options.map((option) => (
          <label key={option.label} className="param-option" data-checked={value === option.value || undefined}>
            <input
              type="radio"
              name={name}
              checked={value === option.value}
              onChange={() => onEdit(spec.path, option.value)}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </div>
    </div>
  );
};

/**
 * A yes/no parameter: its name, what it means right now in one word, and a
 * toggle. The toggle is the native checkbox, drawn as the switch of the top
 * bar menu, inside its label, so the whole tile toggles it, the keyboard
 * reaches it and a screen reader announces it as a switch.
 */
const BooleanTile: React.FC<{
  spec: BooleanParameter;
  value: boolean | null;
  changed: boolean;
  conflict?: FieldConflict;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, changed, conflict, onEdit }) => {
  const id = `parameter-${spec.path.replace(/\./g, '-')}`;
  const [on, off] = RTL_STATES;
  return (
    <div
      data-parameter={spec.path}
      data-conflict={conflict?.severity}
      data-conflict-reason={conflict?.title}
      data-state={tileState(conflict, changed)}
      className="param-tile param-tile-boolean"
    >
      <label htmlFor={id} className="flex h-full cursor-pointer flex-col">
        <TileLabel spec={spec} changed={changed} />
        <span className="param-boolean-row mt-auto flex items-center justify-between gap-3 pb-1">
          <span key={String(value)} className="param-state anim-param-state">
            {value ? on : off}
          </span>
          <input
            id={id}
            type="checkbox"
            role="switch"
            checked={value === true}
            onChange={(event) => onEdit(spec.path, event.target.checked)}
            className="param-switch"
          />
        </span>
      </label>
    </div>
  );
};

/**
 * One number: its name, the figure in large type and editable in place, a
 * slider across the whole range with the default as a tick, and the range
 * printed under it with the default between the ends. A figure typed out of
 * range is corrected and said, and an unreadable one refused; the field shakes
 * and the reason takes the last line for a moment.
 */
const NumberTile: React.FC<{
  spec: NumberParameter;
  value: number | null;
  defaultValue: number | null;
  changed: boolean;
  /** An error on the field: its edge turns red and its title carries the reason. */
  conflict?: FieldConflict;
  /** Why the field cannot be edited now (`ParameterSpec.disabledReason`); `null` when it can. */
  locked: string | null;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, defaultValue, changed, conflict, locked, onEdit }) => {
  const [rejection, setRejection] = useState<string | null>(null);
  useEffect(() => {
    if (!rejection) return;
    const id = window.setTimeout(() => setRejection(null), REJECT_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [rejection]);

  if (value === null) return <MissingValue spec={spec} />;

  const atDefault = defaultValue !== null && Math.abs(value - defaultValue) < 10 ** -(spec.precision + 1);
  const edit = (next: number) => onEdit(spec.path, next);
  const scrub = SCRUB_PATHS.has(spec.path);
  const bound = (n: number) => n.toFixed(spec.precision);

  return (
    <div
      data-parameter={spec.path}
      data-conflict={conflict?.severity}
      data-conflict-reason={conflict?.title}
      data-locked={locked !== null ? 'true' : undefined}
      data-state={tileState(conflict, changed)}
      aria-disabled={locked !== null ? true : undefined}
      className={cn('param-tile', locked !== null && 'param-tile-locked')}
    >
      <TileLabel spec={spec} changed={changed} />

      <div className="mt-1.5">
        {locked !== null ? (
          <span className="param-figure tnum">
            {value.toFixed(spec.precision)}
            <span className="param-unit">{spec.unit}</span>
          </span>
        ) : (
          <ExactValueField
            value={value}
            min={spec.min}
            max={spec.max}
            precision={spec.precision}
            unit={spec.unit}
            label={LABEL_OVERRIDE[spec.path] ?? spec.label}
            variant="display"
            scrubStep={scrub ? spec.step : undefined}
            onCommit={edit}
            onReject={setRejection}
          />
        )}
      </div>

      {scrub ? null : (
        <HybridSlider
          value={value}
          min={spec.min}
          max={spec.max}
          step={spec.step}
          precision={spec.precision}
          label={LABEL_OVERRIDE[spec.path] ?? spec.label}
          marker={defaultValue ?? undefined}
          onChange={edit}
          disabled={locked !== null}
          className="mt-2"
        />
      )}

      {rejection ? (
        <p role="alert" className="param-foot-note param-reject text-ember">
          {rejection}
        </p>
      ) : locked !== null ? (
        <p className="param-foot-note text-frost/55">{locked}</p>
      ) : (
        <div className="param-range tnum">
          <span>{bound(spec.min)}</span>
          {defaultValue !== null ? (
            <span className={atDefault ? 'text-frost/40' : 'text-frost/75'}>
              padrão {defaultValue.toFixed(spec.precision)}
              {spec.unit ?? ''}
            </span>
          ) : null}
          <span>{bound(spec.max)}</span>
        </div>
      )}
    </div>
  );
};

/** One field of a section, by kind; a value the document lacks is named as missing. */
const ParameterTile: React.FC<{
  spec: ParameterSpec;
  working: ParamsDoc;
  defaults: ParamsDoc | null;
  changed: boolean;
  conflict?: FieldConflict;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, working, defaults, changed, conflict, onEdit }) => {
  if (spec.kind === 'boolean') {
    const value = readBoolean(working, spec);
    if (value === null && getPath(working, spec.path) !== undefined) return <MissingValue spec={spec} />;
    return spec.path === TAKEOFF_FROM_GROUND_PATH ? (
      <TakeoffPointTile spec={spec} value={value} changed={changed} conflict={conflict} onEdit={onEdit} />
    ) : (
      <BooleanTile spec={spec} value={value} changed={changed} conflict={conflict} onEdit={onEdit} />
    );
  }
  return (
    <NumberTile
      spec={spec}
      value={readNumber(working, spec)}
      defaultValue={readNumber(defaults, spec)}
      changed={changed}
      conflict={conflict}
      locked={spec.disabledReason?.(working) ?? null}
      onEdit={onEdit}
    />
  );
};

const Section: React.FC<{
  section: SheetSection;
  working: ParamsDoc;
  defaults: ParamsDoc | null;
  changedPaths: Set<string>;
  errors: Partial<Record<string, FieldConflict>>;
  onEdit: (path: string, value: unknown) => void;
}> = ({ section, working, defaults, changedPaths, errors, onEdit }) => {
  const Icon = section.icon;
  const changed = section.items.filter((item) => changedPaths.has(item.path)).length;
  const errorCount = section.items.filter((item) => errors[item.path]).length;

  return (
    <section className="param-section" data-section={section.id} data-live={changed > 0 || undefined}>
      <header className="flex items-center gap-2.5 px-1 pb-3">
        <span className="param-section-icon">
          <Icon size={15} strokeWidth={1.9} />
        </span>
        <h3 className="title-pixel font-pixel text-[14.5px] font-semibold tracking-[0.02em] text-frost">{section.title}</h3>
        <span className="ml-auto flex items-center gap-1.5">
          {errorCount > 0 ? (
            <span data-group-conflicts={section.id} className="tnum rounded-full bg-ember/15 px-2 py-0.5 text-[11px] font-medium text-ember">
              {errorCount}
            </span>
          ) : null}
          {changed > 0 ? (
            <span className="tnum rounded-full bg-mint/15 px-2 py-0.5 text-[11px] font-medium text-mint">{changed}</span>
          ) : null}
        </span>
      </header>

      <div className="grid min-h-0 flex-1 gap-2.5" style={{ gridTemplateColumns: `repeat(${section.items.length}, minmax(0, 1fr))` }}>
        {section.items.map((spec) => (
          <ParameterTile
            key={spec.path}
            spec={spec}
            working={working}
            defaults={defaults}
            changed={changedPaths.has(spec.path)}
            conflict={errors[spec.path]}
            onEdit={onEdit}
          />
        ))}
      </div>
    </section>
  );
};

/** Least time the dots of a save stay up, so a fast write still reads as one, ms. */
const SAVE_MIN_LOADING_MS = 700;
/** How long "Salvo" holds before the button settles into "Tudo salvo", ms. */
const SAVE_DONE_HOLD_MS = 1500;

type SavePhase = 'idle' | 'loading' | 'done';

/**
 * Salvar, in its states (kinetics "submit states"): "Salvar" while edits are
 * pending; three dots bouncing while the document is written; a tick drawing
 * itself with "Salvo" once it is, which holds and settles into "Tudo salvo",
 * the button's word for nothing left to save. A combination the mission
 * cannot fly keeps it shut. Each `nudge` shakes it once.
 */
const SaveButton: React.FC<{ dirty: boolean; saving: boolean; blocked: boolean; nudge: number; onSave: () => void }> = ({
  dirty,
  saving,
  blocked,
  nudge,
  onSave,
}) => {
  const [phase, setPhase] = useState<SavePhase>('idle');
  const loadingSince = useRef(0);
  const wasSaving = useRef(saving);
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const was = wasSaving.current;
    wasSaving.current = saving;
    if (saving && !was) {
      loadingSince.current = Date.now();
      setPhase('loading');
      return;
    }
    if (!saving && was) {
      const wait = Math.max(0, SAVE_MIN_LOADING_MS - (Date.now() - loadingSince.current));
      const id = window.setTimeout(() => setPhase(dirty ? 'idle' : 'done'), wait);
      return () => window.clearTimeout(id);
    }
  }, [saving, dirty]);

  useEffect(() => {
    if (phase !== 'done') return;
    const id = window.setTimeout(() => setPhase('idle'), SAVE_DONE_HOLD_MS);
    return () => window.clearTimeout(id);
  }, [phase]);

  useEffect(() => {
    const button = buttonRef.current;
    if (!nudge || !button) return;
    button.classList.remove('is-shaking');
    void button.offsetWidth;
    button.classList.add('is-shaking');
  }, [nudge]);

  return (
    <DirectionalButton
      ref={buttonRef}
      tone={dirty || phase !== 'idle' ? 'primary' : 'ghost'}
      onClick={onSave}
      onAnimationEnd={(event) => event.currentTarget.classList.remove('is-shaking')}
      disabled={!dirty || saving || blocked || phase !== 'idle'}
      data-save-phase={phase}
      data-blocked={blocked || undefined}
      className="save-btn min-w-[148px] justify-center"
    >
      {phase === 'loading' ? (
        <span key="loading" className="save-face" aria-live="polite">
          <span className="save-dots" aria-hidden>
            <i />
            <i />
            <i />
          </span>
          <span className="sr-only">Salvando</span>
        </span>
      ) : phase === 'done' ? (
        <span key="done" className="save-face" aria-live="polite">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden className="save-check">
            <path d="M5 12.5l4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Salvo
        </span>
      ) : dirty ? (
        <span key="idle" className="save-face">
          <Save size={15} strokeWidth={1.9} aria-hidden />
          Salvar
        </span>
      ) : (
        <span key="clean" className="save-face">
          <Check size={15} strokeWidth={2.2} aria-hidden />
          Tudo salvo
        </span>
      )}
    </DirectionalButton>
  );
};

/**
 * The parameters that define the mission, on one screen.
 *
 * The flight envelope and the camera share the top row; the time limits and,
 * when the flight returns, the pad marker share the row below. Every number is
 * the same tile and every yes/no the same control, so one is read like all the
 * others.
 *
 * There are no warnings to read and no dialog to answer. A combination the
 * mission cannot fly turns the fields involved red, with the reason in their
 * title, and keeps Salvar shut; anything it can fly saves as it is.
 *
 * Saving writes the whole document, so the fields not shown here — PID gains,
 * jerk ceilings, calibration statistics, the marker dictionary — keep the
 * values the mission last wrote. "Restaurar Padrões" reapplies the favourite
 * of the mode when there is one, and otherwise resets the sheet fields to the
 * mission's own defaults. Neither persists: the change reads as pending.
 */
export const ParameterSheet: React.FC<ParameterSheetProps> = ({
  working,
  defaults,
  changedPaths,
  dirty,
  saving,
  favoriteActive,
  hasFavorite,
  onEdit,
  onSave,
  onSaveFavorite,
  onRestoreFavorite,
  refusedPath = null,
  altitudeNotice = null,
  nudgeSave = 0,
  onClose,
}) => {
  const missionMode = readMissionMode(working);
  const groups = useMemo(() => groupsForMode(missionMode, working), [missionMode, working]);
  // The pad marker fields belong to the return: a flight that lands in place
  // reads none of them.
  const returns = readRtlEnabled(working);
  const modeLabel = MISSION_MODES.find((mode) => mode.id === missionMode)?.label ?? missionMode;

  const errors = useMemo(() => {
    const byPath: Partial<Record<string, FieldConflict>> = {};
    for (const conflict of activeConflicts(working)) {
      if (conflict.severity !== 'error') continue;
      for (const path of conflict.paths) byPath[path] ??= { severity: 'error', title: conflict.title };
    }
    if (returns) {
      const fieldErrors = advancedFieldErrors(working);
      for (const path of [MARKER_ID_PATH, TAG_SIZE_PATH, CENTERING_PATH]) {
        const message = fieldErrors[path];
        if (message) byPath[path] ??= { severity: 'error', title: message };
      }
    }
    if (refusedPath && !byPath[refusedPath]) byPath[refusedPath] = { severity: 'error', title: 'Recusado pela missão no lançamento' };
    return byPath;
  }, [working, returns, refusedPath]);
  const blocked = Object.keys(errors).length > 0;

  const sections = useMemo(() => {
    const asSection = (group: ParameterGroup): SheetSection => ({
      id: group.id,
      title: group.title,
      icon: GROUP_ICON[group.id],
      items: group.items,
    });
    const byId = (id: string) => groups.find((group) => group.id === id);
    const top = ['envelope', 'gimbal'].map(byId).filter((group): group is ParameterGroup => Boolean(group)).map(asSection);
    const bottom = ['timeouts'].map(byId).filter((group): group is ParameterGroup => Boolean(group)).map(asSection);
    if (returns) bottom.push({ id: 'aruco', title: 'Pouso ArUco', icon: QrCode, items: arucoSpecs(working) });
    return [top, bottom].filter((row) => row.length > 0);
  }, [groups, returns, working]);

  const restoreDefaults = () => {
    if (hasFavorite) {
      onRestoreFavorite();
      return;
    }
    if (!defaults) return;
    for (const group of PARAMETER_GROUPS) {
      for (const spec of group.items) {
        const value = spec.kind === 'boolean' ? readBoolean(defaults, spec) : readNumber(defaults, spec);
        if (value !== null) onEdit(spec.path, value);
      }
    }
    for (const path of ADVANCED_PATHS) {
      const value = getPath(defaults, path);
      if (value !== undefined) onEdit(path, value);
    }
  };

  return (
    <div className="param-sheet">
      <div data-sheet-header className="flex shrink-0 items-center justify-between gap-4 px-7 pb-4 pt-6">
        <div className="flex items-center gap-2">
          <h2 className="title-pixel font-pixel text-[23px] font-semibold leading-none tracking-[0.01em] text-white">{modeLabel}</h2>
          <DirectionalButton
            tone="icon"
            onClick={onSaveFavorite}
            aria-label="Salvar como favorito"
            aria-pressed={favoriteActive}
            data-filled={favoriteActive}
            className="param-star"
          >
            <Star size={17} strokeWidth={1.9} aria-hidden className={favoriteActive ? 'fill-mint text-mint' : 'text-frost/70'} />
          </DirectionalButton>
        </div>
        {onClose ? (
          <DirectionalButton tone="icon" onClick={onClose} aria-label="Fechar" className="param-close">
            <X size={18} strokeWidth={2} aria-hidden />
          </DirectionalButton>
        ) : null}
      </div>
      {altitudeNotice ? (
        <p role="status" data-altitude-notice className="mx-7 mb-3 shrink-0 rounded-full border border-amber/30 bg-amber/[0.08] px-4 py-1.5 text-[12.5px] text-amber">
          {altitudeNotice}
        </p>
      ) : null}

      <div className="flex min-h-0 flex-1 flex-col gap-3 px-5">
        {sections.map((row, index) => (
          <div
            key={index}
            className="grid min-h-0 gap-3"
            style={{ gridTemplateColumns: row.map((section) => `${section.items.length}fr`).join(' ') }}
          >
            {row.map((section) => (
              <Section
                key={section.id}
                section={section}
                working={working}
                defaults={defaults}
                changedPaths={changedPaths}
                errors={errors}
                onEdit={onEdit}
              />
            ))}
          </div>
        ))}
      </div>

      <div className="flex shrink-0 items-center gap-2 px-7 pb-6 pt-4">
        <DirectionalButton
          tone="ghost"
          onClick={restoreDefaults}
          disabled={!hasFavorite && !defaults}
        >
          <RotateCcw size={15} strokeWidth={1.9} aria-hidden />
          Restaurar Padrões
        </DirectionalButton>

        <div className="ml-auto">
          <SaveButton dirty={dirty} saving={saving} blocked={blocked} nudge={nudgeSave} onSave={onSave} />
        </div>
      </div>
    </div>
  );
};
