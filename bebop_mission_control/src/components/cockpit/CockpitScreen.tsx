import React, { useEffect } from 'react';
import type { MissionState, TelemetryView, TrackPoint } from '../../types/mission';
import type { LandProgressPhase, RawEvidence, VoiceLevel } from '../../types/bmg';
import type { Finding } from '../../lib/forensics';
import { StageBar } from './StageBar';
import { OpticalFeed } from './OpticalFeed';
import { TacticalMap } from './TacticalMap';
import { ForensicPanel } from './ForensicPanel';
import { abortEnabled } from '../../lib/flightState';
import { AbortControl } from './AbortControl';
import { cn } from '../../lib/format';
import { isMissionOver } from '../../lib/missionOutcome';
import type { FinishLockResult } from '../../lib/finishLock';

/**
 * Checks whether the key event target is an interactive text input or embedded terminal.
 * Used by R11 to prevent global Escape abort when typing.
 */
export function isTextInputOrTerminal(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable || target.getAttribute('contenteditable') === 'true') return true;
  if (target.closest('.xterm, [data-terminal], [data-terminal-title], textarea, input, select')) return true;
  return false;
}

interface CockpitScreenProps {
  telemetry: TelemetryView;
  track: TrackPoint[];
  stale: boolean;
  missionState: MissionState;
  stage: number;
  stageName: string;
  streamFps: number;
  streamBridgeUp: boolean;
  streamLive: boolean;
  streamWidth: number;
  streamHeight: number;
  streamSource: string;
  captureFlash: boolean;
  captureCount: number;
  latestCapture: RawEvidence | null;
  arrivalRadius: number | null;
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
  /** The report's closing line as the copilot said it. */
  reportClosing: string | null;
  onAbort: () => void;
  onFinish: () => void;
  /** The finish lock, evaluated by `useFinishLock` in the App. */
  finishLock: FinishLockResult;
  /** Closed-loop land supervisor progress phase (R6). */
  landProgress?: LandProgressPhase | null;
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
  reportClosing,
  onAbort,
  onFinish,
  finishLock,
  landProgress,
}) => {
  const running = missionState === 'running' || missionState === 'arming';
  const over = isMissionOver(missionState);

  // R11: Global Escape shortcut fires onAbort when abortEnabled,
  // ignored inside interactive text fields or diagnostics terminal.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (isTextInputOrTerminal(event.target)) return;
      if (abortEnabled(missionState, telemetry.flying_state, landProgress)) {
        event.preventDefault();
        onAbort();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [missionState, telemetry.flying_state, landProgress, onAbort]);

  // The last gimbal command on /bebop/move_camera, from the mission's ramp or
  // the station's slider, as the telemetry bridge echoes it. A command, not a
  // measurement: the Nectar SDK exposes no gimbal position, so this is the
  // closest the station can come to where the camera points.
  const gimbalTilt =
    typeof telemetry.camera_tilt_deg === 'number' && Number.isFinite(telemetry.camera_tilt_deg)
      ? telemetry.camera_tilt_deg
      : null;

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
            <div className="absolute inset-x-0 -bottom-6 z-30 flex justify-center">
              <AbortControl
                onAbort={onAbort}
                disabled={!abortEnabled(missionState, telemetry.flying_state, landProgress)}
                busy={missionState === 'aborting'}
                landProgress={landProgress}
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
            overview={missionState === 'idle'}
            flyingState={telemetry.flying_state ?? null}
          />
        </div>

        {/* What the flight is producing. */}
        <ForensicPanel
          latest={latestCapture}
          count={captureCount}
          stage={stage}
          missionOver={over}
          landed={landed}
          report={report}
          reportRevealed={reportRevealed}
          reportClosing={reportClosing}
          onFinish={onFinish}
          lock={finishLock}
        />
      </div>

    </div>
  );
};
