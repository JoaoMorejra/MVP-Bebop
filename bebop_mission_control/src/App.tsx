import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Screen } from './types/mission';
import type { BatteryFailsafe } from './types/bmg';
import {
  RTL_ACK_TIMEOUT_MS,
  RTL_STAGE,
  batteryTriggerArmed,
  clampThreshold,
  failsafeAction,
  launchParamsJson,
} from './lib/batteryFailsafe';
import { PreflightScreen } from './components/preflight/PreflightScreen';
import { CockpitScreen } from './components/cockpit/CockpitScreen';
import { TopBar } from './components/shell/TopBar';
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
  type LaunchSpeechEvent,
  useForensicNarration,
  useLinkLossNarration,
  useNarrationQueue,
} from './hooks/useCopilot';
import { useVoiceLevel } from './hooks/useVoiceLevel';
import { useInspectionAssessment } from './hooks/useInspectionAssessment';
import { useForensicReport } from './hooks/useForensicReport';
import { LAUNCH_KEYS, planSentence, reserveLaunchPhrase } from './lib/copilotPhrases';
import { deepEqual, getPath } from './lib/paths';
import { readMissionMode, readRtlEnabled, type MissionMode } from './lib/missionMode';
import { NO_FLY_PATH, readFlightMode, type FlightMode } from './lib/flightMode';
import { LAUNCH_COUNTDOWN_SEC, launchBlockers, launchCountdownSec, reconcileCoupledPaths } from './lib/launchDocument';
import { blockingConflicts } from './lib/parameterConflicts';
import { isMissionOver } from './lib/missionOutcome';
import { useFinishLock } from './hooks/useFinishLock';
import { reportMayStart } from './lib/finishLock';
import { useGuardedAsync } from './hooks/useGuardedAsync';
import { useMissionCountdown } from './hooks/useMissionCountdown';
import { useStageProgress } from './hooks/useStageProgress';
import { useModeEcho, type LaunchPlan } from './hooks/useModeEcho';
import { useLaunchSequence } from './hooks/useLaunchSequence';
import { useLaunchAbortShortcut } from './hooks/useLaunchAbortShortcut';
import { launchesFromGround } from './lib/altitudeEnvelope';
import { useLandProgress } from './hooks/useLandProgress';
import { useTouchdown } from './hooks/useTouchdown';
import { landOnCriticalBattery, runCriticalBatteryReturn, type BatteryReturnHooks } from './lib/batteryReturn';

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
 * The top bar is mounted here, once, above both tabs: the dock that switches
 * between them sits at its centre and does not move when the tab under it
 * changes, so an operator going back and forth reaches for the same
 * coordinate both times. Radio, driver, voice and charge live in its tray.
 */
export const App: React.FC = () => {
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const [counting, setCounting] = useState(() => previewCountdown() > 0);
  const [countdownSeconds, setCountdownSeconds] = useState(() => previewCountdown() || 0);
  /** Click instant of the launch being counted down, epoch ms. */
  const [launchAt, setLaunchAt] = useState<number | null>(() => (previewCountdown() > 0 ? Date.now() : null));
  /** The document of the launch being prepared was committed (the `params` checkpoint). */
  const [paramsCommitted, setParamsCommitted] = useState(false);
  /**
   * The mission process was asked for. Until then a runtime still reporting
   * the end of the previous flight is not the end of this one.
   */
  const [spawnRequested, setSpawnRequested] = useState(false);
  /** Set by a cancel; the launch in progress checks it after each wait. */
  const launchCancelledRef = useRef(false);
  /** The clearance line of the launch being prepared, as the copilot handled it (D3). */
  const [speech, setSpeech] = useState<{ startedAt: number | null; endedAt: number | null; heard: boolean; text: string | null }>(
    { startedAt: null, endedAt: null, heard: false, text: null }
  );
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
  /** The mission announced this flight's forced return (`mission.battery_return`). */
  const batteryReturnAckRef = useRef(false);

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
  // The strip's fill follows the flight from its first milestone, before the
  // cockpit opens: mounted here rather than in the cockpit.
  const stageFill = useStageProgress({
    stage: mission.stage,
    state: mission.state,
    altitude:
      Boolean(telemetry.nav_fresh ?? telemetry.data_fresh) && Number.isFinite(telemetry.altitude) ? telemetry.altitude : null,
  });
  const voice = useVoiceLevel();

  const running = mission.state === 'running' || mission.state === 'arming';
  const over = isMissionOver(mission.state);
  const benchMode = Boolean(getPath(params.working ?? params.committed, 'no_fly'));
  /** The aircraft is committed: pre-flight is locked until it is back down. See `preflightLocked`. */
  const airborne = preflightLocked(mission.state, mission.ran);
  const modeLocked = launchPending || counting || running || params.status === 'saving';
  const flightMode = readFlightMode(params.working ?? params.committed);

  /**
   * Pick a flight mode (D1). `test` only arms `no_fly`, leaving the profile
   * underneath as it is. A profile switches first and clears `no_fly` after:
   * `switchMode` persists the working document it closes over, so clearing
   * first would be overwritten by the stale `no_fly` it still holds.
   */
  const selectFlightMode = useCallback(
    async (next: FlightMode) => {
      if (modeLocked) return;
      const doc = params.working ?? params.committed;
      if (next === 'test') {
        params.edit(NO_FLY_PATH, true);
        return;
      }
      if (readMissionMode(doc) !== next) await params.switchMode(next);
      if (getPath(doc, NO_FLY_PATH) === true) params.edit(NO_FLY_PATH, false);
    },
    [modeLocked, params]
  );

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
  const touchdown = useTouchdown();
  /** The aircraft is down: the mission declared touchdown, or its run is over. */
  const touchedDown = touchdown || landed || over;

  const observeLaunchSpeech = useCallback((event: LaunchSpeechEvent) => {
    setSpeech((previous) =>
      event.phase === 'start'
        ? { startedAt: event.at, endedAt: null, heard: false, text: event.text }
        : { ...previous, endedAt: event.at, heard: event.heard === true, text: event.text }
    );
  }, []);
  useFlightNarration(narration, true, mission.startedAt, observeLaunchSpeech);
  useLinkLossNarration(narration, mission.state === 'running' && !benchMode && benchStage === null && stale);

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
    if (launching.current) return;
    if (isMissionOver(mission.state)) {
      mission.reset();
    } else if (preflightLocked(mission.state, mission.ran)) {
      return;
    }
    launching.current = true;
    launchCancelledRef.current = false;
    setLaunchPending(true);
    // The click instant. The mission takes off at it plus the countdown
    // (engine/launch.py) and the dial counts towards the same instant, so the
    // sequence starts here and not when the process answers: measured, that
    // was 40 s later.
    const clickedAt = Date.now();
    setCounting(true);
    setLaunchAt(clickedAt);
    setParamsCommitted(false);
    setSpawnRequested(false);
    setSpeech({ startedAt: null, endedAt: null, heard: false, text: null });
    setLaunchError(null);
    let spawned = false;
    try {
      primeLaunchPhrases();
      const doc = await commitLaunchDocument();
      if (launchCancelledRef.current || !doc) return;
      setParamsCommitted(true);
      setLaunchedMode(readMissionMode(doc));
      setLaunchedRtl(readRtlEnabled(doc));
      setLaunchedSurface(launchesFromGround(doc) ? 'ground' : 'platform');
      // No longer an operator parameter: the window is fixed at
      // LAUNCH_COUNTDOWN_SEC and pinned in the document, which the mission
      // reads as `kinematics.countdown_sec`, so the dial and the aircraft
      // count the same window.
      const seconds = launchCountdownSec(doc) ?? 0;

      resetTrack();
      setLaunchError(null);
      setReportDismissed(false);
      failsafeFiredRef.current = false;
      batteryReturnAckRef.current = false;
      setFailsafeTriggered(false);
      setPendingStage(null);
      setCountdownSeconds(seconds);

      // The plan is part of what the operator commits to at the click: said
      // with the countdown, from the document sent (section 5.7).
      const planLine = planSentence({ mode: readMissionMode(doc), rtlEnabled: readRtlEnabled(doc) });
      prepareSpeech(planLine);
      narration.enqueue('plan', () => planLine);

      // The document is the only source of the flight parameters (5.1): no
      // per-field option, so no fallback can stand in for a value the
      // operator never set.
      if (launchCancelledRef.current) return;
      spawned = true;
      setSpawnRequested(true);
      const result = await mission.launch({
        countdown: seconds,
        noFly: Boolean(getPath(doc, 'no_fly')),
        launchAtMs: clickedAt,
        paramsJson: launchParamsJson(doc, failsafe),
      });

      if (!result.success) {
        // Stay put. Throwing the operator into the cockpit hides the control they
        // were using and gives them no way back to the thing that failed.
        setCounting(false);
        setLaunchError(result.error ?? result.message ?? 'o processo não subiu');
        return;
      }

      if (seconds < 1) {
        setCounting(false);
        setScreen('cockpit');
      }
    } finally {
      // Nothing was spawned: a refused document, a cancel or a throw leaves
      // the dial split and idle again.
      if (!spawned) {
        setCounting(false);
        setParamsCommitted(false);
      }
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
          paramsJson: launchParamsJson(doc, failsafe),
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
          setParamsCommitted(true);
          setSpawnRequested(true);
          setSpeech({ startedAt: null, endedAt: null, heard: false, text: null });
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
    setParamsCommitted(false);
    setSpawnRequested(false);
    setScreen('cockpit');
  }, []);

  // A mission that exits during the countdown — a missing camera, a refused
  // ground calibration — is shown at once rather than after the remaining
  // seconds of a countdown for a process that is no longer there.
  useEffect(() => {
    if (counting && spawnRequested && isMissionOver(mission.state)) finishCountdown();
  }, [counting, spawnRequested, mission.state, finishCountdown]);

  // A launch document the mission refused (exit 78): nothing was commanded, so
  // the countdown is cancelled without opening the cockpit, and the operator
  // stays on pre-flight with the reason and the refused field marked.
  useEffect(() => {
    if (!mission.refusal) return;
    setCounting(false);
    setScreen('preflight');
    setLaunchError(`Lançamento recusado: ${mission.refusal.reason}`);
  }, [mission.refusal]);

  // Whenever the aircraft becomes airborne or reaches flight stages, ALWAYS transition
  // immediately to the cockpit so the operator never stays stuck on the preflight screen.
  useEffect(() => {
    const isFlyingOrAirborne =
      isAirborne(telemetry.flying_state) ||
      (typeof telemetry.altitude === 'number' && telemetry.altitude > 0.35) ||
      mission.stage >= 2;
    if (
      isFlyingOrAirborne &&
      (counting || screen === 'preflight') &&
      (mission.state === 'running' || mission.state === 'arming' || spawnRequested)
    ) {
      finishCountdown();
    }
  }, [telemetry.flying_state, telemetry.altitude, mission.stage, mission.state, counting, screen, spawnRequested, finishCountdown]);

  const cancelCountdown = useCallback(async () => {
    launchCancelledRef.current = true;
    launching.current = false;
    setLaunchPending(false);
    setCounting(false);
    setParamsCommitted(false);
    setSpawnRequested(false);
    await mission.abort();
    mission.reset();
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

  const sequence = useLaunchSequence({
    active: counting,
    clickedAt: launchAt,
    seconds: countdownSeconds || LAUNCH_COUNTDOWN_SEC,
    paramsCommitted,
    linkReady: benchMode || benchStage !== null ? link.driverRunning : link.flightReady,
    stageReached: mission.stage >= 1,
    countdown: missionCountdown,
    speech,
    speechText: speech.text,
    onDone: finishCountdown,
  });

  useLaunchAbortShortcut({
    armed: counting && screen === 'preflight',
    handoff: screen === 'cockpit',
    onAbort: () => void cancelCountdown(),
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

  const changeFailsafe = useCallback(
    (next: BatteryFailsafe) => {
      setFailsafe(next);
      try {
        window.localStorage.setItem(FAILSAFE_KEY, JSON.stringify(next));
      } catch {
        /* private mode: the setting lives for the session only */
      }
      // A running mission took the switch from the launch payload; a change
      // in flight reaches it on mission/battery_failsafe. A return already
      // started is not cancelled by switching off (the mission keeps it).
      if (running && bridge?.setBatteryFailsafe) {
        void bridge.setBatteryFailsafe(next.enabled, next.thresholdPct);
      }
    },
    [bridge, running],
  );

  /**
   * Battery failsafe: at the configured charge, bring the aircraft home.
   *
   * Armed only while the aircraft itself reports hovering or flying
   * (`batteryTriggerArmed`); the mission owns the climb and the descent. On the
   * ground and at spin-up nothing fires and nothing latches, the countdown
   * included: the pre-arm gate refuses the launch there, and a stage 5
   * requested before takeoff would be flown on the ground. A bench run never
   * leaves the ground,
   * and a charge the aircraft has not reported is not a reading to act on. It
   * fires once per flight: repeating it every telemetry frame would only
   * restart a return or a landing already under way.
   */
  const airborneNow = isAirborne(telemetry.flying_state);
  const inFlight = batteryTriggerArmed(telemetry.flying_state) && Boolean(telemetry.connected);

  // Read inside the return watchdog, which outlives the render that armed it.
  const stageRef = useRef(mission.stage);
  stageRef.current = mission.stage;
  const runningRef = useRef(running);
  runningRef.current = running;
  const airborneRef = useRef(airborneNow);
  airborneRef.current = airborneNow;
  const landRef = useRef(land);
  landRef.current = land;
  const rtlWatchdogRef = useRef<number | null>(null);

  useEffect(() => {
    if (!bridge?.onMilestone) return;
    return bridge.onMilestone((event) => {
      if (event.key === 'mission.start') batteryReturnAckRef.current = false;
      else if (event.key === 'mission.battery_return') batteryReturnAckRef.current = true;
    });
  }, [bridge]);

  const clearRtlWatchdog = useCallback(() => {
    if (rtlWatchdogRef.current !== null) window.clearTimeout(rtlWatchdogRef.current);
    rtlWatchdogRef.current = null;
  }, []);

  useEffect(() => clearRtlWatchdog, [clearRtlWatchdog]);

  /**
   * Bring the aircraft home on a critical charge (`lib/batteryFailsafe.ts`).
   *
   * The mission jumps to stage 5 on its own at the same charge; this request
   * is the redundancy, and a duplicate is refused there without effect. The
   * return counts as acknowledged when the mission reports stage 5 or the
   * `mission.battery_return` milestone, whichever comes first: a jump held
   * behind a blocking stage-4 capture announces itself before its
   * `[STEP 5: ...]`. Without either within {@link RTL_ACK_TIMEOUT_MS} the jump
   * is asked once more; the aircraft lands where it is only when the mission
   * is gone, or a request the host could not deliver, or the second request,
   * is followed by a window in silence. A mission found gone at a window's end
   * is landed even after acknowledging. Every landing here requires the
   * aircraft still in the air, and the station speaks only in that landing.
   */
  const returnOnCriticalBattery = useCallback(async () => {
    const acknowledged = () => stageRef.current === RTL_STAGE || batteryReturnAckRef.current;
    const armWindow = (onExpiry: () => void) => {
      clearRtlWatchdog();
      rtlWatchdogRef.current = window.setTimeout(() => {
        rtlWatchdogRef.current = null;
        onExpiry();
      }, RTL_ACK_TIMEOUT_MS);
    };
    const hooks: BatteryReturnHooks = {
      say: (label, text) => narration.preempt(label, () => text, 'URGENT'),
      gotoStage: () => (bridge ? bridge.gotoStage(RTL_STAGE) : Promise.resolve({ success: false })),
      land: () => landRef.current(),
      graceWindow: () =>
        runningRef.current
          ? new Promise<boolean>((resolve) => {
              armWindow(() => resolve(!airborneRef.current || (runningRef.current && acknowledged())));
            })
          : Promise.resolve(false),
    };
    const planned = running && acknowledged() ? 'none' : failsafeAction(stageRef.current, running);
    const action = planned === 'rtl' && !bridge ? 'land' : planned;
    const taken = await runCriticalBatteryReturn(action, hooks);
    if (taken !== 'rtl') return;

    const watch = (retry: boolean) =>
      armWindow(() => {
        if (!airborneRef.current) return;
        if (!runningRef.current) {
          void landOnCriticalBattery(hooks);
          return;
        }
        if (acknowledged()) return;
        if (!retry) {
          void landOnCriticalBattery(hooks);
          return;
        }
        void hooks
          .gotoStage()
          .catch(() => ({ success: false }))
          .then(() => watch(false));
      });
    watch(true);
  }, [bridge, clearRtlWatchdog, narration, running]);

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
    setParamsCommitted(false);
    setSpawnRequested(false);
    failsafeFiredRef.current = false;
    clearRtlWatchdog();
    batteryReturnAckRef.current = false;
    setFailsafeTriggered(false);
    setScreen('preflight');
  }, [bridge, camera, clearRtlWatchdog, mission, narration, resetTrack]);

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
  const {
    revealed: reportRevealed,
    announced: reportAnnounced,
    readings: reportReadings,
    closing: reportClosing,
  } = useForensicNarration(
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
          missionMode={readMissionMode(params.working ?? params.committed)}
          rtlEnabled={launchedRtl ?? readRtlEnabled(params.working ?? params.committed)}
          streamFps={Math.round(stream.fps)}
          streamBridgeUp={stream.running}
          streamLive={stream.live}
          streamWidth={stream.width}
          streamHeight={stream.height}
          captureFlash={mission.captureFlash}
          captureCount={mission.captureCount}
          latestCapture={mission.latestCapture}
          arrivalRadius={arrivalRadius}
          // A flight that faulted produced no assessment. The capture still
          // rises and holds — it is evidence either way — but there is no
          // report under it to reveal. The mission's own touchdown milestone
          // is the moment: it precedes the process exit by about a second.
          landed={touchedDown}
          benchMode={benchMode}
          benchStage={benchStage}
          benchCountdown={benchStage !== null && counting ? missionCountdown.remaining : null}
          onRunStage={(stage) => void runBenchStage(stage)}
          onGotoStage={(stage) => void gotoStage(stage)}
          pendingStage={pendingStage}
          cameraTilt={camera.tilt}
          cameraAvailable={camera.available}
          onCameraTilt={camera.set}
          report={touchedDown ? report : null}
          reportAnalyzing={touchedDown && forensic.analyzing}
          reportRevealed={reportRevealed}
          reportAnnounced={reportAnnounced}
          reportReadings={reportReadings}
          reportClosing={reportClosing}
          onAbort={() => void land()}
          onFinish={() => void finishMission()}
          finishLock={finishLock}
          landProgress={landProgress}
          stageFill={stageFill}
        />
      );
    }

    return (
      <PreflightScreen
        telemetry={telemetry}
        stale={stale}
        readiness={link.readiness}
        flightReady={link.flightReady}
        driverRunning={link.driverRunning}
        params={params.working}
        paramDefaults={params.factory}
        paramsStatus={params.status}
        paramsError={params.error}
        changedPaths={params.changedPaths}
        dirty={params.dirty}
        favoriteActive={Boolean(params.favorite) && params.activePresetId === params.favorite?.id}
        hasFavorite={Boolean(params.favorite)}
        onEdit={params.edit}
        flightMode={flightMode}
        onFlightModeChange={(mode) => void selectFlightMode(mode)}
        onSave={() => void params.save()}
        onDiscard={params.discard}
        onSaveFavorite={() => void params.saveFavorite()}
        onRestoreFavorite={() => void params.restoreFavorite()}
        batteryThresholdPct={failsafe.thresholdPct}
        batteryFailsafeEnabled={failsafe.enabled}
        launchError={launchError}
        refusedPath={mission.refusal?.field ?? null}
        altitudeNotice={params.altitudeNotice}
        onLaunch={() => void launch()}
        sequence={screen === 'preflight' ? sequence : null}
        onAbortLaunch={() => void cancelCountdown()}
        launchErrorShort="Falha ao iniciar"
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
    touchedDown,
    reportRevealed,
    reportAnnounced,
    reportReadings,
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
    flightMode,
    selectFlightMode,
    params.working,
    params.committed,
    launchedRtl,
    sequence,
    cancelCountdown,
    counting,
    missionCountdown.remaining,
    stageFill,
  ]);

  return (
    <div className="flex h-full w-full flex-col bg-abyss">
      <TopBar
        screen={screen}
        onNavigate={setScreen}
        live={running}
        locked={airborne}
        frozen={counting}
        telemetry={telemetry}
        networks={link.networks}
        currentSsid={link.currentSsid}
        scanning={link.scanning}
        linkPhase={link.phase}
        readiness={link.readiness}
        flightReady={link.flightReady}
        driverRunning={link.driverRunning}
        wifiPower={link.wifiPower}
        onToggleWifiPower={(enabled) => void link.setWifiPower(enabled)}
        onDisconnectWifi={() => void link.disconnectWifi()}
        onScan={() => void link.scan(true)}
        onConnect={(ssid, password) => void link.connect(ssid, password)}
        onStartDriver={() => void link.startDriver()}
        onStopDriver={() => void link.stopDriver()}
        failsafe={failsafe}
        onFailsafeChange={changeFailsafe}
        failsafeTriggered={failsafeTriggered}
        voice={voice.level}
        onVolume={voice.setVolume}
        onToggleMute={voice.toggleMute}
      />

      <div className="relative min-h-0 flex-1">{body}</div>


    </div>
  );
};

export default App;
