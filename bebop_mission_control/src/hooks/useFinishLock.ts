import { useEffect, useRef, useState } from 'react';
import type { MissionState, TelemetryView } from '../types/mission';
import { finishLockState, type FinishLockResult } from '../lib/finishLock';
import { isMissionOver } from '../lib/missionOutcome';

/** Re-evaluation period: the lock has time-based transitions (2 s, 10 s). */
const TICK_MS = 250;

interface UseFinishLockArgs {
  missionState: MissionState;
  exitCode: number | null;
  benchMode: boolean;
  telemetry: TelemetryView;
  finishing: boolean;
}

const PROCESS_UP_STATES: ReadonlySet<MissionState> = new Set<MissionState>(['arming', 'running', 'aborting']);

/**
 * Feed `finishLockState` from the live cockpit state.
 *
 * Tracks what the pure function needs and a single frame cannot say: since
 * when the flying state has held its current value, the last value the
 * simulator reported in this run (the bridge drops it three seconds after the
 * process ends), and when the process exited. Re-evaluates on its own clock,
 * so a frame going stale is noticed without a new frame arriving.
 */
export function useFinishLock({
  missionState,
  exitCode,
  benchMode,
  telemetry,
  finishing,
}: UseFinishLockArgs): FinishLockResult {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  const flyingState = typeof telemetry.flying_state === 'number' ? telemetry.flying_state : null;
  const since = useRef<{ value: number | null; at: number }>({ value: flyingState, at: Date.now() });
  if (since.current.value !== flyingState) since.current = { value: flyingState, at: Date.now() };

  const processUp = PROCESS_UP_STATES.has(missionState);
  const benchState = useRef<number | null>(null);
  const wasUp = useRef(processUp);
  if (processUp && !wasUp.current) benchState.current = null;
  wasUp.current = processUp;
  if (telemetry.nav_source === 'simulator' && flyingState !== null) benchState.current = flyingState;

  const over = isMissionOver(missionState);
  const exitedAt = useRef<number | null>(over ? Date.now() : null);
  if (over && exitedAt.current === null) exitedAt.current = Date.now();
  if (!over) exitedAt.current = null;

  const current = Math.max(now, Date.now());
  const telemetryAgeSec =
    typeof telemetry.at === 'number' ? Math.max(0, (current - telemetry.at) / 1000) : telemetry.ageSec ?? null;

  return finishLockState(
    {
      missionState,
      processUp,
      exitCode,
      benchMode,
      flyingState,
      flyingStateSince: since.current.at,
      navFresh: Boolean(telemetry.nav_fresh),
      navSource: telemetry.nav_source,
      telemetryAgeSec,
      finishing,
      benchFlyingState: benchState.current,
      exitedAt: exitedAt.current,
    },
    current
  );
}
