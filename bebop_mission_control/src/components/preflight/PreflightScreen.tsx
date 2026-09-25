import React, { useMemo, useState } from 'react';
import { SlidersHorizontal, TerminalSquare, Wrench, X } from 'lucide-react';
import type { BatteryFailsafe, VoiceLevel } from '../../types/bmg';
import type { TelemetryView } from '../../types/mission';
import type { LinkProgressEvent, LinkReadiness, WifiNetwork } from '../../types/bmg';
import type { LinkPhase } from '../../hooks/useLink';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { VideoBackdrop } from '../brand/VideoBackdrop';
import { StatusBar } from '../shell/StatusBar';
import { LaunchDial } from './LaunchDial';
import { ConnectionSheet } from './ConnectionSheet';
import { ParameterSheet } from './ParameterSheet';
import { DiscardChangesDialog } from './DiscardChangesDialog';
import { cn } from '../../lib/format';

interface PreflightScreenProps {
  telemetry: TelemetryView;
  stale: boolean;
  networks: WifiNetwork[];
  currentSsid: string;
  scanning: boolean;
  linkPhase: LinkPhase;
  linkMessage: string | null;
  linkProgress: LinkProgressEvent[];
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;
  onScan: () => void;
  onConnect: (ssid: string) => void;
  onStartDriver: () => void;
  onStopDriver: () => void;

  params: ParamsDoc | null;
  paramsStatus: 'loading' | 'ready' | 'saving' | 'error';
  paramsError: string | null;
  changedPaths: Set<string>;
  dirty: boolean;
  hasPreset: boolean;
  presetActive: boolean;
  onEdit: (path: string, value: unknown) => void;
  onSave: () => void;
  onDiscard: () => void;
  onSavePreset: () => void;
  onApplyPreset: () => void;

  /** Battery failsafe, configured from the battery flyout. */
  failsafe: BatteryFailsafe;
  onFailsafeChange: (next: BatteryFailsafe) => void;
  failsafeTriggered: boolean;

  /** The copilot's output level, controlled from the status bar. */
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;

  launchError: string | null;
  onLaunch: () => void;
  onOpenDiagnostics: () => void;
}

/**
 * Pre-flight: the aircraft on the ground, and one decision.
 *
 * The brand field is the screen rather than a backdrop to it, and the command
 * sits alone at its centre with the two things an operator reaches for next —
 * the flight parameters and the bench — directly beneath it.
 *
 * Everything else reports rather than asks. Charge and radio sit in the top
 * right the way a menu bar states them; the machinery behind those two figures
 * opens from them on demand. What used to occupy this screen — a ground
 * telemetry card on the left, a parameters card on the right, a build chip and
 * an environment chip in the corner — said the same few things four times and
 * left the command competing with them for the centre.
 */
export const PreflightScreen: React.FC<PreflightScreenProps> = (props) => {
  const {
    telemetry,
    stale,
    driverRunning,
    readiness,
    flightReady,
    params,
    paramsStatus,
    paramsError,
    dirty,
  } = props;

  const [sheet, setSheet] = useState<'none' | 'link' | 'params'>('none');
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);

  // Closing the parameter sheet with edits pending asks first; confirming
  // restores the last saved document. Launching still commits a pending draft
  // silently (App.launch), which this deliberately leaves alone.
  const closeParams = () => {
    if (dirty) setConfirmingDiscard(true);
    else setSheet('none');
  };
  const discardAndClose = () => {
    props.onDiscard();
    setConfirmingDiscard(false);
    setSheet('none');
  };

  const benchMode = Boolean(getPath(params, 'no_fly'));
  const missingTopics = readiness?.missing ?? [];


  /**
   * What stops the mission from being commanded.
   *
   * Bench mode is the point at which these differ. `mission.py --no-fly` still
   * needs the driver, because it connects to it and subscribes to the camera,
   * but the kinematic simulator stands in for the airframe: there is no Wi-Fi
   * link to the aircraft and no /bebop/odom to wait for.
   */
  const blockedReason = useMemo(() => {
    if (paramsStatus === 'error') return 'Parâmetros indisponíveis';
    if (!driverRunning) return 'Driver ROS 2 fora do ar';
    if (benchMode) return null;
    if (!telemetry.connected) return 'Conecte-se à rede da aeronave para liberar o comando';
    if (!flightReady) {
      return missingTopics.length
        ? `Tópicos sem tráfego: ${missingTopics.join(', ')}`
        : 'Validando tópicos da aeronave';
    }
    if (stale) return 'Sem odometria recente';
    return null;
  }, [
    paramsStatus,
    driverRunning,
    benchMode,
    telemetry.connected,
    flightReady,
    missingTopics,
    stale,
  ]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-abyss">
      <VideoBackdrop subdued={sheet !== 'none'} />

      <div className="relative flex h-full flex-col">
        {/* The dock floats over the centre of this row; both clusters stay clear of it. */}
        <header className="flex shrink-0 items-start justify-between p-7">
          {/* The video background (/assets/Tech4aiMVPvideo.mp4) renders the
              Tech4ai brand mark in this corner. An empty placeholder preserves
              header geometry without obscuring or duplicating the video's mark. */}
          <span aria-hidden className="w-10" />

          <div className="flex items-center gap-2.5">
            <StatusBar
              telemetry={telemetry}
              onOpenLink={() => setSheet('link')}
              networks={props.networks}
              currentSsid={props.currentSsid}
              scanning={props.scanning}
              linkPhase={props.linkPhase}
              linkMessage={props.linkMessage}
              readiness={readiness}
              flightReady={flightReady}
              driverRunning={driverRunning}
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

            <button
              type="button"
              onClick={props.onOpenDiagnostics}
              aria-label="Abrir diagnóstico"
              title="Diagnóstico"
              className="rounded-full border border-frost/12 bg-abyss/45 p-2 text-haze backdrop-blur-md transition-colors hover:border-frost/30 hover:text-frost"
            >
              <TerminalSquare size={14} strokeWidth={1.8} />
            </button>
          </div>
        </header>

        <main className="flex min-h-0 flex-1 flex-col items-center justify-center px-10 pb-16">
          <LaunchDial
            ready={blockedReason === null}
            blockedReason={
              props.launchError ? `A missão não iniciou: ${props.launchError}` : blockedReason
            }
            benchMode={benchMode}
            onLaunch={props.onLaunch}
            beneath={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSheet('params')}
                  className={cn(
                    'flex items-center gap-2 rounded-full border px-5 py-2 backdrop-blur-md transition-colors',
                    dirty
                      ? 'border-mint/45 bg-mint/[0.08] text-frost hover:border-mint/70'
                      : 'border-frost/15 bg-abyss/45 text-frost/85 hover:border-frost/35 hover:text-frost'
                  )}
                >
                  <SlidersHorizontal size={13} strokeWidth={1.8} className="text-haze" />
                  <span className="font-cond text-sm tracking-wide">Parâmetros de voo</span>
                  {dirty ? <span className="h-1.5 w-1.5 rounded-full bg-mint" /> : null}
                </button>

                {/* One switch, not a panel. What bench mode does — keep the
                    motors inert — is a property of the next launch, and the
                    routines it used to list are now the stage pills in the
                    cockpit. Its colour is the brand's electric cyan, and the
                    dial above takes the same colour while it is on, so a
                    rehearsal never reads as the mint of a real flight. */}
                <button
                  type="button"
                  role="switch"
                  aria-checked={benchMode}
                  onClick={() => props.onEdit('no_fly', !benchMode)}
                  title={
                    benchMode
                      ? 'Modo bancada ativo: os motores não giram. As etapas são disparadas na Cabine.'
                      : 'Ativar o modo bancada: motores inertes, visão e gimbal reais.'
                  }
                  className={cn(
                    'flex items-center gap-2 rounded-full border px-5 py-2 backdrop-blur-md transition-all',
                    benchMode
                      ? 'border-cyan/60 bg-cyan/10 text-cyan shadow-bench hover:border-cyan'
                      : 'border-frost/15 bg-abyss/45 text-frost/85 hover:border-frost/35 hover:text-frost'
                  )}
                >
                  <Wrench
                    size={13}
                    strokeWidth={1.8}
                    className={benchMode ? 'text-cyan' : 'text-haze'}
                  />
                  <span className="font-cond text-sm tracking-wide">Bancada</span>
                  {benchMode ? <span className="h-1.5 w-1.5 rounded-full bg-cyan anim-breathe" /> : null}
                </button>
              </div>
            }
          />
        </main>
      </div>

      <ConnectionSheet
        open={sheet === 'link'}
        onClose={() => setSheet('none')}
        telemetry={telemetry}
        networks={props.networks}
        currentSsid={props.currentSsid}
        phase={props.linkPhase}
        message={props.linkMessage}
        progress={props.linkProgress}
        readiness={readiness}
        flightReady={flightReady}
        driverRunning={driverRunning}
        onScan={props.onScan}
        onConnect={props.onConnect}
        onStartDriver={props.onStartDriver}
        onStopDriver={props.onStopDriver}
      />

      {sheet === 'params' ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-8"
          role="dialog"
          aria-modal="true"
          aria-label="Parâmetros de voo"
        >
          <button
            type="button"
            aria-label="Fechar"
            onClick={closeParams}
            className="absolute inset-0 cursor-default bg-abyss/70 backdrop-blur-[3px]"
          />
          <div className="anim-rise relative flex h-full max-h-[780px] w-full max-w-[1120px] flex-col">
            <button
              type="button"
              onClick={closeParams}
              aria-label="Fechar"
              className="absolute -top-10 right-0 rounded-bezel p-1.5 text-haze transition-colors hover:bg-hull-raise hover:text-frost"
            >
              <X size={16} />
            </button>

            {params ? (
              <ParameterSheet
                working={params}
                changedPaths={props.changedPaths}
                dirty={dirty}
                saving={paramsStatus === 'saving'}
                hasPreset={props.hasPreset}
                presetActive={props.presetActive}
                onEdit={props.onEdit}
                onSave={props.onSave}
                onDiscard={props.onDiscard}
                onSavePreset={props.onSavePreset}
                onApplyPreset={props.onApplyPreset}
              />
            ) : (
              <div className="flex h-full items-center justify-center rounded-panel border border-strut-soft bg-hull">
                <p className="max-w-[44ch] text-center text-sm text-haze">
                  {paramsStatus === 'loading'
                    ? 'Lendo mission_config.json'
                    : paramsError ?? 'Parâmetros indisponíveis'}
                </p>
              </div>
            )}
          </div>
          {confirmingDiscard ? (
            <DiscardChangesDialog onConfirm={discardAndClose} onCancel={() => setConfirmingDiscard(false)} />
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
