import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnnouncePriority } from '../types/bmg';
import type { Finding } from '../lib/forensics';
import { findingGapMs } from '../lib/forensics';
import {
  alertSentence,
  isMilestoneKey,
  nextPhrase,
  phraseForMilestone,
} from '../lib/copilotPhrases';
import { NarrationQueue } from '../lib/narrationQueue';
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

const TOUCHDOWN_CALL = 'Pouso seguro concluído com sucesso na base.';

/** Queue group of the post-landing report's lines. */
const REPORT_GROUP = 'forensic';

/**
 * The one narration queue the station speaks through.
 *
 * Shared by the flight narration and the post-landing report, so the whole
 * script -- milestones, alerts, the touchdown call, the report -- is one FIFO
 * with one voice, rather than two sequences each calling the copilot and
 * talking over each other at the seam between landing and report.
 */
export function useNarrationQueue(copilot: Copilot): NarrationQueue {
  const sayRef = useRef(copilot.say);
  sayRef.current = copilot.say;
  const cancelRef = useRef(copilot.cancel);
  cancelRef.current = copilot.cancel;
  const queue = useRef<NarrationQueue | null>(null);
  if (queue.current === null) {
    queue.current = new NarrationQueue(
      (text, priority) => sayRef.current(text, priority),
      () => cancelRef.current()
    );
  }
  useEffect(() => () => queue.current?.reset(), []);
  return queue.current;
}

/**
 * Narrate the flight itself, one milestone at a time.
 *
 * Driven by the milestones the flight actually crossed (`bmg:milestone`),
 * never by a timer, so the copilot cannot announce a manoeuvre the aircraft
 * did not reach. The flight does not wait for the voice: milestones go into
 * the shared FIFO {@link NarrationQueue} as they arrive and are spoken in that
 * order, each once the previous line has been heard, so an early detection is
 * narrated after the takeoff and scan calls it overtook rather than over them.
 *
 * Each key is spoken once per flight, drawn from its phrase pool with the
 * cross-flight history in `copilotPhrases`. A flight begins at its
 * `mission.start` milestone, which clears the queue and that per-flight set.
 * The reset is keyed on the milestone and not on `flightKey` because the
 * launch timestamp is committed by a React render that can land after the
 * main process has already sent `mission.start`, and a reset then would drop
 * the flight's first line. `flightKey` returning to null -- the cycle closed --
 * drops whatever is still queued.
 *
 * `altitudeM` is the configured target altitude, the fallback for a takeoff
 * milestone whose payload lacks one. Milestones are ignored while `enabled` is
 * false.
 *
 * Alerts are not. Under the station the mission has no voice of its own
 * (`announcer.station_narrates`), so a failure, abort or failsafe it reports is
 * heard only if this says it: every alert preempts the narration, bench or not.
 */
export function useFlightNarration(
  queue: NarrationQueue,
  landed: boolean,
  enabled: boolean,
  flightKey: number | null,
  altitudeM: number = Number.NaN
): void {
  const bridge = useBridge();
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const altitude = useRef(altitudeM);
  altitude.current = altitudeM;
  const spoken = useRef(new Set<string>());
  const touchdownCalled = useRef(false);

  useEffect(() => {
    if (!bridge) return;
    return bridge.onMilestone((event) => {
      if (event.kind === 'alert') {
        const payload = event.payload ?? {};
        queue.preempt(event.key, () => alertSentence(payload), 'URGENT');
        return;
      }
      if (!enabledRef.current || !isMilestoneKey(event.key)) return;
      const key = event.key;
      if (key === 'mission.start') {
        queue.reset();
        spoken.current.clear();
        touchdownCalled.current = false;
      }
      if (spoken.current.has(key)) return;
      spoken.current.add(key);
      const payload = event.payload ?? {};
      queue.enqueue(key, () => phraseForMilestone(key, payload, altitude.current));
    });
  }, [bridge, queue]);

  useEffect(() => {
    if (flightKey === null) queue.reset();
  }, [flightKey, queue]);

  useEffect(() => {
    if (!landed || touchdownCalled.current) return;
    touchdownCalled.current = true;
    queue.enqueue('touchdown', () => TOUCHDOWN_CALL);
  }, [landed, queue]);
}

/**
 * Present the preliminary report, in step with the voice.
 *
 * The sequence is the introduction (`inspection.intro`), each finding read
 * aloud with its card appearing as its sentence ends (`inspection.point_1..4`),
 * then the closing line (`inspection.outro`). All of it goes through the same
 * queue as the flight narration, behind whatever the flight still has to say
 * -- the touchdown call first, then the report -- and a report torn down
 * midway drops only its own lines.
 *
 * Each reveal waits for the *later* of two things: the sentence having been
 * read, and a drawn beat of roughly two seconds. Waiting on the voice alone is
 * what puts the card on its own sentence, but a copilot that cannot speak -- no
 * API key, no network, no audio device -- answers every request in
 * milliseconds, and a report paced by that answer would dump all four findings
 * in a single frame. Taking whichever is longer gives the presentation one
 * rhythm: narrated when there is narration, and the same deliberate cadence
 * when there is not.
 *
 * Returns how many cards have been revealed, which is what the panel renders,
 * and the closing line as spoken, so the screen shows the same sentence.
 */
export function useForensicNarration(
  queue: NarrationQueue,
  active: boolean,
  report: Finding[] | null
): { revealed: number; closing: string | null } {
  const [revealed, setRevealed] = useState(0);
  const [closing, setClosing] = useState<string | null>(null);
  const runRef = useRef(0);

  useEffect(() => {
    if (!active || !report || report.length === 0) {
      setRevealed(0);
      setClosing(null);
      return;
    }

    // Each run carries a token; a callback from a superseded run is ignored
    // even if it slips past the group reset.
    const token = runRef.current + 1;
    runRef.current = token;
    const live = () => runRef.current === token;

    setRevealed(0);
    const outro = nextPhrase('inspection.outro');
    setClosing(outro);

    queue.enqueue('inspection.intro', () => nextPhrase('inspection.intro'), {
      group: REPORT_GROUP,
      minMs: findingGapMs(),
    });
    report.forEach((finding, index) => {
      queue.enqueue(`inspection.point_${index + 1}`, () => finding.speech, {
        group: REPORT_GROUP,
        minMs: findingGapMs(),
        onDone: () => {
          if (live()) setRevealed(index + 1);
        },
      });
    });
    queue.enqueue('inspection.outro', () => outro, { group: REPORT_GROUP, minMs: findingGapMs() });

    return () => {
      runRef.current = token + 1;
      queue.resetGroup(REPORT_GROUP);
    };
  }, [active, report, queue]);

  return { revealed, closing };
}
