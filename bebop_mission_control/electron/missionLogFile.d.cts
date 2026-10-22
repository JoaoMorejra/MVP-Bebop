export declare const KEEP_MISSION_LOGS: number;
export declare function missionLogName(date: Date): string;
export declare function rotateMissionLogs(dir: string, keep?: number, fsImpl?: unknown): string[];
export interface MissionLogFile {
  open(): string | null;
  write(type: string, text: string): void;
  close(): void;
  path(): string | null;
}
export declare function createMissionLogFile(options: {
  directory: () => string;
  now?: () => Date;
  fsImpl?: unknown;
  warn?: (text: string) => void;
}): MissionLogFile;
