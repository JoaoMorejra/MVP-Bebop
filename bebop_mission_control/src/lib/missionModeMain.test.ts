import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { MISSION_MODES, normalizeLaunchMode } = require('../../electron/missionMode.cjs') as {
  MISSION_MODES: readonly string[];
  normalizeLaunchMode: (doc: unknown) =>
    | { ok: true; doc: Record<string, unknown>; mode: string; defaulted: boolean }
    | { ok: false; error: string };
};

describe('normalizeLaunchMode', () => {
  it('lists the three profiles', () => {
    expect(MISSION_MODES).toEqual(['simple', 'intermediate', 'advanced']);
  });

  it.each([
    ['simple', 'simple'],
    ['Intermediate', 'intermediate'],
    [' ADVANCED ', 'advanced'],
  ])('normalises %j to %s and keeps the rest of the document', (raw, expected) => {
    const result = normalizeLaunchMode({ mission_mode: raw, kinematics: { countdown_sec: 3 } });
    expect(result).toMatchObject({ ok: true, mode: expected, defaulted: false });
    if (result.ok) {
      expect(result.doc.mission_mode).toBe(expected);
      expect(result.doc.kinematics).toEqual({ countdown_sec: 3 });
    }
  });

  it('says so when the document carries no mode and runs the full mission', () => {
    for (const doc of [{}, { mission_mode: '' }, { mission_mode: null }]) {
      const result = normalizeLaunchMode(doc);
      expect(result).toMatchObject({ ok: true, mode: 'advanced', defaulted: true });
      if (result.ok) expect(result.doc.mission_mode).toBe('advanced');
    }
  });

  it.each(['expert', 3, ['simple'], true])('refuses %j instead of defaulting', (raw) => {
    const result = normalizeLaunchMode({ mission_mode: raw });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain('mission_mode');
  });

  it('does not mutate its input', () => {
    const doc = { mission_mode: 'Simple' };
    normalizeLaunchMode(doc);
    expect(doc.mission_mode).toBe('Simple');
  });
});
