import React, { useEffect, useMemo, useRef, useState } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import type { TelemetryView } from '../../types/mission';
import type { LinkReadiness } from '../../types/bmg';
import type { ParamsDoc } from '../../hooks/useMissionParameters';
import { getPath } from '../../lib/paths';
import { parametersEnabled, type FlightMode } from '../../lib/flightMode';
import {
  preflightBlockedCode,
  preflightBlockedReason,
  preflightBlockedShort,
  type PreflightGateInput,
} from '../../lib/preflightGate';
import type { LaunchView } from '../../lib/launchSequence';
import { VideoBackdrop } from '../brand/VideoBackdrop';
import { LaunchDial } from './LaunchDial';
import { ControlDock } from './ControlDock';
import { MissionModeSelector } from './MissionModeSelector';
import { ParameterSheet } from './ParameterSheet';
import { cn } from '../../lib/format';

interface PreflightScreenProps {
  telemetry: TelemetryView;
  stale: boolean;
  readiness: LinkReadiness | null;
  flightReady: boolean;
  driverRunning: boolean;

  params: ParamsDoc | null;
  /** `MissionParameters.factory()`, for "Restaurar Padrões"; `null` until served. */
  paramDefaults: ParamsDoc | null;
  paramsStatus: 'loading' | 'ready' | 'saving' | 'error';
  paramsError: string | null;
  changedPaths: Set<string>;
  dirty: boolean;
  /** The working values equal the favourite of the mode (`activePresetId === favorite.id`). */
  favoriteActive: boolean;
  hasFavorite: boolean;
  onEdit: (path: string, value: unknown) => void;
  /** The flight mode of the working document (`readFlightMode`). */
  flightMode: FlightMode;
  onFlightModeChange: (mode: FlightMode) => void;
  onSave: () => void;
  onDiscard: () => void;
  onSaveFavorite: () => void;
  onRestoreFavorite: () => void;

  /** Battery failsafe threshold and switch, set from the top bar; they feed the launch gate. */
  batteryThresholdPct: number;
  batteryFailsafeEnabled: boolean;

  launchError: string | null;
  /** Field of the launch document the mission refused; the sheet marks it. */
  refusedPath?: string | null;
  /** An altitude the envelope of the launch surface moved (`useMissionParameters.altitudeNotice`). */
  altitudeNotice?: string | null;
  onLaunch: () => void;
  /** The launch sequence on the dial, or null when none is being prepared. */
  sequence?: LaunchView | null;
  /** Cancel the launch being prepared ("Abortar Missão"). */
  onAbortLaunch?: () => void;
  /** Short form of `launchError` for the centre of the dial. */
  launchErrorShort?: string | null;
  /**
   * True from the click on the launch dial until the mission ends; the
   * profile is part of the document already sent.
   */
  modeLocked?: boolean;
}

/**
 * Pre-flight: the aircraft on the ground, and one decision.
 *
 * The brand field is the screen rather than a backdrop to it, and the command
 * sits alone at its centre with the two things an operator reaches for next —
 * the flight parameters and the flight mode — directly beneath it. Charge,
 * radio, driver and voice live in the global top bar.
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

  const [sheet, setSheet] = useState<'none' | 'params'>('none');
  /** The operator tried to leave with edits pending once; the next attempt discards them. */
  const [exitArmed, setExitArmed] = useState(false);
  const [saveNudge, setSaveNudge] = useState(0);

  // Leaving the parameter sheet with edits pending shakes Salvar instead of
  // asking in a dialog; a second attempt leaves and restores the last saved
  // document. Launching still commits a pending draft silently (App.launch),
  // which this deliberately leaves alone.
  const closeSheet = () => {
    setExitArmed(false);
    setSheet('none');
  };
  const closeParams = () => {
    if (dirty && !exitArmed) {
      setExitArmed(true);
      setSaveNudge((count) => count + 1);
      return;
    }
    if (dirty) props.onDiscard();
    closeSheet();
  };
  useEffect(() => {
    if (!dirty) setExitArmed(false);
  }, [dirty]);

  // Escape leaves the sheet the way its close button does.
  const closeRef = useRef(closeParams);
  closeRef.current = closeParams;
  useEffect(() => {
    if (sheet !== 'params') return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sheet]);

  const benchMode = Boolean(getPath(params, 'no_fly'));
  const flightMode = props.flightMode;
  const paramsAvailable = parametersEnabled(flightMode);

  // The bench runs the stored document as it is: the sheet has nothing to
  // edit in Teste, and a switch to it closes an open sheet.
  useEffect(() => {
    if (!paramsAvailable && sheet === 'params') {
      closeSheet();
    }
  }, [paramsAvailable, sheet]);
  const missingTopics = readiness?.missing ?? [];

  /** What stops the mission from being commanded; see `lib/preflightGate.ts`. */
  const gate = useMemo<PreflightGateInput>(
    () => ({
      paramsStatus,
      driverRunning,
      benchMode,
      connected: telemetry.connected,
      flightReady,
      missingTopics,
      stale,
      flyingState: telemetry.flying_state,
      batteryPct: telemetry.battery_pct,
      batteryKnown: telemetry.battery_known,
      batteryFailsafeThreshold: props.batteryThresholdPct,
      batteryFailsafeEnabled: props.batteryFailsafeEnabled,
      bridgeReady: readiness?.ready,
    }),
    [
      paramsStatus,
      driverRunning,
      benchMode,
      telemetry.connected,
      telemetry.flying_state,
      telemetry.battery_pct,
      telemetry.battery_known,
      flightReady,
      missingTopics,
      stale,
      props.batteryThresholdPct,
      props.batteryFailsafeEnabled,
      readiness?.ready,
    ]
  );
  const blockedCode = useMemo(() => preflightBlockedCode(gate), [gate]);
  const blockedReason = useMemo(() => preflightBlockedReason(gate), [gate]);
  const blockedShort = useMemo(() => preflightBlockedShort(gate), [gate]);
  const sequence = props.sequence ?? null;

  // The sheet does not open over a launch being prepared.
  useEffect(() => {
    if (sequence !== null && sheet === 'params') {
      closeSheet();
    }
  }, [sequence, sheet]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-abyss">
      <VideoBackdrop subdued={sheet !== 'none'} />

      <div className="relative flex h-full flex-col">
        <main className="flex min-h-0 flex-1 flex-col items-center justify-center px-10 pb-16">
          <LaunchDial
            view={sequence}
            ready={blockedReason === null}
            blockedCode={blockedCode}
            blockedShort={blockedShort}
            blockedReason={
              props.launchError ? `A missão não iniciou: ${props.launchError}` : blockedReason
            }
            errorShort={props.launchError ? props.launchErrorShort ?? 'Falha ao iniciar' : null}
            benchMode={benchMode}
            onLaunch={props.onLaunch}
            beneath={
              <ControlDock
                fused={sequence !== null}
                onAbort={() => props.onAbortLaunch?.()}
                parameters={
                  <button
                    type="button"
                    onClick={() => setSheet('params')}
                    disabled={!paramsAvailable || sequence !== null}
                    className={cn(
                      'dock-pill',
                      !paramsAvailable && 'cursor-not-allowed opacity-45',
                      dirty && paramsAvailable && 'border-mint/45'
                    )}
                  >
                    <SlidersHorizontal size={15} strokeWidth={1.8} className="text-frost/75" />
                    <span>Parâmetros</span>
                    {dirty && paramsAvailable ? <span className="h-1.5 w-1.5 rounded-full bg-mint" /> : null}
                  </button>
                }
                mode={
                  <MissionModeSelector
                    value={flightMode}
                    onChange={props.onFlightModeChange}
                    disabled={props.modeLocked}
                  />
                }
              />
            }
          />
        </main>
      </div>

      {sheet === 'params' ? (
        <div
          className="fixed inset-x-0 bottom-0 top-12 z-50 flex items-center justify-center px-8 pb-6 pt-12"
          role="dialog"
          aria-modal="true"
          aria-label="Parâmetros"
        >
          <button
            type="button"
            aria-label="Fechar"
            onClick={closeParams}
            className="absolute inset-0 cursor-default bg-abyss/70 backdrop-blur-[3px]"
          />
          <div className="anim-rise relative flex max-h-full w-full max-w-[1320px] flex-col">
            {params ? (
              <ParameterSheet
                working={params}
                defaults={props.paramDefaults}
                changedPaths={props.changedPaths}
                dirty={dirty}
                saving={paramsStatus === 'saving'}
                favoriteActive={props.favoriteActive}
                hasFavorite={props.hasFavorite}
                onEdit={props.onEdit}
                onSave={props.onSave}
                nudgeSave={saveNudge}
                onClose={closeParams}
                onSaveFavorite={props.onSaveFavorite}
                onRestoreFavorite={props.onRestoreFavorite}
                refusedPath={props.refusedPath ?? null}
                altitudeNotice={props.altitudeNotice ?? null}
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
        </div>
      ) : null}
    </div>
  );
};
