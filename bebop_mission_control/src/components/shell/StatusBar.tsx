import React, { useEffect, useRef, useState } from 'react';
import {
  BatteryWarning,
  ChevronRight,
  Loader2,
  Lock,
  Plane,
  RefreshCw,
  Volume1,
  Volume2,
  VolumeX,
} from 'lucide-react';
import type { TelemetryView } from '../../types/mission';
import type {
  BatteryFailsafe,
  LinkReadiness,
  VoiceLevel,
  WifiNetwork,
} from '../../types/bmg';
import type { LinkPhase } from '../../hooks/useLink';
import { cn, rfBars } from '../../lib/format';
import { FAILSAFE_MAX_PCT, FAILSAFE_MIN_PCT } from '../../lib/batteryFailsafe';

interface StatusBarProps {
  telemetry: TelemetryView;
  /** Opens the full link sheet, from the foot of the Wi-Fi flyout. */
  onOpenLink: () => void;

  networks: WifiNetwork[];
  currentSsid: string;
  /** A forced rescan is running. */
  scanning: boolean;
  linkPhase: LinkPhase;
  linkMessage: string | null;
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  onScan: () => void;
  onConnect: (ssid: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;

  failsafe: BatteryFailsafe;
  onFailsafeChange: (next: BatteryFailsafe) => void;
  /** The failsafe has already commanded a landing on this flight. */
  failsafeTriggered: boolean;

  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
  className?: string;
}

/**
 * Charge, radio and the copilot's voice, in the top right corner.
 *
 * Three independent items, as a notebook's system tray has them: each is its
 * own pill, and each opens its own flyout — the battery its failsafe, the radio
 * its network list and driver, the voice its level. Fusing the first two into
 * one button meant a press on the battery opened a Wi-Fi sheet.
 *
 * A figure that has never been measured reads as a dash. The bridge publishes
 * zero for "never read", and a battery gauge showing 0 % is a specific,
 * alarming claim about an aircraft nobody has asked yet.
 */

/** Outside press and Escape close a flyout, as every menu bar item does. */
function useFlyout() {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!hostRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return { open, setOpen, hostRef };
}

const PILL =
  'flex items-center gap-2 rounded-full border border-frost/12 bg-abyss/45 px-3 py-1.5 backdrop-blur-md transition-colors hover:border-frost/30';
const FLYOUT =
  'anim-rise absolute right-0 top-[calc(100%+8px)] z-50 rounded-panel border border-frost/12 bg-abyss/95 shadow-lg backdrop-blur-xl';

/** The battery glyph: a cell, a cap, and a fill proportional to the charge. */
const BatteryGlyph: React.FC<{ pct: number; known: boolean; critical: boolean }> = ({
  pct,
  known,
  critical,
}) => (
  <span className="inline-flex items-center gap-[2px]" aria-hidden>
    <span className="relative block h-[13px] w-[25px] rounded-[3px] border border-frost/45 p-[2px]">
      <span
        className={cn(
          'block h-full rounded-[1px] transition-all duration-500',
          !known ? 'bg-haze-deep' : critical ? 'bg-ember' : pct <= 40 ? 'bg-amber' : 'bg-frost'
        )}
        style={{
          width: known ? `${Math.max(4, Math.min(100, pct))}%` : '100%',
          opacity: known ? 1 : 0.2,
        }}
      />
    </span>
    <span className="block h-[5px] w-[2px] rounded-r-[1px] bg-frost/45" />
  </span>
);

/**
 * The Wi-Fi glyph as a notebook draws it: a dot and three arcs, lit up to the
 * measured strength. `level` is 0–4; zero draws the outline only.
 */
const WifiGlyph: React.FC<{ level: number; tone?: 'frost' | 'amber' | 'mint'; size?: number }> = ({
  level,
  tone = 'frost',
  size = 16,
}) => {
  const on = tone === 'amber' ? '#FFC24B' : tone === 'mint' ? '#01D5A3' : '#E8F2F0';
  const off = 'rgba(232,242,240,0.2)';
  const arcs = [
    'M 4.6 9.6 A 4.8 4.8 0 0 1 11.4 9.6',
    'M 2.3 7.2 A 8.2 8.2 0 0 1 13.7 7.2',
    'M 0.2 4.8 A 11.4 11.4 0 0 1 15.8 4.8',
  ];
  return (
    <svg width={size} height={size * 0.8} viewBox="0 0 16 13" aria-hidden className="shrink-0">
      {arcs.map((d, i) => (
        <path
          key={d}
          d={d}
          fill="none"
          stroke={level >= i + 2 ? on : off}
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      ))}
      <circle cx="8" cy="11.6" r="1.3" fill={level >= 1 ? on : off} />
    </svg>
  );
};

/** nmcli signal percentage to the four-level scale used across the station. */
const levelFromPercent = (pct: number) =>
  pct >= 75 ? 4 : pct >= 55 ? 3 : pct >= 35 ? 2 : pct > 0 ? 1 : 0;

/**
 * The copilot's voice.
 *
 * The icon opens the panel; the panel holds the slider and the mute. Clicking
 * the icon used to mute outright, with the slider appearing only on hover —
 * which meant the volume was unreachable by touch, unreachable by keyboard, and
 * reachable by mouse only if you knew to hover something that looked like a
 * button. One press now opens the thing being reached for, and muting is an
 * explicit control inside it rather than a side effect of going looking.
 *
 * Exported because the cockpit needs it too: a demonstration is exactly when an
 * operator wants the voice down, and the pre-flight screen is not on screen then.
 */
export const VoiceControl: React.FC<{
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
  /** Which side the panel opens toward, so it never leaves the window. */
  align?: 'left' | 'right';
  /** Drawn as a standalone pill, as in the pre-flight tray. */
  pill?: boolean;
}> = ({ voice, onVolume, onToggleMute, align = 'right', pill = false }) => {
  const [open, setOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement | null>(null);

  // Anywhere outside closes it, which is what every menu bar item does and what
  // a pointer that has wandered off expects.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!hostRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const percent = Math.round(voice.volume * 100);
  const Icon = voice.muted ? VolumeX : voice.volume < 0.5 ? Volume1 : Volume2;

  return (
    <div ref={hostRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-label="Voz do copiloto"
        aria-expanded={open}
        title={voice.muted ? 'Copiloto silenciado' : `Copiloto a ${percent}%`}
        className={cn(
          pill ? PILL : 'flex items-center gap-1.5 rounded-full px-1.5 py-1 transition-colors',
          voice.muted ? 'text-haze-deep hover:text-haze' : 'text-frost hover:text-mint',
          open && 'text-mint',
          pill && open && 'border-mint/45'
        )}
      >
        <Icon size={14} strokeWidth={1.8} />
        <span className="tnum font-mono text-2xs">{voice.muted ? '—' : `${percent}%`}</span>
      </button>

      {open ? (
        <div
          className={cn(
            'anim-rise absolute top-[calc(100%+8px)] z-50 w-[188px] rounded-panel border',
            'border-frost/12 bg-abyss/95 p-3 shadow-lg backdrop-blur-md',
            align === 'right' ? 'right-0' : 'left-0'
          )}
        >
          <div className="flex items-baseline justify-between">
            <span className="font-cond text-3xs tracking-wide text-haze-deep">VOZ DO COPILOTO</span>
            <button
              type="button"
              onClick={onToggleMute}
              aria-pressed={voice.muted}
              className={cn(
                'rounded-full px-1.5 py-0.5 font-cond text-3xs tracking-wide transition-colors',
                voice.muted
                  ? 'bg-amber/15 text-amber hover:bg-amber/25'
                  : 'text-haze hover:bg-frost/[0.08] hover:text-frost'
              )}
            >
              {voice.muted ? 'MUDO' : 'silenciar'}
            </button>
          </div>

          <div className="group relative mt-2.5 h-4">
            <input
              type="range"
              aria-label="Volume do copiloto"
              min={0}
              max={100}
              step={1}
              value={percent}
              onChange={(e) => {
                const next = Number(e.target.value) / 100;
                onVolume(next);
                // Moving the handle off zero is an unambiguous request to hear
                // it; leaving it muted afterwards would make the slider a lie.
                if (next > 0 && voice.muted) onToggleMute();
              }}
              className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
            <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-frost/15" />
            <div
              className={cn(
                'pointer-events-none absolute left-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full transition-colors',
                voice.muted ? 'bg-haze-deep' : 'bg-mint'
              )}
              style={{ width: `${percent}%` }}
            />
            <div
              className={cn(
                'pointer-events-none absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full',
                'border-2 bg-hull-deep transition-shadow duration-150',
                voice.muted ? 'border-haze-deep' : 'border-mint',
                'peer-hover:shadow-live peer-focus-visible:shadow-live'
              )}
              style={{ left: `${percent}%` }}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
};

/** Battery, with its failsafe. */
const BatteryWidget: React.FC<{
  telemetry: TelemetryView;
  failsafe: BatteryFailsafe;
  onFailsafeChange: (next: BatteryFailsafe) => void;
  triggered: boolean;
}> = ({ telemetry, failsafe, onFailsafeChange, triggered }) => {
  const { open, setOpen, hostRef } = useFlyout();

  const connected = Boolean(telemetry.connected);
  const known = connected && Boolean(telemetry.battery_known);
  const charge = known ? telemetry.battery_pct : 0;
  const critical = known && (charge < 25 || (failsafe.enabled && charge <= failsafe.thresholdPct));
  const low = known && charge <= 40;
  const thresholdRatio =
    (failsafe.thresholdPct - FAILSAFE_MIN_PCT) / (FAILSAFE_MAX_PCT - FAILSAFE_MIN_PCT);
  const level = !known ? 'Sem leitura' : critical ? 'Crítica' : low ? 'Baixa' : 'Normal';
  const source =
    telemetry.battery_source === 'aircraft'
      ? 'ARSDK (aeronave)'
      : telemetry.battery_source === 'console'
      ? 'console do driver'
      : '—';
  const age =
    typeof telemetry.battery_age_sec === 'number' ? `há ${telemetry.battery_age_sec.toFixed(0)} s` : '—';
  const flying = telemetry.flying_state_label && telemetry.flying_state_label !== 'unknown'
    ? telemetry.flying_state_label
    : connected
    ? 'em solo'
    : '—';

  return (
    <div ref={hostRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-label="Bateria da aeronave"
        title={known ? `Bateria ${charge}%` : 'Bateria sem leitura'}
        className={cn(PILL, open && 'border-mint/45', critical && 'border-ember/50')}
      >
        <BatteryGlyph pct={charge} known={known} critical={critical} />
        <span
          className={cn(
            'tnum font-mono text-2xs',
            !known ? 'text-haze-deep' : critical ? 'text-ember' : 'text-frost'
          )}
        >
          {known ? `${charge}%` : '—'}
        </span>
        {failsafe.enabled ? (
          <span
            aria-label="pouso automático armado"
            title={`Pouso automático em ${failsafe.thresholdPct}%`}
            className={cn('h-1.5 w-1.5 rounded-full', triggered ? 'bg-ember anim-breathe' : 'bg-mint')}
          />
        ) : null}
      </button>

      {open ? (
        <div className={cn(FLYOUT, 'w-[312px] p-4')} role="dialog" aria-label="Bateria">
          <div className="flex items-center justify-between">
            <span className="font-cond text-3xs tracking-[0.16em] text-haze-deep">BATERIA DA AERONAVE</span>
            <span
              className={cn(
                'rounded-full px-2 py-0.5 font-cond text-3xs tracking-wide',
                !known
                  ? 'bg-frost/[0.06] text-haze'
                  : critical
                  ? 'bg-ember/15 text-ember'
                  : low
                  ? 'bg-amber/15 text-amber'
                  : 'bg-mint/15 text-mint'
              )}
            >
              {level}
            </span>
          </div>

          <div className="mt-3 flex items-end gap-3">
            <span
              className={cn(
                'tnum font-mono text-3xl leading-none',
                !known ? 'text-haze-deep' : critical ? 'text-ember' : 'text-frost'
              )}
            >
              {known ? charge : '—'}
              <span className="text-lg text-haze">%</span>
            </span>
            <div className="mb-1 h-2.5 flex-1 overflow-hidden rounded-full bg-frost/10">
              <div
                className={cn(
                  'h-full rounded-full transition-all duration-500',
                  critical ? 'bg-ember' : low ? 'bg-amber' : 'bg-mint'
                )}
                style={{ width: `${known ? Math.max(2, Math.min(100, charge)) : 0}%` }}
              />
            </div>
          </div>

          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-2xs">
            <dt className="text-haze-deep">Estado</dt>
            <dd className="text-right text-frost">
              {known ? `${flying} · sem carregamento em voo` : 'aeronave sem leitura de carga'}
            </dd>
            <dt className="text-haze-deep">Origem</dt>
            <dd className="text-right font-mono text-frost">{source}</dd>
            <dt className="text-haze-deep">Leitura</dt>
            <dd className="text-right font-mono text-frost">{age}</dd>
          </dl>

          <div className="mt-4 border-t border-strut-soft pt-3">
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-sm text-frost">
                <BatteryWarning size={14} strokeWidth={1.8} className="text-amber" />
                Pouso automático por bateria baixa
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={failsafe.enabled}
                aria-label="Pouso automático por bateria baixa"
                onClick={() => onFailsafeChange({ ...failsafe, enabled: !failsafe.enabled })}
                className={cn(
                  'relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-200 ease-instrument',
                  failsafe.enabled ? 'border-mint bg-mint/25' : 'border-strut bg-abyss'
                )}
              >
                <span
                  className={cn(
                    'absolute top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full transition-all duration-200 ease-instrument',
                    failsafe.enabled ? 'left-[24px] bg-mint' : 'left-[4px] bg-haze-deep'
                  )}
                />
              </button>
            </div>

            <div className={cn('mt-3 transition-opacity', failsafe.enabled ? 'opacity-100' : 'opacity-45')}>
              <div className="flex items-baseline justify-between">
                <span className="text-2xs text-haze">Nível crítico para retorno e pouso (%)</span>
                <span className="tnum font-mono text-sm text-frost">{failsafe.thresholdPct}%</span>
              </div>
              <div className="group relative mt-1.5 h-4">
                <input
                  type="range"
                  aria-label="Nível crítico para retorno e pouso"
                  min={FAILSAFE_MIN_PCT}
                  max={FAILSAFE_MAX_PCT}
                  step={1}
                  value={failsafe.thresholdPct}
                  disabled={!failsafe.enabled}
                  onChange={(e) => onFailsafeChange({ ...failsafe, thresholdPct: Number(e.target.value) })}
                  className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
                />
                <div className="pointer-events-none absolute left-0 right-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-frost/15" />
                <div
                  className="pointer-events-none absolute left-0 top-1/2 h-[2px] -translate-y-1/2 rounded-full bg-amber"
                  style={{ width: `${thresholdRatio * 100}%` }}
                />
                <div
                  className="pointer-events-none absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-amber bg-hull-deep"
                  style={{ left: `${thresholdRatio * 100}%` }}
                />
              </div>
              <div className="mt-0.5 flex justify-between font-mono text-3xs text-haze-deep">
                <span>{FAILSAFE_MIN_PCT}%</span>
                <span>{FAILSAFE_MAX_PCT}%</span>
              </div>
            </div>

            <p className="mt-2.5 text-2xs leading-relaxed text-haze">
              Quando ativado, caso a bateria atinja ou caia abaixo deste nível durante o voo, a
              aeronave interrompe a etapa atual e retorna à base para o pouso de precisão no
              marcador. Na decolagem, ou sem missão em execução, pousa no local.
            </p>
            {triggered ? (
              <p className="mt-2 rounded-bezel border border-ember/40 bg-ember/10 px-2.5 py-1.5 text-2xs text-ember">
                Pouso automático disparado neste voo.
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
};

/** Wi-Fi, with a flyout modelled on a notebook's quick settings. */
const WifiWidget: React.FC<{
  telemetry: TelemetryView;
  networks: WifiNetwork[];
  currentSsid: string;
  scanning: boolean;
  phase: LinkPhase;
  message: string | null;
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  onScan: () => void;
  onConnect: (ssid: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;
  onOpenLink: () => void;
}> = ({
  telemetry,
  networks,
  currentSsid,
  scanning,
  phase,
  message,
  readiness,
  flightReady,
  driverRunning,
  onScan,
  onConnect,
  onStartDriver,
  onStopDriver,
  onOpenLink,
}) => {
  const { open, setOpen, hostRef } = useFlyout();
  const [joining, setJoining] = useState<string | null>(null);

  /**
   * `connected` is the bridge's verdict on the aircraft being reachable. The
   * pill names the network the station is on either way, but only an
   * answering aircraft lights it: a laptop on the office Wi-Fi is on a
   * network, not on a link.
   */
  const connected = Boolean(telemetry.connected);
  const hostSsid = currentSsid || telemetry.wifi_ssid;
  // The aircraft's RSSI only while its driver is publishing; otherwise the
  // host's own reading of the network it is on, which is live either way.
  const bars = telemetry.data_fresh
    ? rfBars(telemetry.wifi_signal_dbm)
    : levelFromPercent(networks.find((n) => n.active)?.signal ?? 0);
  const label = connected ? telemetry.wifi_ssid || hostSsid || 'Conectado' : hostSsid || 'Desconectado';

  const busy = phase === 'connecting' || phase === 'starting-driver' || phase === 'validating';
  const flowing = readiness ? readiness.topics.filter((t) => t.receiving).length : 0;
  const expected = readiness ? readiness.topics.length : 0;

  useEffect(() => {
    if (!busy) setJoining(null);
  }, [busy]);

  // Opening the flyout is a request to see what is around now.
  useEffect(() => {
    if (open) onScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <div ref={hostRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        aria-label="Redes Wi-Fi"
        title={
          telemetry.data_fresh
            ? `${label} · ${telemetry.wifi_signal_dbm} dBm`
            : connected
            ? `${label} · sem dados do driver`
            : `Aeronave fora de alcance${hostSsid ? ` (a estação está em ${hostSsid})` : ''}`
        }
        className={cn(PILL, open && 'border-mint/45')}
      >
        <WifiGlyph level={hostSsid || connected ? Math.max(bars, 1) : 0} tone={connected ? 'frost' : 'amber'} />
        <span
          className={cn('max-w-[14ch] truncate font-mono text-2xs', connected ? 'text-frost' : 'text-amber/80')}
        >
          {label}
        </span>
      </button>

      {open ? (
        <div className={cn(FLYOUT, 'flex w-[344px] flex-col')} role="dialog" aria-label="Redes Wi-Fi">
          <div className="flex items-center justify-between border-b border-strut-soft px-4 py-3">
            <div>
              <div className="text-sm font-medium text-frost">Redes Wi-Fi</div>
              <div className="text-3xs text-haze-deep">
                {scanning ? 'Procurando redes…' : `${networks.length} ${networks.length === 1 ? 'rede disponível' : 'redes disponíveis'}`}
              </div>
            </div>
            <button
              type="button"
              onClick={onScan}
              disabled={scanning}
              aria-label="Atualizar redes"
              title="Atualizar redes"
              className="rounded-full p-2 text-haze transition-colors hover:bg-frost/[0.08] hover:text-frost disabled:opacity-40"
            >
              <RefreshCw size={14} className={cn(scanning && 'animate-spin')} />
            </button>
          </div>

          <ul className="scroll-thin max-h-[280px] overflow-y-auto p-1.5">
            {networks.length === 0 ? (
              <li className="px-3 py-6 text-center text-2xs leading-relaxed text-haze-deep">
                Nenhuma rede encontrada. Ligue a aeronave e espere a rede
                <span className="font-mono text-haze"> Bebop2-…</span> aparecer.
              </li>
            ) : (
              networks.map((network) => {
                const active = network.active || network.ssid === currentSsid;
                const pending = joining === network.ssid && busy;
                return (
                  <li key={network.ssid}>
                    <button
                      type="button"
                      disabled={active || busy}
                      onClick={() => {
                        setJoining(network.ssid);
                        onConnect(network.ssid);
                      }}
                      className={cn(
                        'flex w-full items-center gap-3 rounded-bezel px-3 py-2.5 text-left transition-colors',
                        active ? 'cursor-default bg-mint/[0.09]' : 'hover:bg-frost/[0.06]',
                        busy && !active && 'cursor-wait'
                      )}
                    >
                      <WifiGlyph
                        level={levelFromPercent(network.signal)}
                        tone={active ? 'mint' : 'frost'}
                        size={18}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate text-sm text-frost">{network.ssid}</span>
                          {network.isBebop ? (
                            <span className="flex shrink-0 items-center gap-1 rounded-full border border-mint/45 bg-mint/10 px-1.5 py-px font-cond text-[9px] font-semibold tracking-[0.12em] text-mint">
                              <Plane size={8} strokeWidth={2.5} />
                              DRONE
                            </span>
                          ) : null}
                        </span>
                        <span className="block text-3xs text-haze-deep">
                          {pending
                            ? 'Conectando…'
                            : active
                            ? network.isBebop
                              ? connected
                                ? 'Conectado · aeronave respondendo'
                                : 'Conectado · aguardando a aeronave'
                              : 'Conectado'
                            : network.secure
                            ? 'Protegida'
                            : 'Aberta'}
                        </span>
                      </span>
                      {pending ? (
                        <Loader2 size={13} className="shrink-0 animate-spin text-mint" />
                      ) : network.secure && !network.isBebop ? (
                        <Lock size={11} className="shrink-0 text-haze-deep" />
                      ) : (
                        <span className="tnum shrink-0 font-mono text-3xs text-haze-deep">{network.signal}%</span>
                      )}
                    </button>
                  </li>
                );
              })
            )}
          </ul>

          <div className="border-t border-strut-soft px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      'h-1.5 w-1.5 rounded-full',
                      flightReady ? 'bg-mint' : driverRunning ? 'bg-amber' : 'bg-haze-deep',
                      (phase === 'starting-driver' || phase === 'validating') && 'anim-breathe'
                    )}
                  />
                  <span className="text-sm text-frost">Driver ROS 2</span>
                  <span className={cn('font-cond text-3xs tracking-wide', driverRunning ? 'text-mint' : 'text-haze-deep')}>
                    {driverRunning ? 'ATIVO' : 'PARADO'}
                  </span>
                </div>
                <p className="mt-0.5 font-mono text-3xs text-haze-deep">
                  {readiness ? `${flowing}/${expected} tópicos em tráfego` : 'aguardando o enlace'}
                </p>
              </div>
              {driverRunning ? (
                <button
                  type="button"
                  onClick={onStopDriver}
                  className="rounded-full border border-strut px-3 py-1 text-2xs text-frost transition-colors hover:border-ember/60 hover:text-ember"
                >
                  Parar
                </button>
              ) : (
                <button
                  type="button"
                  onClick={onStartDriver}
                  disabled={!connected || busy}
                  className="flex items-center gap-1.5 rounded-full border border-mint/50 bg-mint/10 px-3 py-1 text-2xs text-mint transition-colors hover:bg-mint/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy ? <Loader2 size={11} className="animate-spin" /> : null}
                  Iniciar
                </button>
              )}
            </div>
            {message ? (
              <p
                className={cn('mt-2 text-3xs leading-relaxed', phase === 'error' ? 'text-ember' : 'text-haze')}
                role="status"
              >
                {message}
              </p>
            ) : null}
          </div>

          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onOpenLink();
            }}
            className="flex items-center justify-between border-t border-strut-soft px-4 py-2.5 text-2xs text-haze transition-colors hover:bg-frost/[0.04] hover:text-frost"
          >
            Detalhes do enlace
            <ChevronRight size={13} />
          </button>
        </div>
      ) : null}
    </div>
  );
};

export const StatusBar: React.FC<StatusBarProps> = (props) => (
  <div className={cn('flex items-center gap-2', props.className)}>
    <BatteryWidget
      telemetry={props.telemetry}
      failsafe={props.failsafe}
      onFailsafeChange={props.onFailsafeChange}
      triggered={props.failsafeTriggered}
    />
    <WifiWidget
      telemetry={props.telemetry}
      networks={props.networks}
      currentSsid={props.currentSsid}
      scanning={props.scanning}
      phase={props.linkPhase}
      message={props.linkMessage}
      readiness={props.readiness}
      flightReady={props.flightReady}
      driverRunning={props.driverRunning}
      onScan={props.onScan}
      onConnect={props.onConnect}
      onStartDriver={props.onStartDriver}
      onStopDriver={props.onStopDriver}
      onOpenLink={props.onOpenLink}
    />
    <VoiceControl
      voice={props.voice}
      onVolume={props.onVolume}
      onToggleMute={props.onToggleMute}
      pill
    />
  </div>
);
