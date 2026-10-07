export interface LaunchReadinessInput {
  benchMode: boolean;
  noFly?: boolean;
  configValid: boolean;
  driverRunning: boolean;
  orphanPids?: Array<number | string>;
  statesLink?: boolean;
  telemetryAgeMs?: number | null;
  odomFresh?: boolean;
  videoFresh?: boolean;
  missingTopics?: string[];
  flyingState?: number | null;
  batteryKnown?: boolean;
  batteryPct?: number | null;
  batteryFailsafeThreshold?: number;
  magnetoRequired?: boolean;
  commandBridgeReady?: boolean;
  ros2CliAvailable?: boolean;
  voiceReady?: boolean;
  standbyReady?: boolean;
}

export interface LaunchReadinessResult {
  ready: boolean;
  blockedReason: string | null;
  warning: string | null;
}

export function evaluateLaunchReadiness(input: LaunchReadinessInput): LaunchReadinessResult;
