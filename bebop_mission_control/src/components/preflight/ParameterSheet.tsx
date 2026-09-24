import React, { useEffect, useState } from 'react';
import { Camera, Plane, RotateCcw, Save, Star, Timer, Undo2 } from 'lucide-react';
import {
  PARAMETER_GROUPS,
  type ParameterGroup,
  type ParameterGroupId,
  type ParameterSpec,
} from '../../lib/parameterSchema';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { Button } from '../ui/Button';
import { cn } from '../../lib/format';

interface ParameterSheetProps {
  working: ParamsDoc;
  changedPaths: Set<string>;
  dirty: boolean;
  saving: boolean;
  hasPreset: boolean;
  onEdit: (path: string, value: unknown) => void;
  onSave: () => void;
  onDiscard: () => void;
  onSavePreset: () => void;
  onApplyPreset: () => void;
}

const GROUP_ICON: Record<ParameterGroupId, React.ElementType> = {
  envelope: Plane,
  gimbal: Camera,
  timeouts: Timer,
};

const readNumber = (doc: ParamsDoc, spec: ParameterSpec): number => {
  const raw = getPath(doc, spec.path);
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : spec.defaultValue;
};

/**
 * One parameter: its name, the exact figure, a slider across the full range
 * and the range itself printed at both ends, so the operator sees where in the
 * envelope the value sits without having to know the bounds.
 */
const ParameterControl: React.FC<{
  spec: ParameterSpec;
  value: number;
  changed: boolean;
  onEdit: (path: string, value: unknown) => void;
}> = ({ spec, value, changed, onEdit }) => {
  const [draft, setDraft] = useState(() => value.toFixed(spec.precision));

  useEffect(() => {
    setDraft(value.toFixed(spec.precision));
  }, [value, spec.precision]);

  const commit = (raw: string) => {
    const parsed = Number.parseFloat(raw.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      setDraft(value.toFixed(spec.precision));
      return;
    }
    const clamped = Math.min(spec.max, Math.max(spec.min, parsed));
    const rounded = Number(clamped.toFixed(spec.precision));
    onEdit(spec.path, rounded);
    setDraft(rounded.toFixed(spec.precision));
  };

  const ratio = Math.max(0, Math.min(1, (value - spec.min) / (spec.max - spec.min || 1)));
  const atDefault = Math.abs(value - spec.defaultValue) < 10 ** -(spec.precision + 1);

  return (
    <div
      className={cn(
        'rounded-bezel border px-3.5 py-2.5 transition-colors duration-200',
        changed ? 'border-mint/45 bg-mint/[0.06]' : 'border-strut-soft bg-abyss/40'
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm text-frost">{spec.label}</span>
            {changed ? <span className="h-1.5 w-1.5 rounded-full bg-mint" aria-label="alterado" /> : null}
          </div>
          <p className="mt-0.5 text-2xs leading-relaxed text-haze-deep">{spec.hint}</p>
        </div>

        <label className="flex w-[92px] shrink-0 items-baseline justify-end gap-1 rounded-bezel border border-strut bg-abyss px-2 py-1 transition-colors focus-within:border-mint/70">
          <input
            value={draft}
            inputMode="decimal"
            aria-label={`${spec.label}, valor exato`}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => commit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            }}
            className="tnum w-full bg-transparent text-right font-mono text-base text-frost outline-none"
          />
          {spec.unit ? (
            <span className="shrink-0 font-mono text-3xs text-haze-deep">{spec.unit}</span>
          ) : null}
        </label>
      </div>

      <div className="group relative mt-3 h-5">
        <input
          type="range"
          aria-label={spec.label}
          min={spec.min}
          max={spec.max}
          step={spec.step}
          value={value}
          onChange={(e) => onEdit(spec.path, Number(Number(e.target.value).toFixed(spec.precision)))}
          className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
        <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-strut" />
        <div
          className="pointer-events-none absolute left-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-gradient-to-r from-kelp to-mint"
          style={{ width: `${ratio * 100}%` }}
        />
        {/* Where the default sits, so a return to it is one drag and not a guess. */}
        <div
          aria-hidden
          className="pointer-events-none absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-frost/40"
          style={{
            left: `${Math.max(0, Math.min(1, (spec.defaultValue - spec.min) / (spec.max - spec.min || 1))) * 100}%`,
          }}
        />
        <div
          className={cn(
            'pointer-events-none absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full',
            'border-2 border-mint bg-hull-deep transition-shadow duration-150',
            'peer-hover:shadow-live peer-focus-visible:shadow-live'
          )}
          style={{ left: `${ratio * 100}%` }}
        />
      </div>

      <div className="mt-1 flex items-center justify-between font-mono text-3xs text-haze-deep">
        <span className="tnum">
          {spec.min.toFixed(spec.precision)}
          {spec.unit}
        </span>
        <span className={cn('tnum', atDefault ? 'text-haze-deep' : 'text-haze')}>
          padrão {spec.defaultValue.toFixed(spec.precision)}
          {spec.unit}
        </span>
        <span className="tnum">
          {spec.max.toFixed(spec.precision)}
          {spec.unit}
        </span>
      </div>
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
  changedPaths: Set<string>;
  onEdit: (path: string, value: unknown) => void;
  index: number;
}> = ({ group, working, changedPaths, onEdit, index }) => {
  const Icon = GROUP_ICON[group.id];
  const changed = group.items.filter((item) => changedPaths.has(item.path)).length;

  return (
    <section className="flex min-h-0 flex-col rounded-panel border border-strut-soft bg-hull/80">
      <header className="flex items-center gap-3 border-b border-strut-soft px-4 py-3">
        <span className="grid h-8 w-8 place-items-center rounded-bezel border border-mint/35 bg-mint/10 text-mint">
          <Icon size={15} strokeWidth={1.8} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-3xs text-haze-deep">0{index + 1}</span>
            <h3 className="font-cond text-sm font-semibold tracking-wide text-frost">{group.title}</h3>
          </div>
          <p className="truncate text-3xs text-haze">{group.summary}</p>
        </div>
        {changed > 0 ? (
          <span className="rounded-full bg-mint/15 px-2 py-0.5 font-mono text-3xs text-mint">
            {changed}
          </span>
        ) : null}
      </header>

      <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3">
        {group.id === 'gimbal' ? (
          <div className="flex items-center gap-3 rounded-bezel border border-strut-soft bg-abyss/40 px-3 py-2">
            <GimbalArc
              searchDeg={readNumber(working, group.items[0])}
              nadirDeg={readNumber(working, group.items[1])}
            />
            <p className="text-3xs leading-relaxed text-haze">
              A câmera desce do ângulo de varredura até o ângulo nadir durante o rastreamento. Ao
              atingi-lo, a velocidade horizontal é zerada e a inspeção começa.
            </p>
          </div>
        ) : null}

        {group.items.map((spec) => (
          <ParameterControl
            key={spec.path}
            spec={spec}
            value={readNumber(working, spec)}
            changed={changedPaths.has(spec.path)}
            onEdit={onEdit}
          />
        ))}
      </div>
    </section>
  );
};

/**
 * The six parameters that define the mission, in three cards.
 *
 * Saving writes the whole document, so the fields not shown here — PID gains,
 * jerk ceilings, calibration statistics — keep the values the mission last
 * wrote. "Restaurar padrões" resets only these six, for the same reason.
 */
export const ParameterSheet: React.FC<ParameterSheetProps> = ({
  working,
  changedPaths,
  dirty,
  saving,
  hasPreset,
  onEdit,
  onSave,
  onDiscard,
  onSavePreset,
  onApplyPreset,
}) => {
  const restoreDefaults = () => {
    for (const group of PARAMETER_GROUPS) {
      for (const spec of group.items) onEdit(spec.path, spec.defaultValue);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-panel border border-strut-soft bg-abyss/85 backdrop-blur-md">
      <div className="flex shrink-0 items-center justify-between border-b border-strut-soft px-5 py-3.5">
        <div>
          <h2 className="font-cond text-lg font-semibold tracking-wide text-frost">Parâmetros de voo</h2>
          <p className="text-2xs text-haze">Seis ajustes que definem o comportamento da missão.</p>
        </div>
        <span className="rounded-full border border-strut px-2.5 py-1 font-mono text-3xs text-haze-deep">
          mission_config.json
        </span>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-4 lg:grid-cols-3">
        {PARAMETER_GROUPS.map((group, index) => (
          <GroupCard
            key={group.id}
            group={group}
            index={index}
            working={working}
            changedPaths={changedPaths}
            onEdit={onEdit}
          />
        ))}
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t border-strut-soft px-4 py-3">
        <Button variant="quiet" onClick={restoreDefaults} icon={<RotateCcw size={13} />}>
          Restaurar Padrões
        </Button>
        <Button
          variant="ghost"
          onClick={onApplyPreset}
          disabled={!hasPreset}
          title={hasPreset ? undefined : 'Nenhum ajuste salvo ainda'}
        >
          Meu ajuste
        </Button>
        <Button variant="ghost" onClick={onSavePreset} icon={<Star size={13} />}>
          Salvar como meu ajuste
        </Button>

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
            onClick={onSave}
            disabled={!dirty || saving}
            icon={<Save size={13} />}
          >
            {saving ? 'Salvando' : 'Salvar'}
          </Button>
        </div>
      </div>
    </div>
  );
};
