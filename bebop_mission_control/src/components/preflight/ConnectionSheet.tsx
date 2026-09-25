import React, { useEffect } from 'react';
import { Loader2, RefreshCw, Wifi, X } from 'lucide-react';
import type { LinkProgressEvent, LinkReadiness, WifiNetwork } from '../../types/bmg';
import type { LinkPhase } from '../../hooks/useLink';
import type { TelemetryView } from '../../types/mission';
import { Button } from '../ui/Button';
import { BatteryCell, Dot, RfBars } from '../ui/Indicator';
import { cn, rfBars } from '../../lib/format';
import { bebopNetworks } from '../../lib/wifi';

interface ConnectionSheetProps {
  open: boolean;
  onClose: () => void;
  telemetry: TelemetryView;
  networks: WifiNetwork[];
  currentSsid: string;
  phase: LinkPhase;
  message: string | null;
  progress: LinkProgressEvent[];
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  onScan: () => void;
  onConnect: (ssid: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;
}

/** nmcli signal percentage to the four-bar scale used across the station. */
const barsFromPercent = (pct: number) =>
  pct >= 75 ? 4 : pct >= 55 ? 3 : pct >= 35 ? 2 : pct > 0 ? 1 : 0;

/**
 * Everything about the link, on demand.
 *
 * The pre-flight screen keeps one command at its centre, so the machinery that
 * decides whether that command is available lives one gesture away rather than
 * competing with it. Opened from the connection chip, closed with Escape.
 */
export const ConnectionSheet: React.FC<ConnectionSheetProps> = ({
  open,
  onClose,
  telemetry,
  networks,
  currentSsid,
  phase,
  message,
  progress,
  readiness,
  flightReady,
  driverRunning,
  onScan,
  onConnect,
  onStartDriver,
  onStopDriver,
}) => {
  // The scan covers every network in range, because the status bar's Wi-Fi
  // menu is a general picker; this sheet connects to the aircraft, so it
  // offers the aircraft's networks and nothing else.
  const drones = bebopNetworks(networks);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const scanning = phase === 'scanning';
  const busy = phase === 'connecting' || phase === 'starting-driver' || phase === 'validating';
  const lastStage = progress.length ? progress[progress.length - 1] : null;
  const flowing = readiness ? readiness.topics.filter((t) => t.receiving).length : 0;
  const expected = readiness ? readiness.topics.length : 0;
  const charge = telemetry.connected && telemetry.battery_known ? telemetry.battery_pct : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="Fechar"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-abyss/55 backdrop-blur-[2px]"
      />

      <aside className="anim-slide-in relative flex h-full w-[420px] flex-col border-l border-strut-soft bg-hull-deep/95 backdrop-blur-xl">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-strut-soft px-5">
          <h2 className="font-cond text-base tracking-wide text-frost">Enlace com a aeronave</h2>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={onScan}
              disabled={scanning}
              aria-label="Procurar redes do Bebop"
              className="rounded-bezel p-1.5 text-haze transition-colors hover:bg-hull-raise hover:text-frost disabled:opacity-40"
            >
              <RefreshCw size={14} className={cn(scanning && 'animate-spin')} />
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Fechar painel"
              className="rounded-bezel p-1.5 text-haze transition-colors hover:bg-hull-raise hover:text-frost"
            >
              <X size={16} />
            </button>
          </div>
        </header>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
          {/* Ground telemetry, first: it is what the operator came to read. */}
          <section className="border-b border-strut-soft px-5 py-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3.5">
              <div className="flex items-center gap-2.5">
                <BatteryCell pct={charge} known={telemetry.battery_known} />
                <div>
                  <div className="font-cond text-3xs tracking-wide text-haze-deep">
                    {telemetry.battery_known
                      ? telemetry.battery_source === 'aircraft'
                        ? 'carga (ARSDK)'
                        : 'carga (console)'
                      : 'carga'}
                  </div>
                  <div className="tnum font-mono text-base text-frost">
                    {telemetry.battery_known ? `${charge}%` : '—'}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2.5">
                <RfBars bars={telemetry.data_fresh ? rfBars(telemetry.wifi_signal_dbm) : 0} />
                <div>
                  <div className="font-cond text-3xs tracking-wide text-haze-deep">
                    {telemetry.signal_source === 'aircraft' ? 'sinal (aeronave)' : 'sinal (estação)'}
                  </div>
                  <div className="tnum font-mono text-base text-frost">
                    {telemetry.data_fresh ? `${telemetry.wifi_signal_dbm} dBm` : '—'}
                  </div>
                </div>
              </div>

              <div>
                <div className="font-cond text-3xs tracking-wide text-haze-deep">
                  {telemetry.gps_fix ? 'posição (GPS)' : 'posição (odometria)'}
                </div>
                <div className="tnum font-mono text-xs text-frost">
                  {telemetry.data_fresh
                    ? `${telemetry.latitude.toFixed(5)}, ${telemetry.longitude.toFixed(5)}`
                    : '—'}
                </div>
              </div>

              <div>
                <div className="font-cond text-3xs tracking-wide text-haze-deep">altitude</div>
                <div className="tnum font-mono text-base text-frost">
                  {telemetry.data_fresh ? `${telemetry.altitude.toFixed(2)} m` : '—'}
                </div>
              </div>
            </div>
          </section>

          {/* Networks. */}
          <section className="border-b border-strut-soft px-3 py-3">
            {drones.length === 0 ? (
              <div className="flex flex-col items-start gap-2 px-2 py-3">
                <Wifi size={18} strokeWidth={1.5} className="text-haze-deep" />
                <p className="text-sm leading-tight text-frost">Nenhuma rede do Bebop por perto</p>
                <p className="max-w-[40ch] text-2xs leading-snug text-haze-deep">
                  Ligue a aeronave e espere a rede aparecer. O nome começa com
                  <span className="font-mono text-haze"> Bebop-</span> ou
                  <span className="font-mono text-haze"> Bebop2-</span>. Outras redes não aparecem
                  aqui.
                </p>
                <Button onClick={onScan} disabled={scanning} className="mt-1">
                  {scanning ? 'Procurando' : 'Procurar de novo'}
                </Button>
              </div>
            ) : (
              <ul className="flex flex-col gap-1">
                {drones.map((network) => {
                  const active = network.active || network.ssid === currentSsid;
                  return (
                    <li key={network.ssid}>
                      <button
                        type="button"
                        disabled={active || busy}
                        onClick={() => onConnect(network.ssid)}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-bezel border px-3 py-2.5 text-left transition-colors',
                          active
                            ? 'cursor-default border-mint/45 bg-mint/[0.08]'
                            : 'border-transparent hover:border-strut hover:bg-hull-deck'
                        )}
                      >
                        <Dot
                          state={active ? (telemetry.connected ? 'pass' : 'warn') : 'idle'}
                          pulse={active && !telemetry.connected}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-sm text-frost">
                            {network.ssid}
                          </span>
                          <span className="block text-2xs text-haze-deep">
                            {active
                              ? telemetry.connected
                                ? 'Conectado'
                                : 'Entrando'
                              : 'Toque para conectar'}
                          </span>
                        </span>
                        <RfBars bars={barsFromPercent(network.signal)} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* Driver. */}
          <section className="border-b border-strut-soft px-5 py-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Dot
                    state={flightReady ? 'pass' : driverRunning ? 'warn' : 'idle'}
                    pulse={phase === 'starting-driver' || phase === 'validating'}
                  />
                  <span className="text-sm text-frost">Driver ROS 2</span>
                </div>
                <p className="mt-0.5 font-mono text-2xs text-haze-deep">
                  {readiness ? `${flowing}/${expected} tópicos em tráfego` : 'aguardando o enlace'}
                </p>
              </div>
              {driverRunning ? (
                <Button variant="quiet" onClick={onStopDriver}>
                  Parar
                </Button>
              ) : (
                <Button
                  variant="quiet"
                  onClick={onStartDriver}
                  disabled={!telemetry.connected || busy}
                  icon={busy ? <Loader2 size={13} className="animate-spin" /> : undefined}
                >
                  Iniciar
                </Button>
              )}
            </div>

            {message ? (
              <p
                className={cn(
                  'mt-2.5 text-2xs leading-relaxed',
                  phase === 'error' ? 'text-ember' : 'text-haze'
                )}
                role="status"
              >
                {lastStage ? (
                  <span className="font-mono text-haze-deep">{lastStage.stage} </span>
                ) : null}
                {message}
              </p>
            ) : null}

          </section>
        </div>
      </aside>
    </div>
  );
};
