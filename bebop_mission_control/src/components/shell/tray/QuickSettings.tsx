import React, { useEffect, useRef, useState } from 'react';
import { BatteryWarning, Volume, Volume1, Volume2, VolumeX } from 'lucide-react';
import type { TelemetryView } from '../../../types/mission';
import type { BatteryFailsafe, LinkReadiness, VoiceLevel, WifiNetwork } from '../../../types/bmg';
import { cn, rfBars } from '../../../lib/format';
import { FAILSAFE_MAX_PCT, FAILSAFE_MIN_PCT } from '../../../lib/batteryFailsafe';
import { batteryAlert, ros2Subtitle, ros2Tone, volumeLevel, wifiLevel, type Ros2Tone } from '../../../lib/trayState';
import { BatteryGlyph, Ros2Glyph, WifiGlyph } from '../glyphs';
import { SuccessCheck } from '../motion';
import { QsSlider, QsSwitch, QsTile } from './controls';
import { WifiMenu } from './WifiMenu';
import { Ros2Menu } from './Ros2Menu';
import { useFlyout } from './TrayButton';

export interface QuickSettingsProps {
  telemetry: TelemetryView;
  networks: WifiNetwork[];
  currentSsid: string;
  /** A forced rescan is running. */
  scanning: boolean;
  /** A join or a bring-up is running (`useLink` phase connecting, starting-driver or validating). */
  busy: boolean;
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  /** The launch sequence is running (D6): everything but the volume is inert. */
  frozen: boolean;
  wifiPower?: boolean;
  onToggleWifiPower?: (enabled: boolean) => void;
  onDisconnectWifi?: () => void;
  onScan: () => void;
  onConnect: (ssid: string, password?: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;
  failsafe: BatteryFailsafe;
  onFailsafeChange: (next: BatteryFailsafe) => void;
  /** The failsafe has already commanded the return on this flight. */
  failsafeTriggered: boolean;
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
}

const VOLUME_ICONS = [VolumeX, Volume, Volume1, Volume2] as const;

/** How long the tick holds the place of the ROS 2 mark once the nodes are up, ms. */
export const ROS2_CONFIRM_MS = 1800;

type Submenu = 'wifi' | 'ros2';

/**
 * The ROS 2 bring-up just completed: true for {@link ROS2_CONFIRM_MS} after the
 * tone goes from `busy` to `ready`. A station that opens with the driver
 * already up confirms nothing; there was no wait to close.
 */
function useReadyConfirmation(tone: Ros2Tone): boolean {
  const previous = useRef(tone);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    const was = previous.current;
    previous.current = tone;
    if (was !== 'busy' || tone !== 'ready') return;
    setConfirming(true);
    const id = window.setTimeout(() => setConfirming(false), ROS2_CONFIRM_MS);
    return () => window.clearTimeout(id);
  }, [tone]);
  return confirming;
}

/**
 * The right end of the bar, as the quick settings of Ubuntu 24.04: one pill
 * holding the ROS 2 mark, the Wi-Fi, the voice and the battery, and one menu
 * behind it.
 *
 * The menu keeps what applies to a ground station and drops the rest (power,
 * lock, screenshot, brightness, Bluetooth, night light, dark style, power
 * mode, aeroplane mode). Top row: the charge, and the battery failsafe switch
 * where GNOME has its system buttons. Then the copilot's volume and, with the
 * failsafe on, its threshold. Then two tiles, Wi-Fi and ROS 2, each with a
 * submenu that opens below them.
 */
export const QuickSettings: React.FC<QuickSettingsProps> = (props) => {
  const { open, setOpen, hostRef } = useFlyout();
  const [submenu, setSubmenu] = useState<Submenu | null>(null);

  const tone = ros2Tone({ driverRunning: props.driverRunning, busy: props.busy, ready: props.flightReady });
  const confirming = useReadyConfirmation(tone);

  const connected = Boolean(props.telemetry.connected);
  const known = connected && Boolean(props.telemetry.battery_known);
  const charge = known ? props.telemetry.battery_pct : 0;
  const alert = batteryAlert(known, charge);

  const level = volumeLevel(props.voice);
  const VolumeIcon = VOLUME_ICONS[level];
  const volumePercent = props.voice.muted ? 0 : Math.round(props.voice.volume * 100);

  const hostSsid = props.currentSsid || props.telemetry.wifi_ssid;
  const bars = props.telemetry.data_fresh
    ? rfBars(props.telemetry.wifi_signal_dbm)
    : wifiLevel(props.networks.find((network) => network.active)?.signal ?? 0);
  const wifiBars = hostSsid || connected ? Math.max(bars, 1) : 0;
  const seeking = props.scanning || props.busy;

  useEffect(() => {
    if (!open) setSubmenu(null);
  }, [open]);

  // Opening the Wi-Fi submenu is a request to see what is around now.
  useEffect(() => {
    if (submenu === 'wifi') props.onScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submenu]);

  const toggleSubmenu = (next: Submenu) => setSubmenu((current) => (current === next ? null : next));
  const wifiPower = props.wifiPower ?? true;
  const wifiActive = wifiPower && Boolean(hostSsid);
  const wifiSubtitle = !wifiPower ? 'Desativado' : hostSsid || 'Desconectado';
  const handleToggleWifi = () => {
    if (props.onToggleWifiPower) {
      props.onToggleWifiPower(!wifiPower);
    } else if (hostSsid && props.onDisconnectWifi) {
      props.onDisconnectWifi();
    }
  };
  const canStart = !props.busy;
  const setVolume = (percent: number) => {
    const next = percent / 100;
    props.onVolume(next);
    // Zero is mute: the slider never shows a level the copilot is not speaking at.
    if ((next === 0) !== props.voice.muted) props.onToggleMute();
  };

  return (
    <div ref={hostRef} className="relative">
      <button
        type="button"
        aria-label="Configurações rápidas"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-qs-cluster
        onClick={() => setOpen((previous) => !previous)}
        className={cn('qs-cluster', open && 'qs-cluster-open')}
      >
        <span data-qs-item="ros2" className="qs-cluster-item">
          {confirming ? <SuccessCheck size={16} className="text-frost" /> : <Ros2Glyph tone={tone} />}
        </span>
        <span data-qs-item="wifi" className="qs-cluster-item">
          <WifiGlyph level={wifiBars} seeking={seeking} />
        </span>
        <span data-qs-item="volume" data-level={level} className="qs-cluster-item">
          <VolumeIcon size={16} strokeWidth={2} />
        </span>
        <span data-qs-item="battery" className="qs-cluster-item gap-1.5">
          <BatteryGlyph pct={charge} known={known} alert={alert} />
          <span className={cn('tnum text-[13px] font-medium', alert ? 'text-ember' : known ? 'text-frost' : 'text-frost/50')}>
            {known ? `${charge}%` : '—'}
          </span>
          {props.failsafeTriggered ? <span aria-label="failsafe disparado" className="h-1.5 w-1.5 rounded-full bg-ember anim-breathe" /> : null}
        </span>
      </button>

      {open ? (
        <div role="dialog" aria-label="Configurações rápidas" className="qs-panel" data-qs-panel>
          <div className="flex items-center justify-between gap-3">
            <span className="qs-chip" data-qs-charge aria-label="Carga da bateria">
              <BatteryGlyph pct={charge} known={known} alert={alert} />
              <span className={cn('tnum text-[14px]', alert ? 'text-ember' : 'text-frost')}>{known ? `${charge}%` : '—'}</span>
            </span>
            <span className="qs-chip gap-3 pr-1.5">
              <span className="text-[14px] text-frost">Failsafe</span>
              <QsSwitch
                label="Failsafe de bateria"
                checked={props.failsafe.enabled}
                disabled={props.frozen}
                onChange={(enabled) => props.onFailsafeChange({ ...props.failsafe, enabled })}
              />
            </span>
          </div>

          <div className="mt-3 flex flex-col">
            <QsSlider
              label="Volume do copiloto"
              value={volumePercent}
              onChange={setVolume}
              icon={<VolumeIcon size={18} strokeWidth={2} />}
            />
            <div className="qs-collapse" data-open={props.failsafe.enabled || undefined}>
              <div className="min-h-0 overflow-hidden">
                {props.failsafe.enabled ? (
                  <QsSlider
                    label="Limiar do failsafe de bateria"
                    value={props.failsafe.thresholdPct}
                    min={FAILSAFE_MIN_PCT}
                    max={FAILSAFE_MAX_PCT}
                    disabled={props.frozen}
                    onChange={(thresholdPct) => props.onFailsafeChange({ ...props.failsafe, thresholdPct })}
                    icon={<BatteryWarning size={18} strokeWidth={2} />}
                    readout={`${props.failsafe.thresholdPct}%`}
                  />
                ) : null}
              </div>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2.5">
            <QsTile
              id="wifi"
              icon={<WifiGlyph level={wifiPower ? wifiBars : 0} seeking={seeking && wifiPower} size={17} />}
              title="Wi-Fi"
              subtitle={wifiSubtitle}
              active={wifiActive}
              expanded={submenu === 'wifi'}
              onMain={handleToggleWifi}
              mainLabel={wifiPower ? 'Desligar Wi-Fi' : 'Ligar Wi-Fi'}
              mainDisabled={props.frozen}
              onExpand={() => toggleSubmenu('wifi')}
              expandLabel="Abrir redes Wi-Fi"
            />
            <QsTile
              id="ros2"
              icon={<Ros2Glyph tone={tone} size={17} />}
              title="ROS 2"
              subtitle={ros2Subtitle(tone, props.readiness?.topics ?? null)}
              active={props.driverRunning}
              expanded={submenu === 'ros2'}
              onMain={props.driverRunning ? props.onStopDriver : props.onStartDriver}
              mainLabel={props.driverRunning ? 'Parar ROS 2' : 'Iniciar ROS 2'}
              mainDisabled={props.frozen}
              onExpand={() => toggleSubmenu('ros2')}
              expandLabel="Abrir tópicos do ROS 2"
            />
          </div>

          <div className="qs-collapse" data-open={submenu !== null || undefined}>
            <div className="min-h-0 overflow-hidden">
              <div className="pt-2.5">
                {submenu === 'wifi' ? (
                  <WifiMenu
                    networks={props.networks}
                    currentSsid={props.currentSsid}
                    scanning={props.scanning}
                    busy={props.busy}
                    frozen={props.frozen}
                    onDisconnect={props.onDisconnectWifi}
                    onScan={props.onScan}
                    onConnect={props.onConnect}
                  />
                ) : submenu === 'ros2' ? (
                  <Ros2Menu
                    readiness={props.readiness}
                    tone={tone}
                    driverRunning={props.driverRunning}
                    canStart={canStart}
                    frozen={props.frozen}
                    onStart={props.onStartDriver}
                    onStop={props.onStopDriver}
                  />
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};
