import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '..');

/** The only modules that may read the mission mode of a document. */
const ALLOWED = new Set([
  'lib/missionMode.ts',
  'lib/parameterSchema.ts',
  'lib/launchDocument.ts',
  'lib/parameterConflicts.ts',
  'lib/parameterModes.ts',
  'lib/operatorPresets.ts',
  'lib/copilotPhrases.ts',
  'lib/batteryReturn.ts',
  'hooks/useMissionParameters.ts',
  'hooks/useModeEcho.ts',
  'hooks/useCopilot.ts',
  'components/preflight/ParameterSheet.tsx',
  'components/preflight/PreflightScreen.tsx',
  'components/preflight/MissionModeSelector.tsx',
  'components/cockpit/CockpitScreen.tsx',
  'components/cockpit/StageBar.tsx',
  'App.tsx',
]);

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return walk(full);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe('the mission mode is read only where the profile matters', () => {
  it('keeps the lock, abort, finish and failsafe rules free of the mode', () => {
    const offenders = walk(SRC)
      .map((file) => relative(SRC, file))
      .filter((file) => !ALLOWED.has(file))
      .filter((file) => /readMissionMode|parseMissionMode|mission_mode|mission\.mode|MissionMode\b/.test(readFileSync(join(SRC, file), 'utf-8')));
    expect(offenders).toEqual([]);
  });
});
