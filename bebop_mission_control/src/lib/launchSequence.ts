/**
 * The launch sequence shown in place on the pre-flight dial (D2, D3).
 *
 * From the click to 90 % the ring traverses six checkpoints in sequence:
 * the document committed, the link, the gimbal attitude, the IMU flat trim,
 * the ground reference and the vision warm-up, in the order `steps/takeoff.py`
 * runs them. The progression never lasts less than 10 seconds in total (at
 * least 7 s across 0..90 % so every checkpoint is cited in order, and 3 s+
 * across 90..100 % following the copilot's clearance speech under "Tudo
 * Pronto!"). When a checkpoint takes longer than its nominal window to
 * initialize, the count decelerates smoothly inside that checkpoint's band
 * without ever freezing on a fixed figure.
 */

export type LaunchStepId = 'params' | 'link' | 'gimbal' | 'imu' | 'ground' | 'vision';

export interface LaunchStep {
  id: LaunchStepId;
  label: string;
  /** Share of the 0..90 % loading span. */
  weight: number;
  /** Typical duration of the step, which paces its slow-creep tail when delayed. */
  expectedMs: number;
}

export const LAUNCH_STEPS: ReadonlyArray<LaunchStep> = [
  { id: 'params', label: 'Parâmetros', weight: 10, expectedMs: 1500 },
  { id: 'link', label: 'Enlace', weight: 10, expectedMs: 2000 },
  { id: 'gimbal', label: 'Gimbal', weight: 15, expectedMs: 3000 },
  { id: 'imu', label: 'IMU', weight: 20, expectedMs: 4000 },
  { id: 'ground', label: 'Solo', weight: 15, expectedMs: 3000 },
  { id: 'vision', label: 'Visão', weight: 20, expectedMs: 5000 },
];

/** The percent the checkpoints alone can reach; 90..100 % belongs to "Tudo Pronto!" and the copilot speech. */
export const LOAD_CEILING_PCT = 90;
/** Minimum time spent traversing the 0..90 % parameter initialization span when all signals are fast. */
export const LOAD_MIN_MS = 7_000;
/** Minimum total duration of the 0..100 % launch progression. */
export const MIN_SEQUENCE_MS = 10_000;

/** Percent held while the line or the countdown is still to finish. */
const HOLD_PCT = 99;

export type StepState = 'done' | 'active' | 'pending' | 'skipped';

export interface LaunchSignals {
  now: number;
  clickedAt: number;
  paramsCommitted: boolean;
  linkReady: boolean;
  stageReached: boolean;
  countdownStarted: boolean;
  trim: 'acked' | 'skipped' | null;
  clearance: boolean;
  /** The backend reported 0 and the click clock reached 0 as well. */
  countdownZero: boolean;
  speech: { startedAt: number | null; endedAt: number | null; heard: boolean; expectedMs: number };
}

export type LaunchPhase = 'loading' | 'announcing' | 'releasing' | 'done';

export const LAUNCH_MESSAGE = 'Tudo Pronto!' as const;

export interface LaunchView {
  phase: LaunchPhase;
  /** Integer, 0..100. */
  percent: number;
  stepId: LaunchStepId | null;
  label: string | null;
  message: typeof LAUNCH_MESSAGE | null;
}

/**
 * State of each checkpoint, following an event and never a schedule.
 *
 * The countdown's first tick (`mission.countdown`) is what confirms the
 * gimbal and the ground reference, both run before it opens; the trim waits
 * for the aircraft's acknowledgement (or its bench skip); the warm-up owns
 * the countdown window and the clearance call closes it. The clearance itself
 * is only made once every interlock passed, so it marks every checkpoint done.
 */
export function stepStates(s: LaunchSignals): Record<LaunchStepId, StepState> {
  if (s.clearance) {
    return {
      params: 'done',
      link: 'done',
      gimbal: 'done',
      imu: s.trim === 'skipped' ? 'skipped' : 'done',
      ground: 'done',
      vision: 'done',
    };
  }
  const staged = s.stageReached || s.countdownStarted;
  const settled: StepState = s.countdownStarted ? 'done' : staged ? 'active' : 'pending';
  return {
    params: s.paramsCommitted ? 'done' : 'active',
    link: s.linkReady ? 'done' : s.paramsCommitted ? 'active' : 'pending',
    gimbal: settled,
    imu: s.trim === 'acked' ? 'done' : s.trim === 'skipped' ? 'skipped' : staged ? 'active' : 'pending',
    ground: settled,
    vision: s.countdownStarted ? 'active' : 'pending',
  };
}

const settledState = (state: StepState) => state === 'done' || state === 'skipped';

/**
 * Maps a loading percentage `0..89` to the checkpoint whose cumulative weight
 * band owns that integer (`0..9` Parâmetros, `10..19` Enlace, `20..34` Gimbal,
 * `35..54` IMU, `55..69` Solo, `70..89` Visão).
 */
export function stepForPercent(pct: number): LaunchStep {
  const clamped = Math.max(0, Math.min(LOAD_CEILING_PCT - 1, Math.floor(pct)));
  let cursor = 0;
  for (const step of LAUNCH_STEPS) {
    cursor += step.weight;
    if (clamped < cursor) return step;
  }
  return LAUNCH_STEPS[LAUNCH_STEPS.length - 1];
}

/**
 * The view of the sequence at `s.now`, never below `floorPct`.
 *
 * Across `0..90 %`, progress is paced over at least `LOAD_MIN_MS` (7 s) so
 * every checkpoint band is traversed in order even when backend signals are
 * already ready. If a checkpoint takes longer than its nominal window to
 * settle, progress decelerates along a smooth rational curve inside that
 * checkpoint's band so the counter keeps ticking slowly without freezing on a
 * fixed number or crossing into the next checkpoint prematurely.
 *
 * At `90..100 %`, the label becomes `"Tudo Pronto!"` and follows the copilot's
 * clearance speech duration and the `MIN_SEQUENCE_MS` (10 s) minimum total
 * duration.
 */
export function deriveLaunchView(s: LaunchSignals, floorPct = 0): LaunchView {
  const states = stepStates(s);
  const allSettled = LAUNCH_STEPS.every((step) => settledState(states[step.id]));
  const floor = Number.isFinite(floorPct) ? Math.max(0, Math.min(HOLD_PCT, Math.floor(floorPct))) : 0;
  const elapsed = Math.max(0, s.now - s.clickedAt);
  const minElapsedMet = elapsed >= MIN_SEQUENCE_MS;

  if (s.countdownZero && !s.clearance && minElapsedMet) {
    return { phase: 'done', percent: 100, stepId: null, label: null, message: null };
  }

  let settledPrefixWeight = 0;
  let firstUnsettled: LaunchStep | null = null;
  for (const step of LAUNCH_STEPS) {
    if (settledState(states[step.id])) {
      settledPrefixWeight += step.weight;
    } else {
      firstUnsettled = step;
      break;
    }
  }

  let signalCap: number;
  if (allSettled && s.clearance) {
    signalCap = LOAD_CEILING_PCT;
  } else if (firstUnsettled === null) {
    signalCap = LOAD_CEILING_PCT - 1;
  } else {
    const w = firstUnsettled.weight;
    const bandStartMs = (settledPrefixWeight / LOAD_CEILING_PCT) * LOAD_MIN_MS;
    const bandDurationMs = (w / LOAD_CEILING_PCT) * LOAD_MIN_MS;
    const inBandMs = Math.max(0, elapsed - bandStartMs);
    const fastRatio = firstUnsettled.id === 'vision' ? 0.85 : 0.6;
    const fastWindowMs = bandDurationMs * fastRatio;
    const fastShare = fastRatio * w * Math.min(1, fastWindowMs > 0 ? inBandMs / fastWindowMs : 1);
    const overtimeMs = Math.max(0, inBandMs - fastWindowMs);
    const tailHalfLifeMs = firstUnsettled.id === 'vision' ? 220 : firstUnsettled.expectedMs;
    const slowShare = ((1 - fastRatio) * w - 0.05) * (overtimeMs / (overtimeMs + tailHalfLifeMs));
    signalCap = Math.min(LOAD_CEILING_PCT - 1, Math.floor(settledPrefixWeight + fastShare + slowShare));
  }

  const timePacedLoad = Math.min(LOAD_CEILING_PCT, Math.floor((elapsed / LOAD_MIN_MS) * LOAD_CEILING_PCT));
  const targetLoadPct = Math.min(signalCap, timePacedLoad);

  if (!(allSettled && s.clearance) || (targetLoadPct < LOAD_CEILING_PCT && floor < LOAD_CEILING_PCT)) {
    const percent = Math.max(floor, Math.min(LOAD_CEILING_PCT - 1, targetLoadPct));
    const currentStep = stepForPercent(percent);
    return { phase: 'loading', percent, stepId: currentStep.id, label: currentStep.label, message: null };
  }

  const { startedAt, endedAt, heard, expectedMs } = s.speech;
  const duration = expectedMs > 0 ? expectedMs : 1;
  const endsAt = startedAt === null ? null : heard ? endedAt : startedAt + duration;
  const speechDone = endsAt !== null && s.now >= endsAt;

  if (speechDone && s.countdownZero && minElapsedMet) {
    return { phase: 'done', percent: 100, stepId: null, label: null, message: null };
  }
  if ((speechDone || s.countdownZero) && minElapsedMet && (startedAt === null || speechDone || s.now >= startedAt + duration)) {
    return { phase: 'releasing', percent: HOLD_PCT, stepId: null, label: null, message: LAUNCH_MESSAGE };
  }
  if (s.countdownZero && startedAt !== null && !speechDone && minElapsedMet) {
    return { phase: 'releasing', percent: HOLD_PCT, stepId: null, label: null, message: LAUNCH_MESSAGE };
  }
  if (speechDone && !s.countdownZero && minElapsedMet) {
    return { phase: 'releasing', percent: HOLD_PCT, stepId: null, label: null, message: LAUNCH_MESSAGE };
  }

  const anchorStart = startedAt ?? s.clickedAt + LOAD_MIN_MS;
  const remainingToMin = Math.max(0, s.clickedAt + MIN_SEQUENCE_MS - anchorStart);
  const effectiveSpan = Math.max(duration, remainingToMin, 1);
  const spoken =
    startedAt === null
      ? Math.min(1, Math.max(0, elapsed - LOAD_MIN_MS) / Math.max(1, MIN_SEQUENCE_MS - LOAD_MIN_MS))
      : Math.min(1, Math.max(0, s.now - anchorStart) / effectiveSpan);
  const rawAnnouncePct = Math.floor(LOAD_CEILING_PCT + (HOLD_PCT - LOAD_CEILING_PCT) * spoken);
  const percent = Math.max(floor, Math.min(HOLD_PCT, speechDone && minElapsedMet ? HOLD_PCT : rawAnnouncePct));
  return { phase: 'announcing', percent, stepId: null, label: null, message: LAUNCH_MESSAGE };
}
