import React, { useMemo } from 'react';
import type { MissionState, TelemetryView, TrackPoint } from '../../types/mission';
import type { RawEvidence, VoiceLevel } from '../../types/bmg';
import type { Finding } from '../../lib/forensics';
import { StageBar } from './StageBar';
import { OpticalFeed } from './OpticalFeed';
import { TacticalMap } from './TacticalMap';
import { ForensicPanel } from './ForensicPanel';
import { AbortControl } from './AbortControl';
import { cn } from '../../lib/format';

interface CockpitScreenProps {
  telemetry: TelemetryView;
  track: TrackPoint[];
  stale: boolean;
  missionState: MissionState;
  stage: number;
  stageName: string;
  exitCode: number | null;
  streamFps: number;
  streamBridgeUp: boolean;
  streamLive: boolean;
  streamWidth: number;
  streamHeight: number;
  streamSource: string;
  captureFlash: boolean;
  captureCount: number;
  latestCapture: RawEvidence | null;
  arrivalRadius: number;
  nadirTilt: number;
  searchTilt: number;
  /**
   * The aircraft is down from a flight that completed. Distinct from the
   * mission merely being over: a faulted flight has no assessment behind it,
   * so its capture stays a dismissible spotlight rather than becoming a modal
   * announcing an inspection that did not happen.
   */
  landed: boolean;
  /** Bench mode: the stage pills become the trigger for a single routine. */
  benchMode: boolean;
  benchStage: number | null;
  onRunStage: (stage: number) => void;
  onGotoStage: (stage: number) => void;
  /** A jump asked for whose stage the mission has not reported reaching yet. */
  pendingStage: number | null;
  /** The copilot's level, reachable without leaving the cockpit. */
  voice: VoiceLevel;
  onVolume: (volume: number) => void;
  onToggleMute: () => void;
  /** The camera gimbal, commanded from the feed itself. */
  cameraTilt: number | null;
  cameraAvailable: boolean;
  onCameraTilt: (degrees: number) => void;
  /** This flight's forensic findings, in this flight's order. */
  report: Finding[] | null;
  /** How many of them the copilot has read out so far. */
  reportRevealed: number;
  onAbort: () => void;
  onOpenEvidence: () => void;
  onFinish: () => void;
}

/**
 * The flight.
 *
 * Two columns. The left one is what the aircraft is doing — what it sees, and
 * where it is — with the abort straddling the seam between them so it belongs
 * to neither and is never hunted for. The right one is what the flight is
 * producing, held open from the first second so the operator knows where the
 * capture will land before it does.
 */
export const CockpitScreen: React.FC<CockpitScreenProps> = ({
  telemetry,
  track,
  stale,
  missionState,
  stage,
  stageName,
  exitCode,
  streamFps,
  streamBridgeUp,
  streamLive,
  streamWidth,
  streamHeight,
  streamSource,
  captureFlash,
  captureCount,
  latestCapture,
  arrivalRadius,
  nadirTilt,
  searchTilt,
  landed,
  benchMode,
  benchStage,
  onRunStage,
  onGotoStage,
  pendingStage,
  voice,
  onVolume,
  onToggleMute,
  cameraTilt,
  cameraAvailable,
  onCameraTilt,
  report,
  reportRevealed,
  onAbort,
  onOpenEvidence,
  onFinish,
}) => {
  const running = missionState === 'running' || missionState === 'arming';
  const over = missionState === 'finished' || missionState === 'faulted';

  const gimbalTilt = useMemo(() => {
    if (!running) return null;
    if (stage >= 4) return nadirTilt;
    if (stage >= 2) return searchTilt;
    return null;
  }, [running, stage, nadirTilt, searchTilt]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-2.5 p-2.5">
      <StageBar
        stage={stage}
        stageName={stageName}
        state={missionState}
        benchMode={benchMode}
        benchStage={benchStage}
        onRunStage={onRunStage}
        onGotoStage={onGotoStage}
        pendingStage={pendingStage}
        voice={voice}
        onVolume={onVolume}
        onToggleMute={onToggleMute}
      />

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.32fr)_minmax(0,1fr)] gap-2.5">
        {/* What the aircraft is doing. */}
        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(0,1.05fr)_minmax(0,1fr)] gap-2.5">
          <div className="relative min-h-0">
            <OpticalFeed
              fps={streamFps}
              bridgeUp={streamBridgeUp}
              live={streamLive}
              width={streamWidth}
              height={streamHeight}
              source={streamSource}
              telemetry={telemetry}
              running={running}
              flash={captureFlash}
              gimbalTilt={gimbalTilt}
              cameraTilt={cameraTilt}
              cameraAvailable={cameraAvailable}
              onCameraTilt={onCameraTilt}
            />

            {/* Anchored on the seam between the feed and the map. */}
            <div className="absolute inset-x-0 -bottom-5 z-30 flex justify-center">
              <AbortControl
                onAbort={onAbort}
                disabled={!running}
                busy={missionState === 'aborting'}
              />
            </div>
          </div>

          <TacticalMap
            track={track}
            latitude={telemetry.latitude}
            longitude={telemetry.longitude}
            heading={telemetry.heading}
            gpsFix={Boolean(telemetry.gps_fix)}
            baseLatitude={telemetry.base_known ? telemetry.base_latitude : null}
            baseLongitude={telemetry.base_known ? telemetry.base_longitude : null}
            baseSource={telemetry.base_source}
            arrivalRadius={arrivalRadius}
            stale={stale}
          />
        </div>

        {/* What the flight is producing. */}
        <ForensicPanel
          latest={latestCapture}
          count={captureCount}
          stage={stage}
          nadirTilt={nadirTilt}
          missionOver={over}
          landed={landed}
          report={report}
          reportRevealed={reportRevealed}
          onOpenLibrary={onOpenEvidence}
          onFinish={onFinish}
        />
      </div>

      {over ? (
        <div
          className="anim-rise pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2"
          role="status"
        >
          <div
            className={cn(
              'pointer-events-auto flex items-center gap-3 rounded-full border px-5 py-2.5 backdrop-blur-md',
              missionState === 'finished'
                ? 'border-mint/45 bg-mint/10'
                : 'border-ember/50 bg-ember/10'
            )}
          >
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                missionState === 'finished' ? 'bg-mint' : 'bg-ember'
              )}
            />
            <span className="text-sm text-frost">
              {missionState === 'finished'
                ? 'Missão encerrada'
                : `Missão interrompida${exitCode !== null ? ` (código ${exitCode})` : ''}`}
            </span>
            <span className="text-2xs text-haze">
              {captureCount > 0
                ? `${captureCount} ${captureCount === 1 ? 'evidência registrada' : 'evidências registradas'}`
                : 'nenhuma evidência registrada'}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
};
