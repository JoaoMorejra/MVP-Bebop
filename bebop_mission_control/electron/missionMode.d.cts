export declare const MISSION_MODES: readonly ['simple', 'intermediate', 'advanced'];
export declare function normalizeLaunchMode(
  doc: unknown
):
  | { ok: true; doc: Record<string, unknown>; mode: string; defaulted: boolean }
  | { ok: false; error: string };
