export declare const MISSION_MODES: readonly ['simple', 'advanced'];
export declare const INTERMEDIATE_REMOVED: string;
export declare function normalizeLaunchMode(
  doc: unknown
):
  | { ok: true; doc: Record<string, unknown>; mode: string; defaulted: boolean }
  | { ok: false; error: string };
