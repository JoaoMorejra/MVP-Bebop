import { useEffect, useRef, useState } from 'react';
import type { MilestoneEvent } from '../types/bmg';
import type { MissionState } from '../types/mission';
import {
  ALTITUDE_REACHED_RATIO,
  EMPTY_FILL,
  EMPTY_SIGNALS,
  advanceFill,
  fillInputFor,
  measuredProgress,
  type FillState,
  type StageSignals,
} from '../lib/stageProgress';
import { useBridge } from './useBridge';

/** Cadence of the strip, ms: fine enough for a fill that reads as continuous. */
export const STAGE_TICK_MS = 100;

const num = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

/** `signals` with what `event` says about the flight; the same object when it says nothing new. */
export function recordMilestone(signals: StageSignals, event: Pick<MilestoneEvent, 'key' | 'payload' | 'at'>): StageSignals {
  const payload = event.payload ?? {};
  const at = event.at;
  switch (event.key) {
    case 'mission.start':
      return { ...EMPTY_SIGNALS, entered: {} };
    case 'mission.parameters': {
      const total = num(payload.countdown_sec);
      return total !== null ? { ...signals, countdown: { ...signals.countdown, total } } : signals;
    }
    case 'mission.countdown': {
      const remaining = num(payload.remaining_sec);
      if (remaining === null) return signals;
      const total = signals.countdown.total ?? remaining;
      return { ...signals, countdown: { remaining, total: Math.max(total, remaining) } };
    }
    case 'mission.flat_trim':
      return { ...signals, trimAt: signals.trimAt ?? at };
    case 'mission.takeoff':
      return { ...signals, takeoffAt: signals.takeoffAt ?? at, targetAltitude: num(payload.target_altitude_m) ?? num(payload.altitude_m) };
    case 'mission.scan_start':
      return { ...signals, scan: { at, windowSec: num(payload.window_sec) ?? 0 } };
    case 'mission.target_found':
      return { ...signals, targetFoundAt: signals.targetFoundAt ?? at };
    case 'mission.approaching':
      return { ...signals, approach: { at, windowSec: num(payload.window_sec) ?? 0 } };
    case 'mission.capture_done':
      return { ...signals, captureAt: signals.captureAt ?? at };
    case 'mission.rtl_start':
      return { ...signals, rtlAt: signals.rtlAt ?? at };
    case 'mission.landing': {
      const altitude = num(payload.altitude_m);
      return altitude !== null ? { ...signals, landing: { at, altitude } } : signals;
    }
    case 'mission.touchdown':
      return { ...signals, touchdownAt: signals.touchdownAt ?? at };
    default:
      return signals;
  }
}

/**
 * The stage strip's fill, driven by the flight itself
 * (`lib/stageProgress.ts`).
 *
 * Collects the signals of the flight from the mission's milestones, the stage
 * markers and the measured altitude, and moves the fill every
 * {@link STAGE_TICK_MS}. Mounted with the app rather than the cockpit, so the
 * countdown and the takeoff, which happen before the cockpit opens, are not
 * missed. A new `mission.start`, or the cycle ending (stage back to 0), clears
 * it.
 */
export function useStageProgress(opts: { stage: number; state: MissionState; altitude: number | null }): FillState {
  const bridge = useBridge();
  const signals = useRef<StageSignals>({ ...EMPTY_SIGNALS, entered: {} });
  const [fill, setFill] = useState<FillState>(EMPTY_FILL);
  const latest = useRef(opts);
  latest.current = opts;

  useEffect(() => {
    if (!bridge?.onMilestone) return;
    return bridge.onMilestone((event) => {
      if (event.kind !== 'milestone') return;
      signals.current = recordMilestone(signals.current, event);
      if (event.key === 'mission.start') setFill(EMPTY_FILL);
    });
  }, [bridge]);

  useEffect(() => {
    if (opts.stage <= 0) {
      signals.current = { ...EMPTY_SIGNALS, entered: {} };
      setFill(EMPTY_FILL);
      return;
    }
    if (signals.current.entered[opts.stage] === undefined) {
      signals.current = { ...signals.current, entered: { ...signals.current.entered, [opts.stage]: Date.now() } };
    }
  }, [opts.stage]);

  const active = opts.stage > 0;
  useEffect(() => {
    if (!active) return;
    let last = Date.now();
    const id = window.setInterval(() => {
      const now = Date.now();
      const dt = now - last;
      last = now;
      const { stage, state, altitude } = latest.current;
      const s = signals.current;
      if (
        s.takeoffAt !== null &&
        s.altitudeReachedAt === null &&
        altitude !== null &&
        s.targetAltitude !== null &&
        altitude >= s.targetAltitude * ALTITUDE_REACHED_RATIO
      ) {
        signals.current = { ...s, altitudeReachedAt: now };
      }
      const measured = measuredProgress(stage, signals.current, now, altitude);
      setFill((previous) => {
        const next = advanceFill(previous, fillInputFor(state, stage, measured), dt);
        // A strip at rest renders nothing new: the app is not re-rendered for it.
        const same =
          next.cursor === previous.cursor && next.fills.every((value, index) => Math.abs(value - previous.fills[index]) < 1e-4);
        return same ? previous : next;
      });
    }, STAGE_TICK_MS);
    return () => window.clearInterval(id);
  }, [active]);

  return fill;
}
