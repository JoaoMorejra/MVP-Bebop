export declare const STOP_RESEND_STATES: ReadonlySet<number>;
export declare const LANDING_STATES: ReadonlySet<number>;

export interface LandStepInput {
  startedAt: number;
  lastBridgeResendAt: number;
  cliBackupSent: boolean;
  flyingState: number | null | undefined;
}

export interface LandStepDecision {
  phase: 'commanded' | 'landing' | 'landed' | 'unconfirmed';
  shouldResendBridge: boolean;
  shouldSendCliBackup: boolean;
  isDone: boolean;
}

export declare function evaluateLandStep(
  input: LandStepInput,
  now: number,
  timeoutMs?: number
): LandStepDecision;

export interface LandSupervisorState {
  startedAt: number;
  lastBridgeResendAt: number;
  cliBackupSent: boolean;
  lastPhase: string | null;
}

export interface LandSupervisor {
  start(): void;
  step(): void;
  stop(): void;
  isActive(): boolean;
  getState(): LandSupervisorState;
}

export declare function createLandSupervisor(options: {
  getFlyingState: () => number | null | undefined;
  sendBridgeLand?: () => void;
  sendCliBackup?: () => void;
  emitProgress: (progress: { phase: 'commanded' | 'landing' | 'landed' | 'unconfirmed'; t: number }) => void;
  recordLog?: (channel: string, entry: { type: string; text: string }) => void;
  intervalMs?: number;
  timeoutMs?: number;
  clock?: () => number;
}): LandSupervisor;
