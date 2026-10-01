import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readParameterFile, writeJsonAtomic } from '../../electron/parameterStore.cjs';

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'bmg-params-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('readParameterFile', () => {
  it('reads a valid document', () => {
    const file = join(dir, 'mission_config.json');
    writeFileSync(file, JSON.stringify({ kinematics: { target_altitude_m: 1.8 } }));
    expect(readParameterFile(file)).toEqual({ success: true, params: { kinematics: { target_altitude_m: 1.8 } } });
  });

  it('reports a missing file as missing, not as an error', () => {
    expect(readParameterFile(join(dir, 'absent.json'))).toEqual({ success: false, missing: true });
  });

  it('refuses a corrupt document instead of substituting defaults', () => {
    const file = join(dir, 'mission_config.json');
    writeFileSync(file, '{"kinematics": {');
    const result = readParameterFile(file);
    expect(result.success).toBe(false);
    expect(result.missing).toBeUndefined();
    expect(result.error).toMatch(/mission_config\.json/);
  });

  it('refuses a document that is not an object', () => {
    const file = join(dir, 'mission_config.json');
    writeFileSync(file, '[1, 2]');
    expect(readParameterFile(file).success).toBe(false);
  });
});

describe('writeJsonAtomic', () => {
  it('replaces the file whole and leaves no temporary behind', () => {
    const file = join(dir, 'mission_config.json');
    writeFileSync(file, '{"old": true}');
    writeJsonAtomic(file, { new: true });
    expect(JSON.parse(readFileSync(file, 'utf-8'))).toEqual({ new: true });
    expect(readdirSync(dir)).toEqual(['mission_config.json']);
  });

  it('gives every write its own temporary', () => {
    const file = join(dir, 'mission_config.json');
    const seen = new Set<string>();
    for (let i = 0; i < 20; i += 1) seen.add(writeJsonAtomic(file, { i }));
    expect(seen.size).toBe(20);
    expect(JSON.parse(readFileSync(file, 'utf-8'))).toEqual({ i: 19 });
  });

  it('leaves the old document in place when the write fails', () => {
    const file = join(dir, 'mission_config.json');
    writeFileSync(file, '{"old": true}');
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => writeJsonAtomic(file, circular)).toThrow();
    expect(readFileSync(file, 'utf-8')).toBe('{"old": true}');
    expect(readdirSync(dir)).toEqual(['mission_config.json']);
  });
});
