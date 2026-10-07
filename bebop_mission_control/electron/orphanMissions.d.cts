export declare const STANDBY_FLAG: string;

export interface OrphanCandidate {
  pid: number;
  standby: boolean;
}

export declare function parseOrphanCandidates(
  pgrepOutput: string,
  options: {
    ownPid: number | null;
    missionPid: number | null;
    isTracked: (pid: number) => boolean;
  }
): OrphanCandidate[];

export interface OrphanReaper {
  sweep(): { blocking: number[] };
}

export declare function createOrphanReaper(options: {
  listCandidates: () => OrphanCandidate[];
  signal: (pid: number, sig: string) => void;
  log: (text: string) => void;
  graceMs?: number;
  now?: () => number;
}): OrphanReaper;
