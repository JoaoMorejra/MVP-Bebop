import type { ChildProcess } from 'node:child_process';

export declare const STANDBY_READY_LINE: string;
export declare const CRITICAL_PATHS: string[];
export declare function standbyKey(doc: unknown, driverRunning: boolean): string;
export interface StandbyManager {
  ensure(doc: unknown, driverRunning: boolean): void;
  take(doc: unknown, driverRunning: boolean, launchAtMs: number): ChildProcess | null;
  /** Why `take` gives nothing for this launch; null when it would hand one over. */
  unavailableReason(doc: unknown, driverRunning: boolean): string | null;
  pause(): void;
  /** Re-prepare the standby for the same document and driver state, e.g. after the link interface changed. */
  recycle(reason: string, startedBefore?: number): void;
  /** Whether `pid` is the prepared standby or a released one that has not exited yet. */
  isStandby(pid: number | undefined): boolean;
  /** Whether the last standby exited 78 and none is prepared until the document changes. */
  isRefused(): boolean;
  dispose(): void;
}
export declare function createStandbyManager(options: {
  spawnStandby: (doc: unknown) => ChildProcess;
  log: (text: string) => void;
  retryMs?: number;
  releaseGraceMs?: number;
}): StandbyManager;
