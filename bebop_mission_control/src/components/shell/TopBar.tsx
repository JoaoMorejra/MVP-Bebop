import React from 'react';
import type { Screen, TelemetryView } from '../../types/mission';
import type { BatteryFailsafe, LinkReadiness, VoiceLevel, WifiNetwork } from '../../types/bmg';
import type { LinkPhase } from '../../hooks/useLink';
import { TabSwitch } from './TabSwitch';
import { TiltLogo } from './motion';
import { QuickSettings } from './tray/QuickSettings';

interface TopBarProps {
  screen: Screen;
  onNavigate: (screen: Screen) => void;
  live: boolean;
  locked: boolean;
  /** The launch sequence is running (D6): everything but the volume is inert. */
  frozen: boolean;
  telemetry: TelemetryView;
  networks: WifiNetwork[];
  currentSsid: string;
  scanning: boolean;
  linkPhase: LinkPhase;
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  wifiPower?: boolean;
  onToggleWifiPower?: (enabled: boolean) => void;
  onDisconnectWifi?: () => void;
  onScan: () => void;
  onConnect: (ssid: string, password?: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;
  failsafe: BatteryFailsafe;
  onFailsafeChange: (next: BatteryFailsafe) => void;
  failsafeTriggered: boolean;
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
}

const BUSY_PHASES: ReadonlySet<LinkPhase> = new Set(['connecting', 'starting-driver', 'validating']);

/** Height of the wordmark on the bar, px: the largest that keeps a 6 px margin top and bottom. */
const LOGO_HEIGHT = 30;

/**
 * The global bar, on both tabs, after the top bar of Ubuntu 24.04: the
 * wordmark on the left, the two tabs where GNOME keeps its clock, and the
 * quick settings on the right, one pill and one menu. Set in the Ubuntu face,
 * white on the deep hull, without auxiliary text.
 */
export const TopBar: React.FC<TopBarProps> = (props) => (
  <header className="topbar relative z-40 grid h-12 shrink-0 grid-cols-[1fr_auto_1fr] items-center px-3 font-ui">
    <div className="flex items-center">
      <TiltLogo height={LOGO_HEIGHT} />
    </div>
    <TabSwitch
      screen={props.screen}
      onNavigate={props.onNavigate}
      live={props.live}
      locked={props.locked}
      frozen={props.frozen}
    />
    <div className="flex items-center justify-end" data-tray>
      <QuickSettings
        telemetry={props.telemetry}
        networks={props.networks}
        currentSsid={props.currentSsid}
        scanning={props.scanning}
        busy={BUSY_PHASES.has(props.linkPhase)}
        readiness={props.readiness}
        flightReady={props.flightReady}
        driverRunning={props.driverRunning}
        frozen={props.frozen}
        wifiPower={props.wifiPower}
        onToggleWifiPower={props.onToggleWifiPower}
        onDisconnectWifi={props.onDisconnectWifi}
        onScan={props.onScan}
        onConnect={props.onConnect}
        onStartDriver={props.onStartDriver}
        onStopDriver={props.onStopDriver}
        failsafe={props.failsafe}
        onFailsafeChange={props.onFailsafeChange}
        failsafeTriggered={props.failsafeTriggered}
        voice={props.voice}
        onVolume={props.onVolume}
        onToggleMute={props.onToggleMute}
      />
    </div>
  </header>
);
