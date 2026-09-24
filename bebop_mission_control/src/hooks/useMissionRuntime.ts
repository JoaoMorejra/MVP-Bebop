import { useCallback, useEffect, useRef, useState } from 'react';
import type { LogLine, MissionLaunchOptions, RawEvidence } from '../types/bmg';
import type { MissionState } from '../types/mission';
import { cues } from '../audio/cues';
import { useBridge } from './useBridge';

const LOG_LIMIT = 4000;

function appendCapped(previous: LogLine[], incoming: LogLine[]): LogLine[] {
  const next = previous.concat(incoming);
  return next.length > LOG_LIMIT ? next.slice(next.length - LOG_LIMIT) : next;
}

/**
 * Owns the life of `mission.py`: launching it, reading its stdout, tracking
 * which of the five stages it reports, and bringing it down.
 *
 * Stage transitions arrive as `bmg:step-change`, which main.cjs derives from
 * the literal `[STEP N:` markers the Python steps log. Nothing here advances
 * the stage on a timer — if the mission does not report a stage, the UI does
 * not claim it.
 */
export function useMissionRuntime() {
  const bridge = useBridge();

  const [state, setState] = useState<MissionState>('idle');
  const [stage, setStage] = useState<number>(0);
  const [stageName, setStageName] = useState<string>('');
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [missionLog, setMissionLog] = useState<LogLine[]>([]);
  const [driverLog, setDriverLog] = useState<LogLine[]>([]);
  const [latestCapture, setLatestCapture] = useState<RawEvidence | null>(null);
  const [captureFlash, setCaptureFlash] = useState(false);
  const [captureCount, setCaptureCount] = useState(0);

  const stageRef = useRef(0);
  /** Whether the process reported its own exit for the current run. */
  const exitSeen = useRef(false);
  /**
   * Whether a mission process exists or is being spawned, known synchronously.
   *
   * `state` cannot answer that for a second launch arriving before React has
   * rendered the first one's `arming`. Such a launch used to be refused by the
   * main process ("Uma missão já está em andamento") and then set `faulted`
   * over the mission that *was* starting — releasing the pre-flight lock and
   * sounding the fault cue mid-countdown, until its first step marker
   * restored `running`.
   */
  const processUp = useRef(false);

  // Buffered history, so a screen mounted after launch still sees the start.
  useEffect(() => {
    if (!bridge) return;
    void bridge.getLogHistory().then(({ mission, driver }) => {
      setMissionLog((prev) => (prev.length ? prev : mission));
      setDriverLog((prev) => (prev.length ? prev : driver));
    });
    void bridge.getMissionStatus().then((status) => {
      if (status.running) {
        processUp.current = true;
        setState('running');
        setStartedAt(status.startedAt);
      }
    });
  }, [bridge]);

  useEffect(() => {
    if (!bridge) return;
    const off = [
      bridge.onMissionLog((line) => setMissionLog((prev) => appendCapped(prev, [line]))),
      bridge.onDriverLog((line) => setDriverLog((prev) => appendCapped(prev, [line]))),
      bridge.onStepChange((event) => {
        if (event.stepNumber === stageRef.current) return;
        stageRef.current = event.stepNumber;
        setStage(event.stepNumber);
        setStageName(event.stepName);
        setState((prev) => (prev === 'aborting' ? prev : 'running'));
        cues.play('phase');
      }),
      bridge.onRawEvidenceReady((evidence) => {
        setLatestCapture(evidence);
        setCaptureCount((n) => n + 1);
        setCaptureFlash(true);
        cues.play('shutter');
        window.setTimeout(() => setCaptureFlash(false), 450);
      }),
      bridge.onMissionExit((event) => {
        exitSeen.current = true;
        processUp.current = false;
        setExitCode(event.code);
        setState(event.code === 0 ? 'finished' : 'faulted');
        cues.play(event.code === 0 ? 'complete' : 'fault');
      }),
    ];
    return () => off.forEach((fn) => fn());
  }, [bridge]);

  const launch = useCallback(
    async (options: MissionLaunchOptions) => {
      if (processUp.current) {
        return { success: false, message: 'Uma missão já está em andamento.' };
      }
      processUp.current = true;
      setState('arming');
      setStage(0);
      stageRef.current = 0;
      setStageName('');
      setExitCode(null);
      exitSeen.current = false;
      setLatestCapture(null);
      setCaptureCount(0);
      setStartedAt(Date.now());

      if (!bridge) {
        // Browser session: there is no mission to run, so say so instead of
        // pretending one started.
        processUp.current = false;
        setState('faulted');
        setMissionLog((prev) =>
          appendCapped(prev, [
            {
              type: 'stderr',
              text: 'Sem ponte Electron. A missão só executa no aplicativo desktop.\n',
              at: Date.now(),
            },
          ])
        );
        return { success: false, error: 'bridge-unavailable' };
      }

      const result = await bridge.startMission(options);
      if (!result.success) {
        processUp.current = false;
        setState('faulted');
        cues.play('fault');
      } else {
        setState('running');
      }
      return result;
    },
    [bridge]
  );

  const abort = useCallback(async () => {
    setState('aborting');
    cues.play('abort');
    if (!bridge) {
      processUp.current = false;
      setState('idle');
      return;
    }
    await bridge.abortMission();
    // How the flight ended is the process's own report, not this call's.
    // Settling unconditionally here overwrote the `faulted` that
    // `onMissionExit` had just set for a mission killed mid-flight, so an
    // aborted run rendered in mint with the success banner while its exit code
    // said otherwise. Only settle when nothing reported an exit at all.
    if (!exitSeen.current) {
      processUp.current = false;
      setState('finished');
    }
  }, [bridge]);

  /**
   * Back to before the flight.
   *
   * The capture goes with the rest of it. It is on disk and in the library
   * either way, but leaving it in this hook meant the evidence of the last
   * flight was still mounted in a cockpit that had been reset for the next one
   * — the operator would return to find a finished mission's photograph
   * presented over an aircraft sitting on the ground.
   */
  const reset = useCallback(() => {
    processUp.current = false;
    setState('idle');
    setStage(0);
    stageRef.current = 0;
    setStageName('');
    setExitCode(null);
    exitSeen.current = false;
    setStartedAt(null);
    setLatestCapture(null);
    setCaptureCount(0);
    setCaptureFlash(false);
  }, []);

  /**
   * Mark a bench routine as running.
   *
   * A bench stage is launched through `bmg:start-bench-stage` rather than
   * through `launch`, so nothing here would otherwise know a process had
   * started: the state stayed `finished` from the previous routine and the
   * strip showed the second run as though it had never begun. This is the same
   * bookkeeping `launch` does, minus the mission arguments.
   */
  const beginBenchStage = useCallback(() => {
    processUp.current = true;
    setState('running');
    setStage(0);
    stageRef.current = 0;
    setStageName('');
    setExitCode(null);
    exitSeen.current = false;
    setStartedAt(Date.now());
  }, []);

  const clearLogs = useCallback(() => {
    setMissionLog([]);
    setDriverLog([]);
  }, []);

  return {
    state,
    stage,
    stageName,
    startedAt,
    exitCode,
    missionLog,
    driverLog,
    latestCapture,
    captureFlash,
    captureCount,
    launch,
    abort,
    reset,
    beginBenchStage,
    clearLogs,
  };
}
