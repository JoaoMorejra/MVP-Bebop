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
  private readonly items: Array<{ label: string; compose: () => string }> = [];
  private draining = false;

  constructor(private readonly speak: (text: string) => Promise<boolean>) {}

  /**
   * Queue one line. `compose` runs when the line is reached, not now, so the
   * phrase reflects the latest values and a line dropped by `reset` never
   * consumes a variant from the cross-flight history.
   */
  enqueue(label: string, compose: () => string): void {
    this.items.push({ label, compose });
    void this.drain();
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
        try {
          await this.speak(text);
        } catch {
          // A voice that fails is treated as a line not heard; the next one
          // still gets its turn.
        }
      }
    } finally {
      this.draining = false;
    }
  }
}
