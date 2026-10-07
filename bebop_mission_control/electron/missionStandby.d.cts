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
  isStandby(pid: number | undefined): boolean;
  dispose(): void;
}
export declare function createStandbyManager(options: {
  spawnStandby: (doc: unknown) => ChildProcess;
  log: (text: string) => void;
  retryMs?: number;
}): StandbyManager;
