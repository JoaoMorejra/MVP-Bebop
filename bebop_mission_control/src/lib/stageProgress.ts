/**
 * How far the aircraft is through each of the five stages, from what the
 * mission actually reports, and how the stage strip fills to show it.
 *
 * Nothing here is a schedule. Each stage reads the signals the mission raises
 * for it (`[MILESTONE ...]` lines, the countdown ticks, the measured altitude)
 * and, where the mission states the window it allows itself, the time spent
 * in that window. Where no figure exists the progress creeps toward a ceiling
 * it never reaches on its own: a stage is only ever complete once the mission
 * reports the next one. Pure: no React, no clock of its own.
 */

/** The signals of one flight, as the hook collects them. Times are epoch ms. */
export interface StageSignals {
  /** When the mission reported entering each stage, by stage number. */
  entered: Partial<Record<number, number>>;
  countdown: { remaining: number | null; total: number | null };
  trimAt: number | null;
  takeoffAt: number | null;
  targetAltitude: number | null;
  /** When the measured altitude first came within reach of the target after the takeoff. */
  altitudeReachedAt: number | null;
  scan: { at: number; windowSec: number } | null;
  targetFoundAt: number | null;
  approach: { at: number; windowSec: number } | null;
  captureAt: number | null;
  rtlAt: number | null;
  landing: { at: number; altitude: number } | null;
  touchdownAt: number | null;
}

export const EMPTY_SIGNALS: StageSignals = {
  entered: {},
  countdown: { remaining: null, total: null },
  trimAt: null,
  takeoffAt: null,
  targetAltitude: null,
  altitudeReachedAt: null,
  scan: null,
  targetFoundAt: null,
  approach: null,
  captureAt: null,
  rtlAt: null,
  landing: null,
  touchdownAt: null,
};

/** The most a stage reads before the mission has moved past it. */
export const MEASURED_CEILING = 0.99;

/** Fraction of the target altitude at which the climb counts as reached. */
export const ALTITUDE_REACHED_RATIO = 0.95;

const clamp01 = (value: number): number => (Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0);

/**
 * `from` moving toward `to` with time constant `tauMs`: quick at first, then
 * slower and slower, never arriving. The shape of a wait whose length is not
 * known.
 */
export function creep(elapsedMs: number, tauMs: number, from: number, to: number): number {
  if (!(elapsedMs > 0) || !(tauMs > 0)) return from;
  return from + (to - from) * (1 - Math.exp(-elapsedMs / tauMs));
}

const since = (at: number | null | undefined, now: number): number => (typeof at === 'number' ? Math.max(0, now - at) : 0);

/** Stage 1: ground sequence and countdown, the climb to the target, the settle. */
function takeoffProgress(s: StageSignals, now: number, altitude: number | null): number {
  let p = creep(since(s.entered[1], now), 6000, 0, 0.06);
  if (s.trimAt !== null) p = Math.max(p, 0.08);
  const { remaining, total } = s.countdown;
  if (remaining !== null && total !== null && total > 0) p = Math.max(p, 0.08 + 0.32 * clamp01(1 - remaining / total));
  if (s.takeoffAt !== null) {
    p = Math.max(p, 0.42);
    if (altitude !== null && s.targetAltitude !== null && s.targetAltitude > 0) {
      p = Math.max(p, 0.42 + 0.43 * clamp01(altitude / s.targetAltitude));
    }
  }
  if (s.altitudeReachedAt !== null) p = Math.max(p, creep(since(s.altitudeReachedAt, now), 3000, 0.85, 0.98));
  return p;
}

/** Stage 2: the time spent in the window the scan announced; a target found ends it. */
function scanProgress(s: StageSignals, now: number): number {
  let p = creep(since(s.entered[2], now), 4000, 0, 0.04);
  if (s.scan && s.scan.windowSec > 0) p = Math.max(p, 0.04 + 0.9 * clamp01(since(s.scan.at, now) / (s.scan.windowSec * 1000)));
  if (s.targetFoundAt !== null) p = Math.max(p, 0.96);
  return p;
}

/**
 * Stage 3: the approach. Its window is a timeout, not a duration: the
 * aircraft usually centres well before it, so the window only sets how slowly
 * the creep runs.
 */
function approachProgress(s: StageSignals, now: number): number {
  let p = creep(since(s.entered[3], now), 4000, 0, 0.04);
  if (s.approach && s.approach.windowSec > 0) {
    p = Math.max(p, creep(since(s.approach.at, now), (s.approach.windowSec * 1000) / 3, 0.04, 0.95));
  }
  return p;
}

/** Stage 4: the settle over the target, then the capture and its record. */
function inspectionProgress(s: StageSignals, now: number): number {
  let p = creep(since(s.entered[4], now), 8000, 0, 0.75);
  if (s.captureAt !== null) p = Math.max(p, creep(since(s.captureAt, now), 4000, 0.8, 0.97));
  return p;
}

/** Stage 5: the way back, then the descent measured against the altitude it started from. */
function returnProgress(s: StageSignals, now: number, altitude: number | null): number {
  const start = s.rtlAt ?? s.entered[5] ?? null;
  let p = creep(since(start, now), 20000, 0, 0.55);
  if (s.landing) {
    p = Math.max(p, 0.6);
    if (altitude !== null && s.landing.altitude > 0) p = Math.max(p, 0.6 + 0.37 * clamp01(1 - altitude / s.landing.altitude));
  }
  if (s.touchdownAt !== null) p = Math.max(p, MEASURED_CEILING);
  return p;
}

/** How far through stage `step` the signals put the aircraft, 0 to {@link MEASURED_CEILING}. */
export function measuredProgress(step: number, signals: StageSignals, now: number, altitude: number | null): number {
  const alt = altitude !== null && Number.isFinite(altitude) ? altitude : null;
  const value =
    step === 1
      ? takeoffProgress(signals, now, alt)
      : step === 2
      ? scanProgress(signals, now)
      : step === 3
      ? approachProgress(signals, now)
      : step === 4
      ? inspectionProgress(signals, now)
      : step === 5
      ? returnProgress(signals, now, alt)
      : 0;
  return Math.min(MEASURED_CEILING, Math.max(0, value));
}

/** What the strip shows: a fill per stage (index 0 is stage 1) and the stage it is filling. */
export interface FillState {
  fills: [number, number, number, number, number];
  /** 0 before the flight; otherwise the stage the strip is on, which may trail the mission's. */
  cursor: number;
}

export const EMPTY_FILL: FillState = { fills: [0, 0, 0, 0, 0], cursor: 0 };

/** Fill per second while a finished stage completes, or a skipped one is filled in: about 0.7 s a stage. */
export const COMPLETE_RATE = 1 / 700;
/** Fill per second the active stage may gain when following its signals, so a jump in them still reads as motion. */
export const FOLLOW_RATE = 0.5 / 1000;

export interface FillInput {
  /** The stage the mission reports, 0 before the first. */
  stage: number;
  /** {@link measuredProgress} of that stage. */
  measured: number;
  /** The flight ended on the ground as planned: the last stage completes. */
  finished: boolean;
  /** The flight stopped (abort, fault): the strip holds where it is. */
  halted: boolean;
}

/**
 * Move the strip `dtMs` forward.
 *
 * Stages the mission has left are filled to the end at {@link COMPLETE_RATE},
 * one after the other, before the strip moves on: a jump from 2 to 5 fills 2,
 * then 3, then 4, each in full, and only then starts 5. The stage the mission
 * is on follows its measured progress, never faster than {@link FOLLOW_RATE}
 * and never backwards, and only completes once the mission leaves it (or, for
 * the last, once the flight has finished). A mission sent back to an earlier
 * stage empties the stages from there on.
 */
export function advanceFill(prev: FillState, input: FillInput, dtMs: number): FillState {
  if (input.stage <= 0) return EMPTY_FILL;
  const fills = [...prev.fills] as FillState['fills'];
  let cursor = prev.cursor || 1;
  if (input.stage < cursor) {
    for (let index = input.stage - 1; index < 5; index += 1) fills[index] = 0;
    cursor = input.stage;
  }
  if (input.halted) return { fills, cursor };

  let budget = Math.max(0, dtMs);
  const target = Math.min(5, input.stage);
  while (cursor < target && budget > 0) {
    const index = cursor - 1;
    const needed = (1 - fills[index]) / COMPLETE_RATE;
    if (budget >= needed) {
      fills[index] = 1;
      budget -= needed;
      cursor += 1;
    } else {
      fills[index] += budget * COMPLETE_RATE;
      budget = 0;
    }
  }

  if (cursor === target && budget > 0) {
    const index = cursor - 1;
    if (input.finished && target === 5) {
      fills[index] = Math.min(1, fills[index] + budget * COMPLETE_RATE);
    } else {
      const goal = Math.min(MEASURED_CEILING, Math.max(0, input.measured));
      if (goal > fills[index]) fills[index] = Math.min(goal, fills[index] + budget * FOLLOW_RATE);
    }
  }
  return { fills, cursor };
}
