import { useCallback, useEffect, useRef, useState } from 'react';
import type { AnnouncePriority } from '../types/bmg';
import type { ForensicScript } from '../lib/forensics';
import { findingGapMs } from '../lib/forensics';
import {
  alertSentence,
  isMilestoneKey,
  linkLossSentence,
  phraseForMilestone,
  takeLaunchPhrase,
  isMilestoneAllowed,
  type MissionParametersPayload,
} from '../lib/copilotPhrases';
import { buildSpeechManifest } from '../lib/spokenCatalog';
import { parseMissionMode, type MissionMode } from '../lib/missionMode';

const MILESTONE_STAGE: Record<string, number> = {
  'mission.start': 1,
  'countdown_3': 1,
  'mission.takeoff': 1,
  'mission.scan_start': 2,
  'mission.target_found': 2,
  'mission.approach': 3,
  'mission.approaching': 3,
  'mission.capture_done': 4,
  'mission.rtl_start': 5,
  'mission.landing': 5,
  'mission.touchdown': 5,
};
import { NarrationQueue } from '../lib/narrationQueue';
import { useBridge } from './useBridge';

/**
 * Ceiling on how long one spoken line may hold the sequence that is waiting on
 * it. Synthesis normally answers in a second or two; this exists so a copilot
 * that has stopped answering degrades the presentation to its own timing
 * instead of freezing it half-read.
 */
export const SPEECH_CEILING_MS = 14000;

export interface Copilot {
  /** Speak one line. Resolves when the operator has heard it, or when it is clear they will not. */
  say: (text: string, priority?: AnnouncePriority) => Promise<boolean>;
  /** Synthesize the line that will be said next, so it starts without delay. */
  prepare: (text: string) => void;
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
        // Past the ceiling the line is written off here, so it is cancelled in
        // the copilot too: otherwise it would still play, late, over the next.
        const timer = window.setTimeout(() => {
          settle(false);
          void bridge.cancelSpeech().catch(() => undefined);
        }, SPEECH_CEILING_MS);
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

  const prepare = useCallback(
    (text: string) => {
      if (bridge && text.trim()) void bridge.prepareSpeech(text).catch(() => undefined);
    },
    [bridge]
  );

  return { say, prepare, cancel, available: Boolean(bridge) };
}

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
  const prepareRef = useRef(copilot.prepare);
  prepareRef.current = copilot.prepare;
  const queue = useRef<NarrationQueue | null>(null);
  if (queue.current === null) {
    queue.current = new NarrationQueue(
      (text, priority) => sayRef.current(text, priority),
      () => cancelRef.current(),
      (text) => prepareRef.current(text)
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
 * Spoken numbers come from the mission alone (D2): the milestone's payload, or
 * the `mission.parameters` document the mission emits once at start, held here
 * for the rest of the flight. Nothing is defaulted from the station's own
 * copy of the parameters. Milestones are ignored while `enabled` is false.
 *
 * The profile comes from the `mission.mode` milestone, never from the interface:
 * it is what the mission resolved. Without one the full repertoire is spoken.
 *
 * Alerts are not. Under the station the mission has no voice of its own
 * (`announcer.station_narrates`), so a failure, abort or failsafe it reports is
 * heard only if this says it: every alert preempts the narration, bench or not.
 */
export function useFlightNarration(
  queue: NarrationQueue,
  enabled: boolean,
  flightKey: number | null
): void {
  const bridge = useBridge();
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const parameters = useRef<MissionParametersPayload>({});
  const spoken = useRef(new Set<string>());
  const missionMode = useRef<MissionMode | null>(null);
  /** The return plan the mission echoed on `mission.mode`; true until it says otherwise. */
  const rtlEnabled = useRef(true);
  /** Set by `mission.battery_return` until the next `mission.start` (D9). */
  const batteryReturnActive = useRef(false);

  useEffect(() => {
    if (!bridge) return;
    const unsubMilestone = bridge.onMilestone((event) => {
      if (event.kind === 'alert') {
        const payload = event.payload ?? {};
        const pri = event.key === 'mission.abort' ? 'LAND' : 'URGENT';
        queue.preempt(event.key, () => alertSentence(payload, event.key), pri);
        return;
      }
      if (event.key === 'mission.mode') {
        missionMode.current = parseMissionMode((event.payload ?? {}).mode);
        rtlEnabled.current = (event.payload ?? {}).rtl_enabled !== false;
        return;
      }
      if (event.key === 'mission.parameters') {
        parameters.current = event.payload ?? {};
        // Everything this mission can say is known now (D1, D2): have it
        // synthesized to disk while the aircraft is still on the ground.
        const plan = { mode: missionMode.current ?? 'advanced', rtlEnabled: rtlEnabled.current };
        try {
          void bridge
            .cacheSpeech?.(buildSpeechManifest([{ parameters: parameters.current, plan }]).map((entry) => entry.text))
            .catch(() => undefined);
        } catch (error) {
          console.warn('[BMG] cache de falas não aquecido:', error);
        }
        return;
      }
      if (!enabledRef.current || !isMilestoneKey(event.key)) return;
      const key = event.key;
      if (key === 'mission.start') {
        queue.reset();
        spoken.current.clear();
        parameters.current = {};
        missionMode.current = null;
        rtlEnabled.current = true;
        batteryReturnActive.current = false;
      }
      if (key === 'mission.scan_start' && !spoken.current.has('mission.takeoff')) return;
      // "Missão segue" would contradict the return already under way (D9).
      if (key === 'mission.battery_warning' && batteryReturnActive.current) return;
      const plan = { mode: missionMode.current ?? 'advanced', rtlEnabled: rtlEnabled.current };
      const payload = event.payload ?? {};
      if (!isMilestoneAllowed(key, plan, payload)) return;
      if (spoken.current.has(key)) return;
      spoken.current.add(key);
      if (key === 'mission.battery_return') {
        // The crossing is the only voice of the threshold (D8): it cuts the
        // narration still queued, which describes a flight that has ended.
        batteryReturnActive.current = true;
        queue.preempt(key, () => phraseForMilestone(key, payload, parameters.current, {}, plan), 'URGENT');
        return;
      }
      // A launch line drawn ahead (`reserveLaunchPhrase`) is spoken as drawn,
      // so the audio synthesized for it at the click is the audio played.
      const launchLine = takeLaunchPhrase(key);
      queue.enqueue(key, () => launchLine ?? phraseForMilestone(key, payload, parameters.current, {}, plan), {
        stage: MILESTONE_STAGE[key],
      });
    });

    const unsubStep = bridge.onStepChange?.((event) => {
      if (typeof event?.stepNumber === 'number') {
        queue.pruneEarlierStages(event.stepNumber);
      }
    });

    return () => {
      unsubMilestone();
      unsubStep?.();
    };
  }, [bridge, queue]);

  useEffect(() => {
    if (flightKey === null) queue.reset();
  }, [flightKey, queue]);

}

/** Queue label of the link-loss call. */
const LINK_LOSS_LABEL = 'link.lost';

/**
 * How long the telemetry must stay fresh before a new loss is called again, ms.
 *
 * The main process restarts a telemetry bridge that dies, with backoff, and
 * its watchdog reads 3 s of bridge silence as a disconnected aircraft
 * (`electron/main.cjs`), so `stale` can flip off and on within seconds while
 * the link is still down. A recovery shorter than this is the same loss.
 */
export const LINK_REARM_DWELL_MS = 3000;

/**
 * Say once that the aircraft's telemetry stopped reaching the station.
 *
 * Edge-triggered on `linkLost`: one call when it turns true, none while it
 * stays true, and re-armed only after it has been false for
 * {@link LINK_REARM_DWELL_MS} without interruption, so a flapping link is
 * called once per loss rather than once per flap. The call preempts the
 * narration at `URGENT`, as a mission alert does, and is never `LAND`: the
 * station cannot command a landing it can no longer reach.
 *
 * `linkLost` is the caller's verdict. The cockpit passes the stale telemetry
 * of `useTelemetry` (no current `/bebop/odom` from the aircraft, or the bridge
 * silent) while a real flight is running; `useFinishLock`'s `link_lost` is not
 * used, since it is only reached after the mission process has exited.
 */
export function useLinkLossNarration(
  queue: NarrationQueue,
  linkLost: boolean,
  random: () => number = Math.random
): void {
  const armed = useRef(true);
  const randomRef = useRef(random);
  randomRef.current = random;

  useEffect(() => {
    if (!linkLost) {
      if (armed.current) return;
      const timer = window.setTimeout(() => {
        armed.current = true;
      }, LINK_REARM_DWELL_MS);
      return () => window.clearTimeout(timer);
    }
    if (!armed.current) return;
    armed.current = false;
    const sentence = linkLossSentence(randomRef.current);
    queue.preempt(LINK_LOSS_LABEL, () => sentence, 'URGENT');
  }, [linkLost, queue]);
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
 * Each reveal waits for its sentence having been read. Only when the copilot
 * did not speak it -- no API key, no network, no audio device, all of which
 * answer in milliseconds -- does the line hold a drawn beat of roughly two
 * seconds (`fallbackMs`), so a silent report still keeps a deliberate cadence
 * instead of dumping all four findings in a single frame, and a spoken one is
 * never padded beyond its own sentence.
 *
 * The script carries its intro and outro, drawn with the findings when the
 * victim assessment arrived, so all six lines were synthesized ahead and the
 * victim line is never read before there is a result or the wait has run out.
 *
 * Returns how many cards have been revealed, which is what the panel renders,
 * and the closing line as spoken, so the screen shows the same sentence.
 */
export function useForensicNarration(
  queue: NarrationQueue,
  active: boolean,
  script: ForensicScript | null
): { revealed: number; closing: string | null } {
  const [revealed, setRevealed] = useState(0);
  const [closing, setClosing] = useState<string | null>(null);
  const runRef = useRef(0);

  useEffect(() => {
    if (!active || !script || script.findings.length === 0) {
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
    const { intro, findings, outro } = script;
    setClosing(outro);

    queue.enqueue('inspection.intro', () => intro, {
      group: REPORT_GROUP,
      fallbackMs: findingGapMs(),
    });
    findings.forEach((finding, index) => {
      queue.enqueue(`inspection.point_${index + 1}`, () => finding.speech, {
        group: REPORT_GROUP,
        fallbackMs: findingGapMs(),
        onDone: () => {
          if (live()) setRevealed(index + 1);
        },
      });
    });
    queue.enqueue('inspection.outro', () => outro, { group: REPORT_GROUP, fallbackMs: findingGapMs() });

    return () => {
      runRef.current = token + 1;
      queue.resetGroup(REPORT_GROUP);
    };
  }, [active, script, queue]);

  return { revealed, closing };
}
