import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { Screen } from './types/mission';
import type { Finding } from './lib/forensics';
import type { BatteryFailsafe } from './types/bmg';
import { PreflightScreen } from './components/preflight/PreflightScreen';
import { BenchWarmupOverlay } from './components/preflight/BenchWarmupOverlay';
import { CountdownOverlay } from './components/preflight/CountdownOverlay';
import { CockpitScreen } from './components/cockpit/CockpitScreen';
import { EvidenceScreen } from './components/evidence/EvidenceScreen';
import { DiagnosticsScreen } from './components/diagnostics/DiagnosticsScreen';
import { TabSwitch } from './components/shell/TabSwitch';
import { useBridge } from './hooks/useBridge';
import { useTelemetry } from './hooks/useTelemetry';
import { useLink } from './hooks/useLink';
import { useMissionRuntime } from './hooks/useMissionRuntime';
import { useMissionParameters } from './hooks/useMissionParameters';
import { preflightLocked } from './lib/navigationLock';
import { useStreamHealth } from './hooks/useStreamHealth';
import { useEvidence } from './hooks/useEvidence';
import { useCameraTilt } from './hooks/useCameraTilt';
import {
  useCopilot,
  useFlightNarration,
  useForensicNarration,
  useNarrationQueue,
} from './hooks/useCopilot';
import { useVoiceLevel } from './hooks/useVoiceLevel';
import { buildForensicReport } from './lib/forensics';
import { getPath } from './lib/paths';

const num = (doc: unknown, path: string, fallback: number): number => {
  const v = getPath(doc, path);
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
};

/** The station is two tabs; these open over whichever one is current. */
type Overlay = 'none' | 'evidence' | 'diagnostics';

/** `?screen=cockpit` opens a tab directly, for screenshots and tests. */
function initialScreen(): Screen {
  if (typeof window === 'undefined') return 'preflight';
  const requested = new URLSearchParams(window.location.search).get('screen');
  return requested === 'cockpit' ? 'cockpit' : 'preflight';
}

const FAILSAFE_KEY = 'bmg.battery-failsafe.v1';
const FAILSAFE_DEFAULT: BatteryFailsafe = { enabled: true, thresholdPct: 20 };

/** ARSDK flying states in which the airframe is in the air and able to land. */
const AIRBORNE_FLYING_STATES = new Set([1, 2, 3, 6]);

function loadFailsafe(): BatteryFailsafe {
  try {
    const raw = window.localStorage.getItem(FAILSAFE_KEY);
    if (!raw) return FAILSAFE_DEFAULT;
    const parsed = JSON.parse(raw) as Partial<BatteryFailsafe>;
    const threshold = Number(parsed.thresholdPct);
    return {
      enabled: typeof parsed.enabled === 'boolean' ? parsed.enabled : FAILSAFE_DEFAULT.enabled,
      thresholdPct: Number.isFinite(threshold)
        ? Math.min(40, Math.max(10, Math.round(threshold)))
        : FAILSAFE_DEFAULT.thresholdPct,
    };
  } catch {
    return FAILSAFE_DEFAULT;
  }
}

/**
 * `?countdown=10` opens the launch sequence without commanding a mission, so
 * the transition can be reviewed and captured. Same purpose as `?screen=`.
 */
function previewCountdown(): number {
  if (typeof window === 'undefined') return 0;
  const raw = new URLSearchParams(window.location.search).get('countdown');
  const parsed = raw === null ? 0 : Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? Math.min(parsed, 60) : 0;
}

/**
 * The station.
 *
 * Two tabs and nothing between them: pre-flight, where the aircraft is on the
 * ground and there is one decision to make, and the cockpit, where it is flying
 * and there is one to unmake. The launch sequence is the hinge — it holds the
 * screen for ten seconds and then hands over on its own.
 *
 * The dock that switches between them is mounted here, once, at the top centre
 * of the window, and does not move when the tab under it changes: an operator
 * going back and forth reaches for the same coordinate both times.
 *
 * The forensic library and the process logs open over either tab rather than
 * competing with them for a place in the architecture.
 */
export const App: React.FC = () => {
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [counting, setCounting] = useState(() => previewCountdown() > 0);
  const [countdownSeconds, setCountdownSeconds] = useState(() => previewCountdown() || 0);
  const [warming, setWarming] = useState(false);
  /**
   * A launch click is being handled. Covers the pending-edit save as well as
   * the spawn, so a double click on the dial starts one flight and leaves no
   * refusal message behind on the pre-flight screen.
   */
  const launching = useRef(false);
  const [evidenceToken, setEvidenceToken] = useState(0);
  const [selectedStamp, setSelectedStamp] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [benchStage, setBenchStage] = useState<number | null>(null);
  const [benchRun, setBenchRun] = useState(false);
  const [pendingStage, setPendingStage] = useState<number | null>(null);
  const [report, setReport] = useState<Finding[] | null>(null);
  const [reportDismissed, setReportDismissed] = useState(false);
  const [failsafe, setFailsafe] = useState<BatteryFailsafe>(loadFailsafe);
  const [failsafeTriggered, setFailsafeTriggered] = useState(false);
  const failsafeFiredRef = useRef(false);

  const bridge = useBridge();
  const { telemetry, track, stale, resetTrack } = useTelemetry();
  const link = useLink({
    connected: telemetry.connected,
    driverRunning: telemetry.driver_running,
  });
  const mission = useMissionRuntime();
  const params = useMissionParameters();
  const stream = useStreamHealth(true);
  const evidence = useEvidence(evidenceToken);
  // Fed the angle the telemetry bridge observed on the gimbal topic, so the
  // control follows the mission's own stage changes and not only the operator.
  const camera = useCameraTilt(telemetry.camera_tilt_deg);
  const copilot = useCopilot();
  const narration = useNarrationQueue(copilot);
  const voice = useVoiceLevel();

  const running = mission.state === 'running' || mission.state === 'arming';
  const over = mission.state === 'finished' || mission.state === 'faulted';
  const benchMode = Boolean(getPath(params.working ?? params.committed, 'no_fly'));
  /** The aircraft is committed: pre-flight is locked until it is back down. See `preflightLocked`. */
  const airborne = preflightLocked(mission.state, benchMode, benchStage);

  const committed = params.committed;
  const arrivalRadius = num(committed, 'rtl.arrival_radius_m', 0.2);
  const nadirTilt = num(committed, 'gimbal.nadir_tilt_deg', -69);
  const searchTilt = num(committed, 'gimbal.search_tilt_deg', -20);

  /**
   * The copilot narrates a flight, not a bench rehearsal.
   *
   * A stage run on the bench reports the same `[STEP N:` markers as the real
   * thing, and a voice announcing "decolagem autorizada" at a drone sitting
   * inert on a desk is the kind of thing that teaches an operator to stop
   * listening to it.
   *
   * `benchRun` outlives `benchStage` deliberately. The stage clears the moment
   * its process exits, which is also the moment the mission reads as finished
   * with a capture on disk — keying the copilot off the stage alone meant a
   * bench rehearsal ended by announcing a safe landing and reading out a full
   * forensic report. This flag marks the whole cycle and is cleared only by a
   * real launch or by finishing the mission.
   */
  const narrating = !benchRun;
  const landed = over && mission.exitCode === 0;

  useFlightNarration(
    narration,
    landed && narrating,
    narrating,
    mission.startedAt,
    num(committed, 'kinematics.target_altitude_m', Number.NaN)
  );

  // The report is drawn when the capture lands, not when it is read out: the
  // wording and the order are settled before the aircraft is back on the ground.
  useEffect(() => {
    if (!mission.latestCapture || benchRun) return;
    setReport(buildForensicReport());
    setReportDismissed(false);
  }, [mission.latestCapture, benchRun]);

  const { revealed: reportRevealed, closing: reportClosing } = useForensicNarration(
    narration,
    (landed || over) && narrating && !reportDismissed && Boolean(mission.latestCapture),
    report
  );

  // A new capture means the library on disk changed.
  useEffect(() => {
    if (!mission.latestCapture) return;
    setEvidenceToken((n) => n + 1);
    setSelectedStamp(
      mission.latestCapture.filename
        .replace(/^accident_(raw|inspected)_/, '')
        .replace(/\.(png|jpg)$/, '')
    );
  }, [mission.latestCapture]);

  // The mission ending is also a moment the library may have changed.
  useEffect(() => {
    if (mission.state === 'finished' || mission.state === 'faulted') {
      setEvidenceToken((n) => n + 1);
    }
  }, [mission.state]);

  /**
   * A bench routine finishing returns the bench to ready, and nothing else.
   *
   * The cockpit stays open, the parameters stay as they were, and the operator
   * can pick another stage immediately — which is the whole point of a bench.
   * Only `benchStage` clears, because that is the one thing that stopped being
   * true; `benchRun` stays set so the copilot does not narrate a rehearsal's
   * end as a safe landing.
   */
  useEffect(() => {
    if (benchStage !== null && (mission.state === 'finished' || mission.state === 'faulted')) {
      setBenchStage(null);
    }
  }, [benchStage, mission.state]);

  const launch = useCallback(async () => {
    if (launching.current) return;
    launching.current = true;
    try {
      // Commit any pending edits first: the mission reads mission_config.json,
      // so an unsaved slider would otherwise not be the flight that happens.
      if (params.dirty) await params.save();

      const doc = params.working ?? params.committed;
      // Ten seconds is the station's standard, and it is a real parameter rather
      // than a UI flourish: the same figure goes to `mission.py --countdown`, so
      // the overlay and the aircraft count the same window.
      const seconds = num(doc, 'kinematics.countdown_sec', 10);

      resetTrack();
      setLaunchError(null);
      setReport(null);
      setReportDismissed(false);
      setBenchRun(false);
      failsafeFiredRef.current = false;
      setFailsafeTriggered(false);
      setPendingStage(null);
      setCountdownSeconds(seconds);

      const result = await mission.launch({
        countdown: seconds,
        noFly: Boolean(getPath(doc, 'no_fly')),
        height: num(doc, 'kinematics.target_altitude_m', 1),
        velocity: num(doc, 'kinematics.forward_cruise_velocity', 0.2),
        rtlVelocity: num(doc, 'rtl.max_speed', 0.1),
        searchTimeout: num(doc, 'timeouts.search_timeout_sec', 30),
        hoverDuration: num(doc, 'kinematics.hover_duration_sec', 7),
        confidence: num(doc, 'vision.confidence_threshold', 0.5),
        arrivalRadius: num(doc, 'rtl.arrival_radius_m', 0.2),
        modelPath: String(getPath(doc, 'vision.model_path') ?? 'yolov8n.pt'),
        ip: String(getPath(doc, 'network.drone_ip') ?? '192.168.42.1'),
        detectionTopic: String(
          getPath(doc, 'network.detection_stream_topic') ?? '/bebop/camera/detections'
        ),
        paramsJson: JSON.stringify(doc ?? {}),
      });

      if (!result.success) {
        // Stay put. Throwing the operator into the cockpit hides the control they
        // were using and gives them no way back to the thing that failed.
        setLaunchError(result.error ?? result.message ?? 'o processo não subiu');
        return;
      }

      // The countdown is the window in which walking away costs nothing, and it
      // is also the window the mission spends warming YOLO and taking its ground
      // reference. Neither applies on the bench: the motors never spin, so there
      // is nothing to stand clear of and nothing to reconsider. The bench gets a
      // plain loading screen instead, long enough for the mission process to have
      // video and a detector by the time the cockpit opens.
      if (benchMode) {
        setWarming(true);
        return;
      }

      if (seconds >= 1) {
        setCounting(true);
      } else {
        setScreen('cockpit');
      }
    } finally {
      launching.current = false;
    }
  }, [benchMode, mission, params, resetTrack]);

  /** One routine on the bench: same process, motors inert, no countdown, no cockpit. */
  const runBenchStage = useCallback(
    async (stage: number) => {
      if (!bridge) return;
      const doc = params.working ?? params.committed;

      // Only one mission process may exist, so switching routines means ending
      // the one that is up. Safe by construction here: everything the bench
      // runs is `--no-fly`, so there is nothing in the air to interrupt.
      if (mission.state === 'running' || mission.state === 'arming') {
        copilot.cancel();
        await bridge.endMission().catch(() => undefined);
      }

      setLaunchError(null);
      setBenchStage(stage);
      setBenchRun(true);
      setPendingStage(null);
      resetTrack();
      // The runtime is told a process is starting; without this the strip would
      // still be showing the previous routine's terminal state.
      mission.beginBenchStage();

      const result = await bridge.startBenchStage({
        stage,
        modelPath: String(getPath(doc, 'vision.model_path') ?? 'yolov8n.pt'),
        ip: String(getPath(doc, 'network.drone_ip') ?? '192.168.42.1'),
        detectionTopic: String(
          getPath(doc, 'network.detection_stream_topic') ?? '/bebop/camera/detections'
        ),
        paramsJson: JSON.stringify(doc ?? {}),
      });

      if (!result.success) {
        setBenchStage(null);
        setLaunchError(result.error ?? result.message ?? 'a rotina de bancada não subiu');
        return;
      }
      // The rehearsal's own feed is the point of watching it.
      setScreen('cockpit');
    },
    [bridge, copilot, mission, params.working, params.committed, resetTrack]
  );

  /**
   * Redirect the running mission to a different stage.
   *
   * The request goes out on the mission's control topic and is marked pending
   * until the mission itself reports the new stage: the step that is running
   * has to unwind first, and a strip that lit the new stage the instant the
   * button was pressed would be claiming a manoeuvre the aircraft had not
   * started. The mission may also refuse — it will not fly stage 1 at an
   * airframe already in the air — and a refusal simply means the request never
   * lands, which is what the pending marker clearing on its own looks like.
   */
  const gotoStage = useCallback(
    async (stage: number) => {
      if (!bridge) return;
      setPendingStage(stage);
      const result = await bridge.gotoStage(stage).catch(() => null);
      if (!result?.success) {
        setPendingStage(null);
        setLaunchError(result?.error ?? 'não foi possível comandar o salto de etapa');
      }
    },
    [bridge]
  );

  // The mission reporting the stage is what resolves the request.
  useEffect(() => {
    if (pendingStage !== null && mission.stage === pendingStage) setPendingStage(null);
  }, [mission.stage, pendingStage]);

  // A request the mission silently refused must not sit on the strip forever.
  useEffect(() => {
    if (pendingStage === null) return;
    const id = window.setTimeout(() => setPendingStage(null), 20000);
    return () => window.clearTimeout(id);
  }, [pendingStage]);

  const finishCountdown = useCallback(() => {
    setCounting(false);
    setScreen('cockpit');
  }, []);

  const finishWarmup = useCallback(() => {
    setWarming(false);
    setScreen('cockpit');
  }, []);

  // A bench mission that exits during the warm-up — a missing camera, a bad
  // parameter — is shown at once rather than after the remaining seconds of a
  // loading screen for a process that is no longer there.
  useEffect(() => {
    if (warming && (mission.state === 'finished' || mission.state === 'faulted')) finishWarmup();
  }, [warming, mission.state, finishWarmup]);

  const cancelCountdown = useCallback(async () => {
    setCounting(false);
    await mission.abort();
  }, [mission]);

  const abort = useCallback(async () => {
    await mission.abort();
  }, [mission]);

  /**
   * Land now, with or without a mission process.
   *
   * With a mission up this is the abort — the process is stopped and the land
   * burst published. Without one (an airframe flown by hand, or already
   * orphaned) the runtime's state is left alone and only the host's landing
   * path runs, so the cockpit does not announce the end of a mission that was
   * never running.
   */
  const land = useCallback(async () => {
    if (mission.state === 'running' || mission.state === 'arming') {
      await mission.abort();
    } else if (bridge) {
      await bridge.abortMission();
    }
  }, [bridge, mission]);

  const changeFailsafe = useCallback((next: BatteryFailsafe) => {
    setFailsafe(next);
    try {
      window.localStorage.setItem(FAILSAFE_KEY, JSON.stringify(next));
    } catch {
      /* private mode: the setting lives for the session only */
    }
  }, []);

  /**
   * Battery failsafe: land on its own at the configured charge.
   *
   * Armed only for a real flight — a mission that is up with the motors live,
   * or an airframe the aircraft itself reports as in the air. A bench run
   * never lands anything, and a charge the aircraft has not reported is not a
   * reading to act on. It fires once per flight: the landing it commands is
   * the same path as the abort button, and repeating it every telemetry frame
   * would only restart that path while the aircraft is already coming down.
   */
  const inFlight =
    ((running && !benchMode && benchStage === null) ||
      AIRBORNE_FLYING_STATES.has(telemetry.flying_state ?? -1)) &&
    Boolean(telemetry.connected);

  useEffect(() => {
    if (!failsafe.enabled || !inFlight || failsafeFiredRef.current) return;
    if (!telemetry.battery_known) return;
    if (telemetry.battery_pct > failsafe.thresholdPct) return;

    failsafeFiredRef.current = true;
    setFailsafeTriggered(true);
    copilot.cancel();
    void copilot.say(
      'Atenção: Nível crítico de bateria atingido. Pouso de emergência iniciado.',
      'URGENT'
    );
    void land();
  }, [
    failsafe.enabled,
    failsafe.thresholdPct,
    inFlight,
    telemetry.battery_known,
    telemetry.battery_pct,
    copilot,
    land,
  ]);

  /**
   * Close the cycle.
   *
   * Everything this flight produced in memory goes: the trail on the map, the
   * capture and the report drawn from it, the launch error, the stage counter,
   * the bench selection and the gimbal reading. The host does the same on its
   * side — `endMission` stops the mission process, silences the copilot and
   * drops the gimbal bridge — so what the operator returns to is pre-flight as
   * it was before the aircraft left, ready for another run immediately.
   *
   * Safe to call twice. Nothing here depends on what state it is starting from.
   */
  const finishMission = useCallback(async () => {
    copilot.cancel();

    if (bridge) {
      // Unconditional, not only while a mission is up: the handler is
      // idempotent and it is also what tears down the copilot and the gimbal.
      await bridge.endMission().catch(() => undefined);
    }

    mission.reset();
    resetTrack();
    camera.reset();
    setBenchStage(null);
    setBenchRun(false);
    setPendingStage(null);
    setReport(null);
    setReportDismissed(false);
    setLaunchError(null);
    setCounting(false);
    failsafeFiredRef.current = false;
    setFailsafeTriggered(false);
    setOverlay('none');
    setSelectedStamp(null);
    setEvidenceToken((n) => n + 1);
    setScreen('preflight');
  }, [bridge, camera, copilot, mission, resetTrack]);

  // The host tearing a mission down out from under the interface clears the
  // state that belonged to the processes it stopped. The rest of the reset is
  // the operator's decision, made by pressing the button, not ours to take on
  // their behalf because a child exited.
  useEffect(() => {
    if (!bridge) return;
    return bridge.onMissionReset(() => {
      camera.reset();
      setBenchStage(null);
    });
  }, [bridge, camera]);

  const body = useMemo(() => {
    if (screen === 'cockpit') {
      return (
        <CockpitScreen
          telemetry={telemetry}
          track={track}
          stale={stale}
          missionState={mission.state}
          stage={mission.stage}
          stageName={mission.stageName}
          exitCode={mission.exitCode}
          streamFps={Math.round(stream.fps)}
          streamBridgeUp={stream.running}
          streamLive={stream.live}
          streamWidth={stream.width}
          streamHeight={stream.height}
          streamSource={stream.source}
          captureFlash={mission.captureFlash}
          captureCount={mission.captureCount}
          latestCapture={mission.latestCapture}
          arrivalRadius={arrivalRadius}
          nadirTilt={nadirTilt}
          searchTilt={searchTilt}
          // A flight that faulted produced no assessment. The capture still
          // rises and holds — it is evidence either way — but there is no
          // report under it to reveal.
          landed={(landed || over) && narrating}
          benchMode={benchMode}
          benchStage={benchStage}
          onRunStage={(stage) => void runBenchStage(stage)}
          onGotoStage={(stage) => void gotoStage(stage)}
          pendingStage={pendingStage}
          voice={voice.level}
          onVolume={voice.setVolume}
          onToggleMute={voice.toggleMute}
          cameraTilt={camera.tilt}
          cameraAvailable={camera.available}
          onCameraTilt={camera.set}
          report={(landed || over) && narrating ? report : null}
          reportRevealed={reportRevealed}
          reportClosing={reportClosing}
          onAbort={() => void abort()}
          onOpenEvidence={() => setOverlay('evidence')}
          onFinish={() => void finishMission()}
        />
      );
    }

    return (
      <PreflightScreen
        telemetry={telemetry}
        stale={stale}
        networks={link.networks}
        currentSsid={link.currentSsid}
        scanning={link.scanning}
        linkPhase={link.phase}
        linkMessage={link.message}
        linkProgress={link.progress}
        readiness={link.readiness}
        flightReady={link.flightReady}
        driverRunning={link.driverRunning}
        onScan={() => void link.scan(true)}
        onConnect={(ssid) => void link.connect(ssid)}
        onStartDriver={() => void link.startDriver()}
        onStopDriver={() => void link.stopDriver()}
        params={params.working}
        paramsStatus={params.status}
        paramsError={params.error}
        changedPaths={params.changedPaths}
        dirty={params.dirty}
        hasPreset={Boolean(params.preset)}
        onEdit={params.edit}
        onSave={() => void params.save()}
        onDiscard={params.discard}
        onSavePreset={params.saveAsPreset}
        onApplyPreset={params.applyPreset}
        failsafe={failsafe}
        onFailsafeChange={changeFailsafe}
        failsafeTriggered={failsafeTriggered}
        voice={voice.level}
        onVolume={voice.setVolume}
        onToggleMute={voice.toggleMute}
        launchError={launchError}
        onLaunch={() => void launch()}
        onOpenDiagnostics={() => setOverlay('diagnostics')}
      />
    );
  }, [
    screen,
    telemetry,
    stale,
    stream,
    link,
    params,
    launch,
    launchError,
    track,
    mission,
    arrivalRadius,
    nadirTilt,
    searchTilt,
    abort,
    finishMission,
    report,
    landed,
    narrating,
    reportRevealed,
    reportClosing,
    reportDismissed,
    benchStage,
    benchMode,
    camera,
    runBenchStage,
    gotoStage,
    pendingStage,
    voice,
    failsafe,
    changeFailsafe,
    failsafeTriggered,
  ]);

  return (
    <div className="relative h-full w-full bg-abyss">
      {body}

      {/* The dock. One mount, one coordinate, both tabs. */}
      <TabSwitch
        screen={screen}
        onNavigate={setScreen}
        live={running}
        locked={airborne}
        className="fixed left-1/2 top-4 z-40 -translate-x-1/2"
      />

      {warming ? <BenchWarmupOverlay onDone={finishWarmup} /> : null}

      {counting ? (
        <CountdownOverlay
          seconds={countdownSeconds}
          stageReached={mission.stage >= 1}
          linkReady={link.flightReady}
          onDone={finishCountdown}
          onCancel={() => void cancelCountdown()}
        />
      ) : null}

      {overlay !== 'none' ? (
        <div className="fixed inset-0 z-[60] flex flex-col bg-abyss/92 backdrop-blur-sm">
          <header className="flex h-12 shrink-0 items-center justify-between border-b border-strut-soft px-4">
            <h2 className="font-cond text-base tracking-wide text-frost">
              {overlay === 'evidence' ? 'Dossiê pericial' : 'Diagnóstico · Terminal'}
            </h2>
            <button
              type="button"
              onClick={() => setOverlay('none')}
              aria-label="Fechar"
              className="rounded-bezel p-1.5 text-haze transition-colors hover:bg-hull-raise hover:text-frost"
            >
              <X size={16} />
            </button>
          </header>
          <div className="min-h-0 flex-1">
            {overlay === 'evidence' ? (
              <EvidenceScreen
                items={evidence.items}
                status={evidence.status}
                error={evidence.error}
                onReload={() => void evidence.reload()}
                selectedStamp={selectedStamp}
                onSelect={setSelectedStamp}
              />
            ) : (
              <DiagnosticsScreen
                missionLog={mission.missionLog}
                driverLog={mission.driverLog}
                onClearLogs={mission.clearLogs}
                missionRunning={running}
                onLand={land}
                onClose={() => setOverlay('none')}
              />
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default App;
