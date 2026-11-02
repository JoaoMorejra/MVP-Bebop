import React, { useEffect, useRef } from 'react';
import { useBridge } from '../../hooks/useBridge';
import type { MissionState, TelemetryView, TrackPoint } from '../../types/mission';
import type { LandProgressPhase, RawEvidence } from '../../types/bmg';
import type { Finding } from '../../lib/forensics';
import type { FindingReading } from '../../hooks/useCopilot';
import { StageBar } from './StageBar';
import type { MissionMode } from '../../lib/missionMode';
import { OpticalFeed } from './OpticalFeed';
import { TacticalMap } from './TacticalMap';
import { ForensicPanel } from './ForensicPanel';
import { abortEnabled } from '../../lib/flightState';
import { isAbortShortcut } from '../../lib/abortShortcut';
import { AbortControl } from './AbortControl';
import type { FillState } from '../../lib/stageProgress';
import { isTextInput } from '../../lib/textInput';
import { nudgeEnabled } from '../../lib/nudge';
import { useNudge } from '../../hooks/useNudge';
import { isMissionOver } from '../../lib/missionOutcome';
import type { FinishLockResult } from '../../lib/finishLock';

export { isTextInput } from '../../lib/textInput';

interface CockpitScreenProps {
  /** The stage strip's fill, driven by the flight (`useStageProgress`). */
  stageFill?: FillState;
  telemetry: TelemetryView;
  track: TrackPoint[];
  stale: boolean;
  missionState: MissionState;
  stage: number;
  missionMode?: MissionMode;
  /** Return plan of the flight on screen (`rtl.enabled`). */
  rtlEnabled?: boolean;
  streamFps: number;
  streamBridgeUp: boolean;
  streamLive: boolean;
  streamWidth: number;
  streamHeight: number;
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
  /** Seconds before the bench routine spawns, for its stage pill; `null` outside that wait. */
  benchCountdown?: number | null;
  onRunStage: (stage: number) => void;
  onGotoStage: (stage: number) => void;
  /** A jump asked for whose stage the mission has not reported reaching yet. */
  pendingStage: number | null;
  /** The camera gimbal, commanded from the feed itself. */
  cameraTilt: number | null;
  cameraAvailable: boolean;
  onCameraTilt: (degrees: number) => void;
  /** This flight's forensic findings, in this flight's order. */
  report: Finding[] | null;
  /** How many of them the copilot has read out so far. */
  reportRevealed: number;
  /** How many findings the copilot has begun to read; defaults to `reportRevealed`. */
  reportAnnounced?: number;
  /** When each finding's sentence stops sounding, which paces its typed text. */
  reportReadings?: ReadonlyArray<FindingReading | undefined>;
  /** The aircraft is down and the report waits for the victim assessment. */
  reportAnalyzing: boolean;
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
  missionMode,
  rtlEnabled,
  streamFps,
  streamBridgeUp,
  streamLive,
  streamWidth,
  streamHeight,
  captureFlash,
  captureCount,
  latestCapture,
  arrivalRadius,
  landed,
  benchMode,
  benchStage,
  benchCountdown = null,
  onRunStage,
  onGotoStage,
  pendingStage,
  cameraTilt,
  cameraAvailable,
  onCameraTilt,
  report,
  reportRevealed,
  reportAnnounced,
  reportReadings,
  reportAnalyzing,
  reportClosing,
  onAbort,
  onFinish,
  finishLock,
  landProgress,
  stageFill,
}) => {
  const bridge = useBridge();
  const running = missionState === 'running' || missionState === 'arming';
  const over = isMissionOver(missionState);

  const armed = abortEnabled(missionState, telemetry.flying_state, landProgress);
  const viaMain = Boolean(bridge?.onAbortShortcut && bridge.setCockpitAbortArmed);

  // D8: the main process takes Ctrl/Cmd+C and Escape before the page while the
  // abort is available, even with the focus in the video iframe or on a
  // slider, and hands them back here. Disarmed on unmount.
  useEffect(() => {
    if (!viaMain) return;
    bridge!.setCockpitAbortArmed!(armed);
  }, [bridge, viaMain, armed]);
  useEffect(() => {
    if (!viaMain) return;
    return () => bridge!.setCockpitAbortArmed!(false);
  }, [bridge, viaMain]);

  const abortRef = useRef({ armed, onAbort });
  abortRef.current = { armed, onAbort };
  useEffect(() => {
    if (!viaMain) return;
    return bridge!.onAbortShortcut!(() => {
      if (abortRef.current.armed) abortRef.current.onAbort();
    });
  }, [bridge, viaMain]);

  // R11 without the bridge (a browser session, an older preload): Escape and
  // Ctrl/Cmd+C fire onAbort exactly as the button does, while the abort is
  // available. Ignored inside text fields, where Ctrl+C is copy. When the
  // abort is not available the event is left alone, so copy still works on the
  // ground. With the bridge the main process consumes the key first, so the
  // two never both fire.
  useEffect(() => {
    if (viaMain) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isAbortShortcut(event) === null) return;
      if (isTextInput(event.target)) return;
      if (abortEnabled(missionState, telemetry.flying_state, landProgress)) {
        event.preventDefault();
        onAbort();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [viaMain, missionState, telemetry.flying_state, landProgress, onAbort]);

  // Keyboard nudge (W A S D Q R): an additive trim of the autonomous command,
  // never a takeover. The mission applies its own, finer permit on top.
  const nudgeArmed = nudgeEnabled({
    missionState,
    flyingState: telemetry.flying_state,
    stage,
    landProgress,
  });
  // The keys stay live without a badge over the feed (operator's choice).
  useNudge(bridge, nudgeArmed);

  // The last gimbal command on /bebop/move_camera, from the mission's ramp or
  // the station's slider, as the telemetry bridge echoes it. A command, not a
  // measurement: the Nectar SDK exposes no gimbal position, so this is the
  // closest the station can come to where the camera points.
  const gimbalTilt =
    typeof telemetry.camera_tilt_deg === 'number' && Number.isFinite(telemetry.camera_tilt_deg)
      ? telemetry.camera_tilt_deg
      : null;

  const missionStarted = missionState !== 'idle' || benchStage !== null;

  if (!missionStarted) {
    return (
      <div data-cockpit-layout="standby" className="flex h-full min-h-0 flex-col p-1.5">
        <div className="grid min-h-0 min-w-0 flex-1 grid-rows-[minmax(0,1.5fr)_minmax(0,1fr)] gap-1.5">
          <div className="relative min-h-0">
            <OpticalFeed
              fps={streamFps}
              bridgeUp={streamBridgeUp}
              live={streamLive}
              width={streamWidth}
              height={streamHeight}
              telemetry={telemetry}
              running={running}
              flash={captureFlash}
              gimbalTilt={gimbalTilt}
              cameraTilt={cameraTilt}
              cameraAvailable={cameraAvailable}
              onCameraTilt={onCameraTilt}
            />
          </div>

          <TacticalMap
            track={track}
            heading={telemetry.heading}
            arrivalRadius={arrivalRadius}
            stale={stale}
            flyingState={telemetry.flying_state ?? null}
          />
        </div>
      </div>
    );
  }

  // One 6 px gutter between every tile, the window edge included: the stages,
  // the feed, the map and the forensic panel read as a single grid.
  return (
    <div data-cockpit-layout="mission" className="flex h-full min-h-0 flex-col gap-1.5 p-1.5">
      <StageBar
        stage={stage}
        missionMode={missionMode}
        rtlEnabled={rtlEnabled}
        state={missionState}
        benchMode={benchMode}
        benchStage={benchStage}
        benchCountdown={benchCountdown}
        onRunStage={onRunStage}
        onGotoStage={onGotoStage}
        pendingStage={pendingStage}
        fill={stageFill}
      />

      {/* The feed is drawn object-cover from a 16:9 stream: the columns and rows
          keep its box near 16:9 at the station's 1600x900, so the picture is
          not cropped to a letterbox. */}
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-1.5">
        {/* What the aircraft is doing. */}
        <div className="grid min-h-0 min-w-0 grid-rows-[minmax(0,1.5fr)_minmax(0,1fr)] gap-1.5">
          <div className="relative min-h-0">
            <OpticalFeed
              fps={streamFps}
              bridgeUp={streamBridgeUp}
              live={streamLive}
              width={streamWidth}
              height={streamHeight}
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
            heading={telemetry.heading}
            arrivalRadius={arrivalRadius}
            stale={stale}
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
          reportAnnounced={reportAnnounced}
          reportReadings={reportReadings}
          flightProgress={stageFill ? (stageFill.fills[3] + stageFill.fills[4]) / 2 : undefined}
          waitProgress={stageFill ? (stageFill.fills[0] + stageFill.fills[1] + stageFill.fills[2] + stageFill.fills[3]) / 4 : 0}
          reportAnalyzing={reportAnalyzing}
          reportClosing={reportClosing}
          onFinish={onFinish}
          lock={finishLock}
        />
      </div>

    </div>
  );
};
