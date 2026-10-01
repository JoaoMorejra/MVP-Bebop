type Run = (cmd: string, args: string[], opts?: object) => Promise<{ ok: boolean }>;

export declare const ROS_PROCESS_PATTERN: RegExp;
export declare function restartRos2Daemon(run: Run): Promise<boolean>;
export declare function countFastddsSegments(): number;
export declare function stationRosProcesses(): number[];
export declare function cleanOrphanShm(deps: {
  rosProcesses?: () => number[];
  countSegments?: () => number;
  run: Run;
}): Promise<{ skipped: true; alive: number[] } | { skipped: false; before: number; after: number; removed: number }>;
export declare function createSsidChangeDetector(): (ssid: unknown) => boolean;
