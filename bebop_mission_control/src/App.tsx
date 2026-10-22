import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Screen } from './types/mission';
import type { BatteryFailsafe } from './types/bmg';
import {
  RTL_ACK_TIMEOUT_MS,
  RTL_STAGE,
  clampThreshold,
  failsafeAction,
  launchParamsJson,
} from './lib/batteryFailsafe';
import { PreflightScreen } from './components/preflight/PreflightScreen';
import { CountdownOverlay } from './components/preflight/CountdownOverlay';
import { CockpitScreen } from './components/cockpit/CockpitScreen';
import { DiagnosticsScreen } from './components/diagnostics/DiagnosticsScreen';
import { DiagnosticsOverlayHeader } from './components/diagnostics/DiagnosticsOverlayHeader';
import { TabSwitch } from './components/shell/TabSwitch';
import { useBridge } from './hooks/useBridge';
import { useTelemetry } from './hooks/useTelemetry';
import { useLink } from './hooks/useLink';
import { useMissionRuntime } from './hooks/useMissionRuntime';
import { useMissionParameters } from './hooks/useMissionParameters';
import { preflightLocked } from './lib/navigationLock';
import { isAirborne } from './lib/flightState';
import { useStreamHealth } from './hooks/useStreamHealth';
import { useCameraTilt } from './hooks/useCameraTilt';
import {
  useCopilot,
  useFlightNarration,
  useForensicNarration,
  useNarrationQueue,
} from './hooks/useCopilot';
import { useVoiceLevel } from './hooks/useVoiceLevel';
import { useInspectionAssessment } from './hooks/useInspectionAssessment';
import { useForensicReport } from './hooks/useForensicReport';
import { LAUNCH_KEYS, planSentence, reserveLaunchPhrase } from './lib/copilotPhrases';
import { deepEqual, getPath } from './lib/paths';
import { readMissionMode, readRtlEnabled, type MissionMode } from './lib/missionMode';
import { launchBlockers, launchCountdownSec, reconcileCoupledPaths } from './lib/launchDocument';
import { blockingConflicts } from './lib/parameterConflicts';
import { isMissionOver } from './lib/missionOutcome';
import { useFinishLock } from './hooks/useFinishLock';
import { reportMayStart } from './lib/finishLock';
import { useGuardedAsync } from './hooks/useGuardedAsync';
import { useMissionCountdown } from './hooks/useMissionCountdown';
import { useModeEcho, type LaunchPlan } from './hooks/useModeEcho';
import { launchesFromGround } from './lib/altitudeEnvelope';
import { useLandProgress } from './hooks/useLandProgress';
import { runCriticalBatteryReturn } from './lib/batteryReturn';

/** The station is two tabs; these open over whichever one is current. */
type Overlay = 'none' | 'diagnostics';

/** `?screen=cockpit` opens a tab directly, for screenshots and tests. */
function initialScreen(): Screen {
  if (typeof window === 'undefined') return 'preflight';
  const requested = new URLSearchParams(window.location.search).get('screen');
  return requested === 'cockpit' ? 'cockpit' : 'preflight';
}

const FAILSAFE_KEY = 'bmg.battery-failsafe.v1';
const FAILSAFE_DEFAULT: BatteryFailsafe = { enabled: true, thresholdPct: 20 };


function loadFailsafe(): BatteryFailsafe {
  try {
    const raw = window.localStorage.getItem(FAILSAFE_KEY);
    if (!raw) return FAILSAFE_DEFAULT;
    const parsed = JSON.parse(raw) as Partial<BatteryFailsafe>;
    return {
      enabled: typeof parsed.enabled === 'boolean' ? parsed.enabled : FAILSAFE_DEFAULT.enabled,
      thresholdPct: clampThreshold(parsed.thresholdPct) ?? FAILSAFE_DEFAULT.thresholdPct,
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
  /** Click instant of the launch being counted down, epoch ms. */
  const [launchAt, setLaunchAt] = useState<number | null>(null);
  /**
   * A launch click is being handled. Covers the pending-edit save as well as
   * the spawn, so a double click on the dial starts one flight and leaves no
   * refusal message behind on the pre-flight screen.
   */
  const launching = useRef(false);
  /**
   * A launch or bench click is being handled, from the click to the answer of
   * the host. The mission mode selector is frozen from that click: the
   * document it sends is the one read at the click.
   */
  const [launchPending, setLaunchPending] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  /** Profile of the launch in progress, as read from the document at the click. */
  const [launchedMode, setLaunchedMode] = useState<MissionMode | null>(null);
  /** Return plan of the launch in progress (`rtl.enabled`), read with the mode. */
  const [launchedRtl, setLaunchedRtl] = useState<boolean | null>(null);
  /** Launch surface of the launch in progress, read with the mode. */
  const [launchedSurface, setLaunchedSurface] = useState<'platform' | 'ground' | null>(null);
  const [benchStage, setBenchStage] = useState<number | null>(null);
  const [pendingStage, setPendingStage] = useState<number | null>(null);
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
  // Fed the angle the telemetry bridge observed on the gimbal topic, so the
  // control follows the mission's own stage changes and not only the operator.
  const camera = useCameraTilt(telemetry.camera_tilt_deg);
  const copilot = useCopilot();
  const narration = useNarrationQueue(copilot);
  const missionCountdown = useMissionCountdown();
  const { phase: landProgress } = useLandProgress();
  const voice = useVoiceLevel();

  const running = mission.state === 'running' || mission.state === 'arming';
  const over = isMissionOver(mission.state);
  const benchMode = Boolean(getPath(params.working ?? params.committed, 'no_fly'));
  /** The aircraft is committed: pre-flight is locked until it is back down. See `preflightLocked`. */
  const airborne = preflightLocked(mission.state, mission.ran);
  const modeLocked = launchPending || counting || running || params.status === 'saving';

  const committed = params.committed;
  const storedRadius = getPath(committed, 'rtl.arrival_radius_m');
  const arrivalRadius =
    typeof storedRadius === 'number' && Number.isFinite(storedRadius) ? storedRadius : null;

  /**
   * The copilot narrates every run the same way: a real flight, a bench
   * mission and a single bench routine alike, touchdown call and forensic
   * report included, as the operator asked for the bench to rehearse exactly
   * what the audience will hear.
   */
  const landed = over && mission.exitCode === 0;

  useFlightNarration(narration, true, mission.startedAt);

  useEffect(() => {
    if (mission.latestCapture) setReportDismissed(false);
  }, [mission.latestCapture]);


  /**
   * A bench routine finishing returns the bench to ready, and nothing else.
   *
   * The cockpit stays open, the parameters stay as they were, and the operator
   * can pick another stage immediately — which is the whole point of a bench.
   * Only `benchStage` clears, because that is the one thing that stopped being
   * true.
   */
  useEffect(() => {
    if (benchStage !== null && isMissionOver(mission.state)) {
      setBenchStage(null);
    }
  }, [benchStage, mission.state]);

  /**
   * Draw the two launch lines and have them synthesized now.
   *
   * mission.start fires when the process spawns and mission.countdown_3
   * three seconds before liftoff; either synthesized on demand opened with
   * about two seconds of silence even on a warm session. Done when the
   * station comes to rest and again at the click, it leaves both ready.
   */
  const prepareSpeech = copilot.prepare;
  const primeLaunchPhrases = useCallback(() => {
    for (const key of LAUNCH_KEYS) prepareSpeech(reserveLaunchPhrase(key));
  }, [prepareSpeech]);

  useEffect(() => {
    if (mission.state === 'idle') primeLaunchPhrases();
  }, [mission.state, primeLaunchPhrases]);

  /**
   * The document a launch or a bench routine is about to send, committed.
   *
   * Pending edits are saved first, because the mission merges what it is sent
   * over `mission_config.json`; a save that fails, a document that is not
   * ready, or one missing a required number stops the launch here with the
   * reason on screen. Returns `null` in every one of those cases.
   */
  const commitLaunchDocument = useCallback(async (): Promise<Record<string, unknown> | null> => {
    if (params.status !== 'ready') {
      setLaunchError(
        params.status === 'error'
          ? 'Parâmetros indisponíveis; lançamento bloqueado.'
          : 'Parâmetros ainda não carregados; lançamento bloqueado.'
      );
      return null;
    }
    const current = params.working ?? params.committed;
    // The working copy is coupled on load; a document that still differs from
    // its coupled form is one the store has not been given yet.
    const doc = current ? reconcileCoupledPaths(current) : current;
    // An inexecutable combination is refused before anything is saved, with
    // the conflicts named rather than the bare paths; the hook's own save
    // refuses it as well.
    const conflicts = blockingConflicts(doc);
    if (conflicts.length) {
      setLaunchError(
        `Combinação de parâmetros inexecutável: ${conflicts.map((c) => c.title).join('; ')}. Ajuste na ficha de parâmetros.`
      );
      return null;
    }
    const uncommitted = params.dirty || (doc !== null && !deepEqual(doc, params.committed));
    if (uncommitted && !(await params.save())) {
      setLaunchError('Não foi possível salvar os parâmetros; lançamento abortado.');
      return null;
    }
    const missing = launchBlockers(doc);
    if (!doc || missing.length) {
      setLaunchError(`Parâmetros ausentes ou fora da faixa permitida: ${missing.join(', ')}.`);
      return null;
    }
    return doc;
  }, [params]);

  const launch = useCallback(async () => {
    // The dial keeps the keyboard focus under the countdown overlay. A second
    // activation during a launch reached the runtime's "already running"
    // refusal, which closed the overlay of the countdown still in progress.
    if (launching.current || preflightLocked(mission.state, mission.ran)) return;
    launching.current = true;
    setLaunchPending(true);
    try {
      // The click instant. The mission takes off at it plus the countdown
      // (engine/launch.py) and the overlay counts towards the same instant, so
      // the countdown starts here and not when the process answers: measured,
      // that was 40 s later.
      const clickedAt = Date.now();
      primeLaunchPhrases();
      const doc = await commitLaunchDocument();
      if (!doc) return;
      setLaunchedMode(readMissionMode(doc));
      setLaunchedRtl(readRtlEnabled(doc));
      setLaunchedSurface(launchesFromGround(doc) ? 'ground' : 'platform');
      // A real parameter rather than a UI flourish: the mission reads the same
      // `kinematics.countdown_sec` from the document, so the overlay and the
      // aircraft count the same window. Zero means no countdown.
      const seconds = launchCountdownSec(doc) ?? 0;

      resetTrack();
      setLaunchError(null);
      setReportDismissed(false);
      failsafeFiredRef.current = false;
      setFailsafeTriggered(false);
      setPendingStage(null);
      setCountdownSeconds(seconds);
      setLaunchAt(clickedAt);
      // The countdown is the window in which walking away costs nothing; the
      // mission runs its flat trim and ground reference inside it. The bench
      // runs the same one, checklist and abort included.
      if (seconds >= 1) setCounting(true);

      // The plan is part of what the operator commits to at the click: said
      // with the countdown, from the document sent (section 5.7).
      const planLine = planSentence({ mode: readMissionMode(doc), rtlEnabled: readRtlEnabled(doc) });
      prepareSpeech(planLine);
      narration.enqueue('plan', () => planLine);

      // The document is the only source of the flight parameters (5.1): no
      // per-field option, so no fallback can stand in for a value the
      // operator never set.
      const result = await mission.launch({
        countdown: seconds,
        noFly: Boolean(getPath(doc, 'no_fly')),
        launchAtMs: clickedAt,
        paramsJson: launchParamsJson(doc, failsafe.thresholdPct),
      });

      if (!result.success) {
        // Stay put. Throwing the operator into the cockpit hides the control they
        // were using and gives them no way back to the thing that failed.
        setCounting(false);
        setLaunchError(result.error ?? result.message ?? 'o processo não subiu');
        return;
      }

      if (seconds < 1) setScreen('cockpit');
    } finally {
      launching.current = false;
      setLaunchPending(false);
    }
  }, [commitLaunchDocument, failsafe.thresholdPct, mission, narration, params, prepareSpeech, primeLaunchPhrases, resetTrack]);

  /**
   * One routine on the bench: same process, motors inert, and the same
   * countdown as a launch. The host spawns the routine when the countdown ends
   * (`bmg:start-bench-stage`), so a later stage does not start behind it.
   */
  const runBenchStage = useCallback(
    async (stage: number) => {
      if (!bridge) return;
      const clickedAt = Date.now();
      primeLaunchPhrases();
      setLaunchPending(true);
      try {
        const doc = await commitLaunchDocument();
        if (!doc) return;

        // Only one mission process may exist, so switching routines means ending
        // the one that is up. Safe by construction here: everything the bench
        // runs is `--no-fly`, so there is nothing in the air to interrupt.
        if (mission.state === 'running' || mission.state === 'arming') {
          narration.silence();
          await bridge.endMission().catch(() => undefined);
        }

        const seconds = launchCountdownSec(doc) ?? 0;
        setLaunchError(null);
        setBenchStage(stage);
        setPendingStage(null);
        resetTrack();
        // The runtime is told a process is starting; without this the strip would
        // still be showing the previous routine's terminal state.
        mission.beginBenchStage();

        const result = await bridge.startBenchStage({
          stage,
          paramsJson: launchParamsJson(doc, failsafe.thresholdPct),
          countdown: seconds,
        });

        if (!result.success) {
          mission.refuseBenchStage();
          setBenchStage(null);
          setLaunchError(result.error ?? result.message ?? 'a rotina de bancada não subiu');
          return;
        }
        if (seconds >= 1) {
          setCountdownSeconds(seconds);
          setLaunchAt(clickedAt);
          setCounting(true);
        } else {
          // The rehearsal's own feed is the point of watching it.
          setScreen('cockpit');
        }
      } finally {
        setLaunchPending(false);
      }
    },
    [bridge, commitLaunchDocument, narration, failsafe.thresholdPct, mission, primeLaunchPhrases, resetTrack]
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

  // A mission that exits during the countdown — a missing camera, a refused
  // ground calibration — is shown at once rather than after the remaining
  // seconds of a countdown for a process that is no longer there.
  useEffect(() => {
    if (counting && isMissionOver(mission.state)) finishCountdown();
  }, [counting, mission.state, finishCountdown]);

  // A launch document the mission refused (exit 78): nothing was commanded, so
  // the countdown is cancelled without opening the cockpit, and the operator
  // stays on pre-flight with the reason and the refused field marked.
  useEffect(() => {
    if (!mission.refusal) return;
    setCounting(false);
    setScreen('preflight');
    setLaunchError(`Lançamento recusado: ${mission.refusal.reason}`);
  }, [mission.refusal]);

  const cancelCountdown = useCallback(async () => {
    setCounting(false);
    await mission.abort();
  }, [mission]);

  const launchedPlan = useMemo<LaunchPlan | null>(
    () =>
      launchedRtl === null || launchedSurface === null
        ? null
        : { rtlEnabled: launchedRtl, takeoffSurface: launchedSurface },
    [launchedRtl, launchedSurface]
  );

  useModeEcho({
    requested: launchedMode,
    plan: launchedPlan,
    onMismatch: (reported, requested) => {
      setLaunchError(
        `A missão iniciou no modo "${reported}", mas o modo "${requested}" foi selecionado. Missão abortada antes da decolagem.`
      );
      void cancelCountdown();
    },
  });

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
      isAirborne(telemetry.flying_state)) &&
    Boolean(telemetry.connected);

  // Read inside the return watchdog, which outlives the render that armed it.
  const stageRef = useRef(mission.stage);
  stageRef.current = mission.stage;
  const landRef = useRef(land);
  landRef.current = land;
  const rtlWatchdogRef = useRef<number | null>(null);

  const clearRtlWatchdog = useCallback(() => {
    if (rtlWatchdogRef.current !== null) window.clearTimeout(rtlWatchdogRef.current);
    rtlWatchdogRef.current = null;
  }, []);

  useEffect(() => clearRtlWatchdog, [clearRtlWatchdog]);

  /**
   * Bring the aircraft home on a critical charge (`lib/batteryFailsafe.ts`).
   *
   * The return is asked for, not assumed: a jump the mission refuses or never
   * reports within {@link RTL_ACK_TIMEOUT_MS} lands the aircraft where it is,
   * the landing this failsafe always made before it could fly a return.
   */
  const returnOnCriticalBattery = useCallback(async () => {
    const planned = failsafeAction(stageRef.current, running);
    const action = planned === 'rtl' && !bridge ? 'land' : planned;
    const taken = await runCriticalBatteryReturn(action, {
      say: (label, text) => narration.preempt(label, () => text, 'URGENT'),
      gotoStage: () => (bridge ? bridge.gotoStage(RTL_STAGE) : Promise.resolve({ success: false })),
      land: () => landRef.current(),
    },
    launchedMode ?? readMissionMode(params.working ?? params.committed),
    launchedRtl ?? readRtlEnabled(params.working ?? params.committed));
    if (taken !== 'rtl') return;
    clearRtlWatchdog();
    rtlWatchdogRef.current = window.setTimeout(() => {
      rtlWatchdogRef.current = null;
      if (stageRef.current !== RTL_STAGE) void landRef.current();
    }, RTL_ACK_TIMEOUT_MS);
  }, [bridge, clearRtlWatchdog, launchedMode, launchedRtl, narration, params.committed, params.working, running]);

  useEffect(() => {
    if (!failsafe.enabled || !inFlight || failsafeFiredRef.current) return;
    if (!telemetry.battery_known) return;
    if (telemetry.battery_pct > failsafe.thresholdPct) return;

    failsafeFiredRef.current = true;
    setFailsafeTriggered(true);
    void returnOnCriticalBattery();
  }, [
    failsafe.enabled,
    failsafe.thresholdPct,
    inFlight,
    telemetry.battery_known,
    telemetry.battery_pct,
    returnOnCriticalBattery,
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
  const endCycle = useCallback(async () => {
    narration.silence();

    if (bridge) {
      // Unconditional, not only while a mission is up: the handler is
      // idempotent and it is also what tears down the copilot and the gimbal.
      await bridge.endMission().catch(() => undefined);
    }

    mission.reset();
    resetTrack();
    camera.reset();
    setBenchStage(null);
    setPendingStage(null);
    setReportDismissed(false);
    setLaunchError(null);
    setLaunchedMode(null);
    setLaunchedRtl(null);
    setLaunchedSurface(null);
    setCounting(false);
    failsafeFiredRef.current = false;
    setFailsafeTriggered(false);
    setOverlay('none');
    setScreen('preflight');
  }, [bridge, camera, mission, narration, resetTrack]);

  /**
   * "Finalizar Missão", guarded: a double click ends the cycle once, and the
   * lock reads `finishing` until `endMission` has resolved.
   */
  const { run: finishMission, busy: finishing } = useGuardedAsync(endCycle);

  /** "Finalizar Missão": unlocked only on confirmed ground (lib/finishLock.ts). */
  const finishLock = useFinishLock({
    missionState: mission.state,
    exitCode: mission.exitCode,
    benchMode: benchMode || benchStage !== null,
    telemetry,
    finishing,
  });

  // The report is drawn when the victim assessment of the capture arrives,
  // normally during the return leg, so the wording and the order are settled
  // before the aircraft is back on the ground. The capture leaving (a new
  // launch, the end of the cycle) drops it.
  const grounded = reportMayStart(finishLock);
  const inspection = useInspectionAssessment(mission.latestCapture);
  const forensic = useForensicReport({
    capture: mission.latestCapture,
    assessment: inspection,
    grounded,
    prepare: prepareSpeech,
  });
  const report = forensic.script?.findings ?? null;

  // The report opens with "Aeronave em solo": it waits for confirmed ground.
  const { revealed: reportRevealed, closing: reportClosing } = useForensicNarration(
    narration,
    grounded && !reportDismissed && Boolean(mission.latestCapture),
    forensic.script
  );

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
          missionMode={readMissionMode(params.working ?? params.committed)}
          rtlEnabled={launchedRtl ?? readRtlEnabled(params.working ?? params.committed)}
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
          // A flight that faulted produced no assessment. The capture still
          // rises and holds — it is evidence either way — but there is no
          // report under it to reveal.
          landed={landed || over}
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
          report={landed || over ? report : null}
          reportAnalyzing={(landed || over) && forensic.analyzing}
          reportRevealed={reportRevealed}
          reportClosing={reportClosing}
          onAbort={() => void land()}
          onFinish={() => void finishMission()}
          finishLock={finishLock}
          landProgress={landProgress}
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
        paramDefaults={params.factory}
        paramsStatus={params.status}
        paramsError={params.error}
        changedPaths={params.changedPaths}
        dirty={params.dirty}
        presets={params.presets}
        activePresetId={params.activePresetId}
        onEdit={params.edit}
        onModeChange={(mode) => void params.switchMode(mode)}
        onSave={() => void params.save()}
        saveAttempted={params.saveAttempted}
        onDiscard={params.discard}
        onSavePreset={params.savePreset}
        onApplyPreset={(id) => void params.applyPreset(id)}
        onRenamePreset={params.renamePreset}
        onDeletePreset={params.deletePreset}
        failsafe={failsafe}
        onFailsafeChange={changeFailsafe}
        failsafeTriggered={failsafeTriggered}
        voice={voice.level}
        onVolume={voice.setVolume}
        onToggleMute={voice.toggleMute}
        launchError={launchError}
        refusedPath={mission.refusal?.field ?? null}
        altitudeNotice={params.altitudeNotice}
        onLaunch={() => void launch()}
        onOpenDiagnostics={() => setOverlay('diagnostics')}
        modeLocked={modeLocked}
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
    abort,
    finishMission,
    report,
    forensic.analyzing,
    landed,
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
    modeLocked,
    params.working,
    params.committed,
    launchedRtl,
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


      {counting ? (
        <CountdownOverlay
          seconds={countdownSeconds}
          remaining={missionCountdown.remaining}
          clearance={missionCountdown.clearance}
          trim={missionCountdown.trim}
          launchAt={launchAt}
          stageReached={mission.stage >= 1}
          linkReady={benchMode || benchStage !== null ? link.driverRunning : link.flightReady}
          onDone={finishCountdown}
          onCancel={() => void cancelCountdown()}
        />
      ) : null}

      {overlay !== 'none' ? (
        <div className="fixed inset-0 z-[60] flex flex-col bg-abyss/92 backdrop-blur-sm">
          <DiagnosticsOverlayHeader title="Diagnóstico · Terminal" onClose={() => setOverlay('none')} />
          <div className="min-h-0 flex-1">
            <DiagnosticsScreen
              missionRunning={running}
              onLand={land}
              onClose={() => setOverlay('none')}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default App;
