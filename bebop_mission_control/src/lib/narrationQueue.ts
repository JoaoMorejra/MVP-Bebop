import type { AnnouncePriority } from '../types/bmg';

export interface EnqueueOptions {
  /** Lines that belong together and are dropped together (`resetGroup`). */
  group?: string;
  /**
   * The least time the item holds the queue, in milliseconds. It ends at the
   * later of its sentence having been heard and this beat, so a copilot that
   * answers at once still paces what hangs off `onDone`.
   */
  minMs?: number;
  /** Called once the item ends, with whether the sentence was heard. Not called for a cut item. */
  onDone?: (heard: boolean) => void;
}

interface Item {
  label: string;
  compose: () => string;
  priority: AnnouncePriority;
  /** Raised with `preempt`: a failure, abort or failsafe, not narration. */
  alert: boolean;
  group: string | null;
  minMs: number;
  onDone?: (heard: boolean) => void;
  /** Cut while playing; its `onDone` must not run. */
  cancelled: boolean;
}

/**
 * First-in, first-out narration: one line at a time, each only once the
 * previous one has been heard.
 *
 * The flight does not wait for the voice (spec 2026-09-24, section 3.2). The
 * mission keeps its own pace, and a drone that confirms a target before the
 * scan call has finished reading will report `mission.target_found` while
 * `mission.scan_start` is still in the operator's ear. Speaking each milestone
 * as it arrives would talk over the previous line or, with a player that
 * drops what it cannot start, lose it. Queueing them and draining on
 * `speak`'s resolution keeps every milestone, in the order the aircraft
 * crossed them, however far the flight has run ahead.
 *
 * `speak` is expected to resolve when the line has been heard or is known
 * never to be (`Copilot.say`, which is bounded by its own ceiling), so a
 * silent copilot drains the queue immediately rather than stalling it.
 */
export class NarrationQueue {
  private readonly items: Item[] = [];
  private draining = false;
  /** The line being spoken right now, if any. */
  private current: Item | null = null;
  /** Ends the current item's beat early when the item is cut. */
  private releaseBeat: (() => void) | null = null;

  /**
   * @param speak Plays one line; resolves when it has been heard or never will be.
   * @param interrupt Cuts the line currently playing so that `speak` resolves
   *   at once (`Copilot.cancel`). Used only by {@link preempt}.
   */
  constructor(
    private readonly speak: (text: string, priority?: AnnouncePriority) => Promise<boolean>,
    private readonly interrupt: () => void = () => undefined
  ) {}

  /**
   * Queue one line. `compose` runs when the line is reached, not now, so the
   * phrase reflects the latest values and a line dropped by `reset` never
   * consumes a variant from the cross-flight history.
   */
  enqueue(label: string, compose: () => string, options: EnqueueOptions = {}): void {
    this.items.push({
      label,
      compose,
      priority: 'NORMAL',
      alert: false,
      group: options.group ?? null,
      minMs: Math.max(0, options.minMs ?? 0),
      onDone: options.onDone,
      cancelled: false,
    });
    void this.drain();
  }

  /**
   * Speak an alert ahead of all narration.
   *
   * The station is the system's only voice, so a failure the mission reports
   * cannot wait behind the flight script it has just made obsolete: pending
   * narration is dropped, the narration line playing now is cut, and the alert
   * is next. Alerts never cut or drop each other; a second one waits for the
   * first, so "step failed" followed by "aborting" is heard in full, in order.
   */
  preempt(label: string, compose: () => string, priority: AnnouncePriority = 'URGENT'): void {
    const alerts = this.items.filter((item) => item.alert);
    this.items.length = 0;
    this.items.push(...alerts, {
      label,
      compose,
      priority,
      alert: true,
      group: null,
      minMs: 0,
      cancelled: false,
    });
    if (this.current && !this.current.alert) this.cutCurrent();
    void this.drain();
  }

  /**
   * Drop every line of `group`, and cut the one playing if it is one of them.
   *
   * How a presentation that is torn down (the report dismissed, a new flight)
   * leaves the queue: its remaining lines go, its callbacks never fire, and
   * lines of other groups -- the flight's own, an alert -- are untouched.
   */
  resetGroup(group: string): void {
    const kept = this.items.filter((item) => item.group !== group);
    this.items.length = 0;
    this.items.push(...kept);
    if (this.current && this.current.group === group) this.cutCurrent();
  }

  private cutCurrent(): void {
    if (!this.current) return;
    this.current.cancelled = true;
    this.releaseBeat?.();
    this.interrupt();
  }

  /** Drop every line not yet started. The one being spoken, if any, finishes. */
  reset(): void {
    this.items.length = 0;
  }

  /** Lines queued and not yet started. */
  get pending(): number {
    return this.items.length;
  }

  /** Whether a line is being spoken or waiting to be. */
  get busy(): boolean {
    return this.draining;
  }

  /** Labels of the lines still waiting, in speaking order. */
  labels(): string[] {
    return this.items.map((item) => item.label);
  }

  private async drain(): Promise<void> {
    if (this.draining) return;
    this.draining = true;
    try {
      for (let item = this.items.shift(); item; item = this.items.shift()) {
        let text: string;
        try {
          text = item.compose();
        } catch {
          continue;
        }
        if (!text.trim()) continue;
        this.current = item;
        let heard = false;
        try {
          heard = (await Promise.all([this.say(text, item.priority), this.beat(item.minMs)]))[0];
        } finally {
          this.current = null;
          this.releaseBeat = null;
        }
        if (!item.cancelled && item.onDone) {
          try {
            item.onDone(heard);
          } catch {
            // A presentation callback must not stop the voice.
          }
        }
      }
    } finally {
      this.draining = false;
    }
  }

  /** `speak`, with a voice that throws or rejects read as a line not heard. */
  private say(text: string, priority: AnnouncePriority): Promise<boolean> {
    try {
      return this.speak(text, priority).catch(() => false);
    } catch {
      return Promise.resolve(false);
    }
  }

  private beat(ms: number): Promise<void> {
    if (ms <= 0) return Promise.resolve();
    return new Promise<void>((resolve) => {
      const id = setTimeout(resolve, ms);
      this.releaseBeat = () => {
        clearTimeout(id);
        resolve();
      };
    });
  }
}
