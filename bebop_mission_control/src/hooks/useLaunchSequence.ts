import { useEffect, useMemo, useRef, useState } from 'react';
import {
  LAUNCH_MESSAGE,
  LOAD_CEILING_PCT,
  MIN_SEQUENCE_MS,
  deriveLaunchView,
  stepForPercent,
  type LaunchView,
} from '../lib/launchSequence';
import { estimateSpeechMs, readSpeechMs } from '../lib/speechTiming';

export interface LaunchSequenceInput {
  /** The launch is being prepared (`App.counting`). */
  active: boolean;
  /** Click instant, epoch ms; the backend takes off at it plus `seconds`. */
  clickedAt: number | null;
  /** The countdown window of the document sent (`kinematics.countdown_sec`). */
  seconds: number;
  paramsCommitted: boolean;
  linkReady: boolean;
  stageReached: boolean;
  countdown: { remaining: number | null; clearance: boolean; trim: 'acked' | 'skipped' | null };
  speech: { startedAt: number | null; endedAt: number | null; heard: boolean };
  /** The clearance line, once known; its measured or estimated length paces 90..100 %. */
  speechText: string | null;
  onDone: () => void;
}

/**
 * The click clock ticks at 40 ms (25 Hz) while the sequence is on screen so
 * the displayed percentage increments +1 % per tick without skipping numbers
 * or checkpoint labels.
 */
const TICK_MS = 40;

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * The launch sequence of the pre-flight dial, as a view (D2, D3, D4).
 *
 * The progression always starts at 0 %, lasts at least 10 seconds
 * (`MIN_SEQUENCE_MS`), traverses every checkpoint in order across 0..89 %
 * without skipping numbers or steps, shows "Tudo Pronto!" across 90..99 %
 * following the copilot's speech duration, and calls `onDone` once at 100 %.
 */
export function useLaunchSequence(input: LaunchSequenceInput): LaunchView | null {
  const { active, clickedAt, seconds, onDone } = input;
  const [now, setNow] = useState(() => Date.now());
  const floor = useRef(0);
  const lastStepAt = useRef<number | null>(null);
  const doneFor = useRef<number | null>(null);
  const zeroAt = useRef<number | null>(null);
  /** The launch the floor and the zero instant belong to; a new click starts both over. */
  const owner = useRef<number | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const sequenceKey = active ? clickedAt : null;
  if (owner.current !== sequenceKey) {
    owner.current = sequenceKey;
    floor.current = 0;
    lastStepAt.current = null;
    zeroAt.current = null;
  }

  useEffect(() => {
    if (!active || clickedAt === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [active, clickedAt]);

  const expectedMs = useMemo(() => {
    const text = input.speechText;
    return (text ? readSpeechMs(storage(), text) : null) ?? estimateSpeechMs(text ?? '');
  }, [input.speechText]);

  const current = Math.max(now, Date.now());
  let view: LaunchView | null = null;
  if (active && clickedAt !== null) {
    const effectiveSeconds = Math.max(MIN_SEQUENCE_MS / 1000, seconds);
    const clockRemaining = Math.max(0, Math.ceil(effectiveSeconds - (current - clickedAt) / 1000));
    const countdownZero = input.countdown.remaining === 0 && clockRemaining === 0;
    if (countdownZero && zeroAt.current === null) zeroAt.current = current;
    const speech =
      input.speech.startedAt === null && countdownZero && input.countdown.clearance && zeroAt.current !== null
        ? { startedAt: zeroAt.current, endedAt: null, heard: false }
        : input.speech;

    const rawView = deriveLaunchView(
      {
        now: current,
        clickedAt,
        paramsCommitted: input.paramsCommitted,
        linkReady: input.linkReady,
        stageReached: input.stageReached,
        countdownStarted: input.countdown.remaining !== null,
        trim: input.countdown.trim,
        clearance: input.countdown.clearance,
        countdownZero,
        speech: { ...speech, expectedMs },
      },
      0
    );

    if (lastStepAt.current === null) {
      if (current - clickedAt <= TICK_MS * 2) {
        floor.current = 0;
      } else {
        floor.current = rawView.percent;
      }
      lastStepAt.current = current;
    } else {
      const dt = Math.max(0, current - lastStepAt.current);
      if (dt >= TICK_MS) {
        lastStepAt.current = current;
        if (rawView.percent > floor.current) {
          const stepIncrement = dt <= TICK_MS * 3 ? 1 : Math.max(1, Math.floor(dt / TICK_MS));
          floor.current = Math.min(rawView.percent, floor.current + stepIncrement);
        }
      }
    }

    const displayedPct = floor.current;
    if (displayedPct >= 100 && rawView.phase === 'done') {
      view = { phase: 'done', percent: 100, stepId: null, label: null, message: null };
    } else if (displayedPct >= LOAD_CEILING_PCT) {
      const pct = Math.min(99, displayedPct);
      const phase = rawView.phase === 'releasing' || rawView.phase === 'done' ? 'releasing' : 'announcing';
      view = { phase, percent: pct, stepId: null, label: null, message: LAUNCH_MESSAGE };
    } else {
      const step = stepForPercent(displayedPct);
      view = { phase: 'loading', percent: displayedPct, stepId: step.id, label: step.label, message: null };
    }
  }

  const done = view?.phase === 'done';
  useEffect(() => {
    if (!done || clickedAt === null || doneFor.current === clickedAt) return;
    doneFor.current = clickedAt;
    onDoneRef.current();
  }, [done, clickedAt]);

  return view;
}
