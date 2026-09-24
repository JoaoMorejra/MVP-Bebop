export interface MilestoneMessage {
  key: string;
  payload: Record<string, unknown>;
  /** Epoch milliseconds at which the main process saw it. */
  at: number;
  /** `mission` for lines from mission.py, `station` for the two the main process raises. */
  source: 'mission' | 'station';
}

export declare const MILESTONE_LINE: RegExp;
export declare const COUNTDOWN_CALL_SEC: number;

export interface MilestoneParser {
  push(chunk: string | Uint8Array): void;
  flush(): void;
}

export declare function createMilestoneParser(
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): MilestoneParser;

export declare function scheduleScriptMilestones(
  countdownSec: number,
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): () => void;
