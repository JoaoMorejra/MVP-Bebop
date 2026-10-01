export interface MilestoneMessage {
  /** A narration milestone, or an alert the station speaks ahead of narration. */
  kind: 'milestone' | 'alert';
  key: string;
  payload: Record<string, unknown>;
  /** Epoch milliseconds at which the main process saw it. */
  at: number;
  /** `mission` for lines from mission.py, `station` for the two the main process raises. */
  source: 'mission' | 'station';
}

export declare const MILESTONE_LINE: RegExp;
export declare const ALERT_LINE: RegExp;
export declare const COUNTDOWN_CALL_SEC: number;
export declare const STEP_LINE: RegExp;

export interface LineBuffer {
  push(chunk: string | Uint8Array): void;
  flush(): void;
}

export declare function createLineBuffer(onLine: (line: string) => void): LineBuffer;

export declare function createStepMarkerParser(emit: (stepNumber: number) => void): LineBuffer;

export interface MilestoneParser {
  push(chunk: string | Uint8Array): void;
  flush(): void;
}

export declare function createMilestoneParser(
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): MilestoneParser;

export declare function emitLaunchMilestone(
  countdownSec: number,
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): void;

export declare function scheduleCountdownTicks(
  countdownSec: number,
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): () => void;

export declare function scheduleScriptMilestones(
  countdownSec: number,
  emit: (message: MilestoneMessage) => void,
  now?: () => number
): () => void;
