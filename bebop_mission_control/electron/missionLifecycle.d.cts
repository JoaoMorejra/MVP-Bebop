import type { EventEmitter } from 'node:events';

export declare const KILL_BACKSTOP_MS: number;
export declare const LAND_PUB: string;
export declare const STOP_PUB: string;

export interface SignallableChild extends EventEmitter {
  kill(signal: string): boolean;
}

export declare function signalOnce(
  child: SignallableChild,
  options: { grace: number; log?: (text: string) => void; onSettle?: () => void }
): Promise<boolean>;

export declare function singleFlight<A extends unknown[], T>(
  fn: (...args: A) => Promise<T> | T
): (...args: A) => Promise<T>;

export declare function publishStopThenLand(
  execFn: (command: string, options: object, done: (err: Error | null) => void) => void,
  env: Record<string, string | undefined>,
  timeoutMs: number
): Promise<{ stop: Error | null; land: Error | null }>;

export interface BridgeGate {
  markReady(child: object | null | undefined): void;
  isReady(child: object | null | undefined): boolean;
}

export declare function createBridgeGate(): BridgeGate;

export declare function waitFor(
  predicate: () => boolean,
  timeoutMs: number,
  pollMs: number
): Promise<boolean>;

export declare function createShutdownSequence(hooks: {
  stopMission: () => Promise<unknown>;
  isAirborne: () => boolean;
  commandLand: () => void;
  stopServices: () => void;
  groundWaitMs?: number;
  pollMs?: number;
}): () => Promise<{ landCommanded: boolean; grounded: boolean }>;
