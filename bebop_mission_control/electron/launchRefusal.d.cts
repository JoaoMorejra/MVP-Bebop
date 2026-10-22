export interface LaunchRefusal {
  /** Dotted path of the refused field, or `mission_mode`. */
  field: string;
  /** Short reason, in Portuguese. */
  reason: string;
}

export declare const EXIT_LAUNCH_REFUSED: 78;
export declare const REFUSED_LINE: RegExp;
export declare function refusalFromPayload(payload: unknown): LaunchRefusal | null;

export interface LaunchRefusalTracker {
  push(chunk: string | Uint8Array): void;
  flush(): void;
  refusal(code: number | null): LaunchRefusal | null;
}

export declare function createLaunchRefusalTracker(): LaunchRefusalTracker;
