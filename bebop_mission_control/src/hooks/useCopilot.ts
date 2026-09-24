import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnnouncePriority } from '../types/bmg';
import type { Finding } from '../lib/forensics';
import { REPORT_INTRO, REPORT_OUTRO, findingGapMs } from '../lib/forensics';
import type { MilestoneKey } from '../lib/copilotPhrases';
import { describeTargetLocation, nextPhrase, spokenMeters } from '../lib/copilotPhrases';
import { useBridge } from './useBridge';

/**
 * Ceiling on how long one spoken line may hold the sequence that is waiting on
 * it. Synthesis normally answers in a second or two; this exists so a copilot
 * that has stopped answering degrades the presentation to its own timing
 * instead of freezing it half-read.
 */
const SPEECH_CEILING_MS = 14000;

export interface Copilot {
  /** Speak one line. Resolves when the operator has heard it, or when it is clear they will not. */
  say: (text: string, priority?: AnnouncePriority) => Promise<boolean>;
  /** Drop whatever is queued and silence the current line. */
  cancel: () => void;
  /** Whether there is a voice bridge at all. False in a browser session. */
  available: boolean;
}

/**
 * The voice copilot, as the interface talks to it.
 *
 * `say` resolves on the sentence having been *heard*, not queued, which is the
 * whole point: the post-landing report reveals each card when its own sentence
 * finishes, so the screen follows the narration rather than racing it. Without
 * a bridge every call resolves false immediately and the caller falls back to
 * its own cadence.
 */
export function useCopilot(): Copilot {
  const bridge = useBridge();
  const pending = useRef(new Map<number, (ok: boolean) => void>());
  /**
   * Verdicts that arrived before anyone was waiting for them.
   *
   * `bmg:announce-done` is a different channel from the `invoke` reply that
   * carries the line's id, and it is not ordered against it. A copilot that
   * cannot speak answers in microseconds, so the verdict routinely wins that
   * race — and a sequence registering its resolver afterwards would then wait
   * out the full ceiling for an answer that had already come and gone.
   */
  const settled = useRef(new Map<number, boolean>());

  useEffect(() => {
    if (!bridge) return;
    return bridge.onAnnounceDone((event) => {
      if (event.id === null) {
        // The copilot died holding a request. Release everyone waiting rather
        // than leaving a sequence parked on a line that will never be read.
        for (const resolve of pending.current.values()) resolve(false);
        pending.current.clear();
        return;
      }
      const resolve = pending.current.get(event.id);
      if (!resolve) {
        settled.current.set(event.id, event.ok);
        // Ids only ever grow, so the early arrivals worth remembering are the
        // recent ones. This keeps the map from being a leak.
        if (settled.current.size > 32) {
          const oldest = Math.min(...settled.current.keys());
          settled.current.delete(oldest);
        }
        return;
      }
      pending.current.delete(event.id);
      resolve(event.ok);
    });
  }, [bridge]);

  // A component unmounting mid-report must not leave its callers awaiting.
  useEffect(
    () => () => {
      for (const resolve of pending.current.values()) resolve(false);
      pending.current.clear();
    },
    []
  );

  const say = useCallback(
    async (text: string, priority: AnnouncePriority = 'NORMAL') => {
      if (!bridge || !text.trim()) return false;

      const result = await bridge.announce({ text, priority }).catch(() => null);
      if (!result?.success || result.id === undefined) return false;

      const id = result.id;

      // The verdict may already be in hand.
      const early = settled.current.get(id);
      if (early !== undefined) {
        settled.current.delete(id);
        return early;
      }

      return new Promise<boolean>((resolve) => {
        const settle = (ok: boolean) => {
          window.clearTimeout(timer);
          pending.current.delete(id);
          resolve(ok);
        };
        const timer = window.setTimeout(() => settle(false), SPEECH_CEILING_MS);
        pending.current.set(id, settle);
      });
    },
    [bridge]
  );

  const cancel = useCallback(() => {
    for (const resolve of pending.current.values()) resolve(false);
    pending.current.clear();
    if (bridge) void bridge.cancelSpeech().catch(() => undefined);
  }, [bridge]);

  return { say, cancel, available: Boolean(bridge) };
}

/**
 * The phrase pool each reported stage is narrated from.
 *
 * Stage 3 begins only once Stage 2 has confirmed a target, which is why its
 * marker carries the target-found call. The stage marker holds no position, so
 * that call names the runway rather than a range.
 */
const STAGE_MILESTONES: Record<number, MilestoneKey> = {
  1: 'mission.takeoff',
  2: 'mission.scan_start',
  3: 'mission.target_found',
  4: 'mission.capture_done',
  5: 'mission.rtl_start',
};

const TOUCHDOWN_CALL = 'Pouso seguro concluído com sucesso na base.';

/**
 * Narrate the flight itself.
 *
 * Driven by the stage the mission has actually reported, never by a timer, so
 * the copilot cannot announce a manoeuvre the aircraft did not reach. Each
 * stage is called once per flight: `mission.py` re-logging a step marker does
 * not make the copilot say it again.
 *
 * `flightKey` is what makes "per flight" mean anything — the launch timestamp,
 * which is a new value for every launch and null once the cycle is closed.
 * Keying the reset on `enabled` instead left the set of already-called stages
 * populated across an entire session, so the copilot went silent from the
 * second flight onward.
 *
 * Each call is drawn from its phrase pool with the cross-flight history in
 * `copilotPhrases`, so consecutive flights do not repeat a wording.
 * `altitudeM` is the configured target altitude the takeoff call cites; it is
 * read when the call is made, so a later edit does not re-trigger it.
 */
export function useFlightNarration(
  copilot: Copilot,
  stage: number,
  landed: boolean,
  enabled: boolean,
  flightKey: number | null,
  altitudeM: number = Number.NaN
): void {
  const called = useRef(new Set<number>());
  const touchdownCalled = useRef(false);
  const altitude = useRef(altitudeM);
  altitude.current = altitudeM;
  const { say } = copilot;

  useEffect(() => {
    called.current.clear();
    touchdownCalled.current = false;
  }, [flightKey]);

  useEffect(() => {
    if (!enabled || !stage) return;
    const key = STAGE_MILESTONES[stage];
    if (!key || called.current.has(stage)) return;
    called.current.add(stage);
    void say(
      nextPhrase(key, {
        altitude: spokenMeters(altitude.current),
        location: describeTargetLocation(null),
      })
    );
  }, [enabled, stage, say]);

  useEffect(() => {
    if (!landed || touchdownCalled.current) return;
    touchdownCalled.current = true;
    void say(TOUCHDOWN_CALL);
  }, [landed, say]);
}

/**
 * Present the preliminary report, in step with the voice.
 *
 * The sequence is: the introduction, then each finding read aloud with its card
 * appearing as the sentence lands, then the closing line. Returns how many
 * cards have been revealed so far, which is what the panel renders.
 *
 * Each reveal waits for the *later* of two things: the sentence having been
 * read, and a drawn beat of roughly two seconds. That pairing is deliberate.
 * Waiting on the voice alone is what puts the card on its own sentence, but a
 * copilot that cannot speak — no API key, no network, no audio device — answers
 * every request in milliseconds, and a report paced by that answer dumps all
 * four findings in a single frame. Waiting on the beat alone would let the
 * cards run ahead of a voice still mid-sentence. Taking whichever is longer
 * gives the presentation one rhythm: narrated when there is narration, and the
 * same deliberate cadence when there is not.
 */
export function useForensicNarration(
  copilot: Copilot,
  active: boolean,
  report: Finding[] | null
): number {
  const [revealed, setRevealed] = useState(0);
  const runRef = useRef(0);
  const { say, cancel } = copilot;

  useEffect(() => {
    if (!active || !report || report.length === 0) {
      setRevealed(0);
      return;
    }

    // Each run carries a token; a run whose token has been superseded stops at
    // its next checkpoint rather than painting over the one that replaced it.
    const token = runRef.current + 1;
    runRef.current = token;
    const live = () => runRef.current === token;

    // Every pending beat is tracked with the resolver it is holding. A run torn
    // down mid-beat must neither leave a timer armed to fire into the sequence
    // that replaced it, nor leave its own `Promise.all` suspended forever —
    // clearing the timer alone would park the run's closure for the life of the
    // page. Tearing down settles them instead, and the `live()` checks after
    // each beat stop the freed run from touching state.
    const beats = new Map<number, () => void>();
    const wait = (ms: number) =>
      new Promise<void>((resolve) => {
        const id = window.setTimeout(() => {
          beats.delete(id);
          resolve();
        }, ms);
        beats.set(id, resolve);
      });

    /** Hold for the sentence and for the beat, and continue on the later of the two. */
    const beat = (line: string) => Promise.all([say(line), wait(findingGapMs())]);

    void (async () => {
      setRevealed(0);
      await beat(REPORT_INTRO);

      for (let index = 0; index < report.length; index++) {
        if (!live()) return;
        await beat(report[index].speech);
        if (!live()) return;
        setRevealed(index + 1);
      }

      if (!live()) return;
      await beat(REPORT_OUTRO);
    })();

    return () => {
      runRef.current = token + 1;
      for (const [id, resolve] of beats) {
        window.clearTimeout(id);
        resolve();
      }
      beats.clear();
      cancel();
    };
  }, [active, report, say, cancel]);

  return revealed;
}
