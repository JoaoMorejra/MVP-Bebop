export interface DriverSessionFacts {
  driverAlive: boolean;
  missionRunning: boolean;
  /** Last non-null `flying_state` the aircraft reported. */
  lastFlyingState: number | null | undefined;
  telemetryFresh: boolean;
  /** `odom_age_sec` of the telemetry bridge; null before its first sample. */
  odomAgeSec: number | null | undefined;
  /** Since this station spawned the driver; null when it did not. */
  driverAgeMs: number | null | undefined;
  /** Since the telemetry bridge was last started. */
  bridgeAgeMs: number | null | undefined;
}

export interface DriverSessionVerdict {
  stale: boolean;
  /** Short reason, in Portuguese. */
  reason: string;
}

export declare const AIRBORNE_FLYING_STATES: ReadonlySet<number>;
export declare const DRIVER_SESSION_GRACE_MS: number;
export declare const ODOM_SILENT_SEC: number;
export declare function staleDriverSession(facts: DriverSessionFacts): DriverSessionVerdict;
