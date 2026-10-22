import React, { useEffect, useMemo, useState } from 'react';
import { Camera, Plane, RotateCcw, Save, Settings2, Timer, Undo2 } from 'lucide-react';
import {
  PARAMETER_GROUPS,
  groupsForMode,
  type BooleanParameter,
  type NumberParameter,
  type ParameterGroup,
  type ParameterGroupId,
  type ParameterSpec,
} from '../../lib/parameterSchema';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { MISSION_MODES, readMissionMode, readRtlEnabled } from '../../lib/missionMode';
import { Button } from '../ui/Button';
import { ExactValueField, HybridSlider } from '../ui/HybridSlider';
import { cn } from '../../lib/format';
import { launchesFromGround, padHeightM } from '../../lib/altitudeEnvelope';
import { AdvancedSettingsDialog } from './AdvancedSettingsDialog';
import { FavoritesMenu } from './FavoritesMenu';
import type { OperatorPreset } from '../../lib/operatorPresets';
import { ParameterConflictDialog } from './ParameterConflictDialog';
import {
  activeConflicts,
  type ConflictSeverity,
  type ParameterConflict,
} from '../../lib/parameterConflicts';

interface ParameterSheetProps {
  working: ParamsDoc;
  /** `MissionParameters.factory()`; `null` until the mission has served it. */
  defaults: ParamsDoc | null;
  changedPaths: Set<string>;
  dirty: boolean;
  saving: boolean;
  /** The favourites of the mode on screen (`useMissionParameters.presets`). */
  presets: readonly OperatorPreset[];
  /** The favourite the working values hold; its star is filled. */
  activePresetId: string | null;
  /** The mission parameters refused a save for an error conflict (`useMissionParameters`). */
  saveAttempted?: boolean;
  onEdit: (path: string, value: unknown) => void;
  onSave: () => void;
  onDiscard: () => void;
  onSavePreset: (name: string) => string | null;
  onApplyPreset: (id: string) => void;
  onRenamePreset: (id: string, name: string) => string | null;
  onDeletePreset: (id: string) => void;
  /** Field of the last launch document the mission refused (`bmg:launch-refused`); marked as an error. */
  refusedPath?: string | null;
  /** An altitude the envelope of the launch surface moved, shown above the cards. */
  altitudeNotice?: string | null;
}

/** The conflict a field is marked with: its most severe one, by title. */
export type FieldConflict = { severity: ConflictSeverity; title: string };

/** Each involved path's most severe conflict; an error outranks a warning. */
function conflictsByPath(conflicts: ParameterConflict[]): Partial<Record<string, FieldConflict>> {
  const byPath: Partial<Record<string, FieldConflict>> = {};
  for (const conflict of conflicts) {
    for (const path of conflict.paths) {
      const current = byPath[path];
      if (!current || (current.severity === 'warning' && conflict.severity === 'error')) {
        byPath[path] = { severity: conflict.severity, title: conflict.title };
      }
    }
  }
  return byPath;
}

const GROUP_ICON: Record<ParameterGroupId, React.ElementType> = {
  envelope: Plane,
  gimbal: Camera,
  timeouts: Timer,
};

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

/** Border and fill of a field card: a conflict outranks an unsaved change. */
function fieldTone(conflict: FieldConflict | undefined, changed: boolean): string {
  if (conflict?.severity === 'error') return 'border-ember/60 bg-ember/[0.06]';
  if (conflict?.severity === 'warning') return 'border-amber/55 bg-amber/[0.06]';
  return changed ? 'border-mint/45 bg-mint/[0.06]' : 'border-strut-soft bg-abyss/40';
}

const MissingValue: React.FC<{ spec: ParameterSpec }> = ({ spec }) => (
  <div data-missing-value={spec.path} className="rounded-bezel border border-amber/45 bg-amber/[0.06] px-3.5 py-2.5">
    <span className="font-sans text-sm font-medium text-frost">{spec.label}</span>
    <p className="mt-0.5 font-sans text-2xs leading-relaxed text-amber">
      Valor ausente no documento de parâmetros. Restaure os padrões ou ajuste antes de lançar.
    </p>
  </div>
);

const ConflictNote: React.FC<{ conflict?: FieldConflict }> = ({ conflict }) =>
  conflict ? (
    <p
      className={cn(
        'mt-1.5 font-sans text-2xs leading-relaxed',
        conflict.severity === 'error' ? 'text-ember' : 'text-amber'
      )}
    >
      {conflict.title}
    </p>
  ) : null;

/**
 * A yes/no parameter: a native checkbox inside its label, so the whole card
 * row toggles it, the keyboard reaches it and a screen reader announces it.
 */
const BooleanControl: React.FC<{
  spec: BooleanParameter;
  value: boolean | null;
  changed: boolean;
  conflict?: FieldConflict;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, changed, conflict, onEdit }) => {
  const id = `parameter-${spec.path.replace(/\./g, '-')}`;
  return (
    <div
      data-parameter={spec.path}
      data-conflict={conflict?.severity}
      className={cn('rounded-bezel border px-3.5 py-2.5 transition-colors duration-200', fieldTone(conflict, changed))}
    >
      <label htmlFor={id} className="flex cursor-pointer items-start gap-3">
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          aria-describedby={`${id}-hint`}
          onChange={(event) => onEdit(spec.path, event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded-sm border border-strut-bright bg-abyss accent-mint focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mint"
        />
        <span className="min-w-0">
          <span className="flex items-center gap-2">
            <span className="font-sans text-sm font-medium text-frost">{spec.label}</span>
            {changed ? <span className="h-1.5 w-1.5 rounded-full bg-mint" aria-label="alterado" /> : null}
          </span>
          <span id={`${id}-hint`} className="mt-0.5 block font-sans text-2xs leading-relaxed text-haze">
            {spec.hint}
          </span>
        </span>
      </label>
      <ConflictNote conflict={conflict} />
    </div>
  );
};

/** The two plan chips: how the flight ends and where it takes off from (section 5.2). */
export const PlanChips: React.FC<{ doc: unknown; className?: string }> = ({ doc, className }) => {
  const ground = launchesFromGround(doc);
  const pad = padHeightM(doc);
  return (
    <div className={cn('flex flex-wrap items-center justify-end gap-1.5', className)} data-plan-chips>
      <span data-plan-chip="end" className="rounded-full border border-strut-soft bg-abyss/50 px-2.5 py-0.5 font-sans text-3xs text-frost">
        {readRtlEnabled(doc) ? 'Final: RTL + pouso de precisão' : 'Final: pouso no local'}
      </span>
      <span data-plan-chip="surface" className="rounded-full border border-strut-soft bg-abyss/50 px-2.5 py-0.5 font-sans text-3xs text-frost">
        {ground ? 'Decolagem: solo' : `Decolagem: totem ${pad.toFixed(2).replace('.', ',')} m`}
      </span>
    </div>
  );
};

/** One field of a card, by kind; a value the document lacks is named as missing. */
const ParameterControl: React.FC<{
  spec: ParameterSpec;
  working: ParamsDoc;
  defaults: ParamsDoc | null;
  changed: boolean;
  conflict?: FieldConflict;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, working, defaults, changed, conflict, onEdit }) => {
  if (spec.kind === 'boolean') {
    const value = readBoolean(working, spec);
    return value === null && getPath(working, spec.path) !== undefined ? (
      <MissingValue spec={spec} />
    ) : (
      <BooleanControl spec={spec} value={value} changed={changed} conflict={conflict} onEdit={onEdit} />
    );
  }
  return (
    <NumberControl
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

/**
 * One parameter: its name, the exact figure, a slider across the full range
 * and the range itself printed at both ends, so the operator sees where in the
 * envelope the value sits without having to know the bounds.
 */
const NumberControl: React.FC<{
  spec: NumberParameter;
  value: number | null;
  defaultValue: number | null;
  changed: boolean;
  /** Shown only once a save was attempted; follows the edits live. */
  conflict?: FieldConflict;
  /** Why the field cannot be edited now (`ParameterSpec.disabledReason`); `null` when it can. */
  locked: string | null;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, defaultValue, changed, conflict, locked, onEdit }) => {
  const atDefault =
    value !== null && defaultValue !== null && Math.abs(value - defaultValue) < 10 ** -(spec.precision + 1);
  const edit = (next: number) => onEdit(spec.path, next);

  if (value === null) return <MissingValue spec={spec} />;

  return (
    <div
      data-parameter={spec.path}
      data-conflict={conflict?.severity}
      data-locked={locked !== null ? 'true' : undefined}
      aria-disabled={locked !== null ? true : undefined}
      className={cn(
        'rounded-bezel border px-3.5 py-2.5 transition-colors duration-200',
        fieldTone(conflict, changed),
        locked !== null && 'opacity-70'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-sans text-sm font-medium text-frost">{spec.label}</span>
            {changed ? <span className="h-1.5 w-1.5 rounded-full bg-mint" aria-label="alterado" /> : null}
          </div>
          <p className="mt-0.5 font-sans text-2xs leading-relaxed text-haze">{locked ?? spec.hint}</p>
        </div>

        {locked !== null ? (
          <span className="tnum w-[92px] shrink-0 text-right font-mono text-sm text-haze">
            {value.toFixed(spec.precision)}
            {spec.unit}
          </span>
        ) : (
          <ExactValueField
            value={value}
            min={spec.min}
            max={spec.max}
            precision={spec.precision}
            unit={spec.unit}
            label={spec.label}
            onCommit={edit}
            className="w-[92px] shrink-0"
          />
        )}
      </div>

      {/* The default tick makes a return to it one drag and not a guess. */}
      <HybridSlider
        value={value}
        min={spec.min}
        max={spec.max}
        step={spec.step}
        precision={spec.precision}
        label={spec.label}
        marker={defaultValue ?? undefined}
        onChange={edit}
        disabled={locked !== null}
        className="mt-3"
      />

      <div className="mt-1 flex items-center justify-between font-mono text-3xs text-haze-deep">
        <span className="tnum">
          {spec.min.toFixed(spec.precision)}
          {spec.unit}
        </span>
        {defaultValue !== null ? (
          <span className={cn('tnum', atDefault ? 'text-haze-deep' : 'text-haze')}>
            padrão {defaultValue.toFixed(spec.precision)}
            {spec.unit}
          </span>
        ) : null}
        <span className="tnum">
          {spec.max.toFixed(spec.precision)}
          {spec.unit}
        </span>
      </div>
      <ConflictNote conflict={conflict} />
    </div>
  );
};

/**
 * The camera's travel, drawn: the search angle it starts at and the nadir
 * angle where the approach freezes. Both come straight from the working
 * document, so the drawing moves with the sliders beneath it.
 */
const GimbalArc: React.FC<{ searchDeg: number; nadirDeg: number }> = ({ searchDeg, nadirDeg }) => {
  const cx = 16;
  const cy = 12;
  const r = 78;
  const point = (deg: number) => {
    const rad = (Math.abs(deg) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };
  const s = point(searchDeg);
  const n = point(nadirDeg);
  const h = point(0);
  const v = point(-90);

  return (
    <svg viewBox="0 0 110 100" className="h-[92px] w-[104px] shrink-0" aria-hidden>
      <path
        d={`M ${h.x} ${h.y} A ${r} ${r} 0 0 1 ${v.x} ${v.y}`}
        fill="none"
        stroke="rgba(232,242,240,0.12)"
        strokeDasharray="2 3"
      />
      <path
        d={`M ${s.x} ${s.y} A ${r} ${r} 0 0 1 ${n.x} ${n.y}`}
        fill="none"
        stroke="#01D5A3"
        strokeWidth={2.5}
        strokeLinecap="round"
      />
      <line x1={cx} y1={cy} x2={s.x} y2={s.y} stroke="rgba(124,153,164,0.8)" strokeWidth={1} />
      <line x1={cx} y1={cy} x2={n.x} y2={n.y} stroke="#5CF2CE" strokeWidth={1.4} />
      <circle cx={n.x} cy={n.y} r={3.2} fill="#5CF2CE" />
      <circle cx={s.x} cy={s.y} r={2.6} fill="#7C99A4" />
      <rect x={cx - 7} y={cy - 5} width={14} height={10} rx={2} fill="#0B3752" stroke="#01D5A3" strokeWidth={1} />
    </svg>
  );
};

const GroupCard: React.FC<{
  group: ParameterGroup;
  working: ParamsDoc;
  defaults: ParamsDoc | null;
  changedPaths: Set<string>;
  /** Conflicts to mark, empty until a save was attempted. */
  marked: ParameterConflict[];
  onEdit: (path: string, value: unknown) => void;
  index: number;
  refusedPath?: string | null;
}> = ({ group, working, defaults, changedPaths, marked, onEdit, index, refusedPath }) => {
  const Icon = GROUP_ICON[group.id];
  const changed = group.items.filter((item) => changedPaths.has(item.path)).length;
  const groupPaths = new Set(group.items.map((item) => item.path));
  const conflictCount = marked.filter((conflict) => conflict.paths.some((path) => groupPaths.has(path))).length;
  const groupErrors = marked.some(
    (conflict) => conflict.severity === 'error' && conflict.paths.some((path) => groupPaths.has(path))
  );
  const byPath = conflictsByPath(marked);
  if (refusedPath && groupPaths.has(refusedPath) && byPath[refusedPath]?.severity !== 'error') {
    byPath[refusedPath] = { severity: 'error', title: 'Recusado pela missão no lançamento' };
  }

  return (
    <section
      className={cn(
        // The sheet behind is already frosted glass over the film; a second
        // blur here would sample a flat field and cost GPU for nothing.
        'glass flex min-h-0 flex-col rounded-panel backdrop-filter-none transition-[border-color,box-shadow] duration-300 ease-instrument',
        changed > 0 && 'glass-live'
      )}
    >
      <header className="flex items-center gap-3 border-b border-strut-soft px-4 py-3">
        <span
          className={cn(
            'grid h-8 w-8 shrink-0 place-items-center rounded-bezel border transition-colors duration-300',
            changed > 0 ? 'border-mint/35 bg-mint/10 text-mint' : 'border-strut-soft bg-hull-raise/60 text-frost-dim'
          )}
        >
          <Icon size={15} strokeWidth={1.8} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="tnum font-mono text-3xs text-haze-deep">0{index + 1}</span>
            <h3 className="font-cond text-base font-semibold tracking-wide text-frost">{group.title}</h3>
          </div>
          <p className="line-clamp-2 font-sans text-2xs text-haze">{group.summary}</p>
        </div>
        {conflictCount > 0 ? (
          <span
            data-group-conflicts={group.id}
            title="Conflitos entre parâmetros"
            className={cn(
              'tnum rounded-full px-2 py-0.5 font-mono text-3xs',
              groupErrors ? 'bg-ember/15 text-ember' : 'bg-amber/15 text-amber'
            )}
          >
            {conflictCount}
          </span>
        ) : null}
        {changed > 0 ? (
          <span className="tnum rounded-full bg-mint/15 px-2 py-0.5 font-mono text-3xs text-mint">
            {changed}
          </span>
        ) : null}
      </header>

      <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3">
        {group.id === 'gimbal' ? (
          <div className="flex items-center gap-3 rounded-bezel border border-strut-soft bg-abyss/40 px-3 py-2">
            <GimbalArc
              searchDeg={readNumber(working, group.items[0]) ?? 0}
              nadirDeg={readNumber(working, group.items[1]) ?? -90}
            />
            <p className="text-3xs leading-relaxed text-haze">
              A câmera desce do ângulo de varredura até apontar totalmente para baixo durante o
              rastreamento. Nessa posição, a velocidade horizontal é zerada e a inspeção começa.
            </p>
          </div>
        ) : null}

        {group.items.map((spec) => (
          <ParameterControl
            key={spec.path}
            spec={spec}
            working={working}
            defaults={defaults}
            changed={changedPaths.has(spec.path)}
            conflict={byPath[spec.path]}
            onEdit={onEdit}
          />
        ))}
      </div>
    </section>
  );
};

/**
 * The parameters that define the mission, in three cards.
 *
 * Saving writes the whole document, so the fields not shown here — PID gains,
 * jerk ceilings, calibration statistics — keep the values the mission last
 * wrote. "Restaurar padrões" resets only the sheet fields, for the same
 * reason, to the mission's own defaults.
 */
export const ParameterSheet: React.FC<ParameterSheetProps> = ({
  working,
  defaults,
  changedPaths,
  dirty,
  saving,
  presets,
  activePresetId,
  saveAttempted = false,
  onEdit,
  onSave,
  onDiscard,
  onSavePreset,
  onApplyPreset,
  onRenamePreset,
  onDeletePreset,
  refusedPath = null,
  altitudeNotice = null,
}) => {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [conflictMode, setConflictMode] = useState<'blocked' | 'confirm' | null>(null);
  const [attempted, setAttempted] = useState(false);
  const conflicts = useMemo(() => activeConflicts(working), [working]);
  const missionMode = readMissionMode(working);
  const groups = useMemo(() => groupsForMode(missionMode, working), [missionMode, working]);
  // The advanced settings are the pad marker of the return: a flight that
  // lands in place reads none of them.
  const returns = readRtlEnabled(working);
  useEffect(() => {
    if (!returns) setAdvancedOpen(false);
  }, [returns]);
  const modeLabel = MISSION_MODES.find((mode) => mode.id === missionMode)?.label ?? missionMode;
  // Marks appear after the first attempt to save and follow the edits live;
  // a save that went through, or a discard, leaves nothing pending to mark.
  useEffect(() => {
    if (!dirty) setAttempted(false);
  }, [dirty]);
  const marked = attempted || saveAttempted ? conflicts : [];

  const requestSave = () => {
    if (conflicts.length === 0) {
      onSave();
      return;
    }
    setAttempted(true);
    setConflictMode(conflicts.some((conflict) => conflict.severity === 'error') ? 'blocked' : 'confirm');
  };

  const restoreDefaults = () => {
    if (!defaults) return;
    for (const group of PARAMETER_GROUPS) {
      for (const spec of group.items) {
        const value = spec.kind === 'boolean' ? readBoolean(defaults, spec) : readNumber(defaults, spec);
        if (value !== null) onEdit(spec.path, value);
      }
    }
  };

  return (
    <div className="glass flex h-full min-h-0 flex-col overflow-hidden rounded-panel bg-abyss/75">
      <div className="flex shrink-0 items-center justify-between border-b border-strut-soft px-5 py-3.5">
        <div>
          <h2 className="font-cond text-lg font-semibold tracking-wide text-frost">Parâmetros de voo</h2>
          <p className="text-2xs text-haze">Os ajustes que definem o comportamento da missão.</p>
          <p className="text-2xs text-haze">Modo: {modeLabel}</p>
        </div>
        <PlanChips doc={working} />
      </div>
      {altitudeNotice ? (
        <p role="status" data-altitude-notice className="shrink-0 border-b border-amber/40 bg-amber/[0.08] px-5 py-2 font-sans text-2xs text-amber">
          {altitudeNotice}
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-4 lg:grid-cols-3">
        {groups.map((group, index) => (
          <GroupCard
            key={group.id}
            group={group}
            index={index}
            working={working}
            defaults={defaults}
            changedPaths={changedPaths}
            marked={marked}
            onEdit={onEdit}
            refusedPath={refusedPath}
          />
        ))}
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t border-strut-soft px-4 py-3">
        <Button
          variant="quiet"
          onClick={restoreDefaults}
          disabled={!defaults}
          title={defaults ? undefined : 'Padrões da missão indisponíveis'}
          icon={<RotateCcw size={13} />}
        >
          Restaurar Padrões
        </Button>
        <FavoritesMenu
          presets={presets}
          activePresetId={activePresetId}
          onSave={onSavePreset}
          onApply={onApplyPreset}
          onRename={onRenamePreset}
          onDelete={onDeletePreset}
        />
        {returns ? (
          <Button variant="ghost" onClick={() => setAdvancedOpen(true)} icon={<Settings2 size={13} />}>
            Configurações Avançadas
          </Button>
        ) : null}

        <div className="ml-auto flex items-center gap-2">
          {dirty ? (
            <>
              <span className="text-2xs text-mint">
                {changedPaths.size} {changedPaths.size === 1 ? 'alteração' : 'alterações'} sem salvar
              </span>
              <Button variant="quiet" onClick={onDiscard} icon={<Undo2 size={13} />}>
                Descartar
              </Button>
            </>
          ) : (
            <span className="text-2xs text-haze-deep">Tudo salvo</span>
          )}
          <Button
            variant={dirty ? 'primary' : 'quiet'}
            onClick={requestSave}
            disabled={!dirty || saving}
            icon={<Save size={13} />}
          >
            {saving ? 'Salvando' : 'Salvar'}
          </Button>
        </div>
      </div>

      {advancedOpen && returns ? (
        <AdvancedSettingsDialog
          working={working}
          defaults={defaults}
          changedPaths={changedPaths}
          conflicts={conflictsByPath(marked)}
          onEdit={onEdit}
          onClose={() => setAdvancedOpen(false)}
        />
      ) : null}

      {conflictMode !== null ? (
        <ParameterConflictDialog
          mode={conflictMode}
          conflicts={conflicts}
          working={working}
          onBack={() => setConflictMode(null)}
          onConfirm={() => {
            setConflictMode(null);
            onSave();
          }}
        />
      ) : null}
    </div>
  );
};
