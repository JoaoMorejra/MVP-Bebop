import { describe, expect, it } from 'vitest';
import { completeDocument, factoryDocument } from './__fixtures__/missionDocument';
import {
  ACTIVE_MODE_KEY,
  MODE_OWNED_PATHS,
  applyOwnedPaths,
  migrateLegacyKeys,
  presetKey,
  readStoredDoc,
  switchModeDocument,
  workingKey,
  writeStoredDoc,
} from './parameterModes';
import { getPath, setPath } from './paths';

class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}

describe('keys', () => {
  it('namespaces the working document and the favourite by mode', () => {
    expect(workingKey('simple')).toBe('bmg.working-parameters.simple');
    expect(presetKey('intermediate')).toBe('bmg.operator-preset.intermediate');
    expect(ACTIVE_MODE_KEY).toBe('bmg.active-mode');
  });
});

describe('applyOwnedPaths', () => {
  it('moves the sheet and advanced fields and nothing else', () => {
    const target = setPath(completeDocument(), 'lateral_pid.kp', 0.42);
    const source = setPath(setPath(completeDocument(), 'kinematics.target_altitude_m', 0.7), 'lateral_pid.kp', 9);
    const next = applyOwnedPaths(target, source);
    expect(getPath(next, 'kinematics.target_altitude_m')).toBe(0.7);
    expect(getPath(next, 'lateral_pid.kp')).toBe(0.42);
  });

  it('skips a path the source does not hold', () => {
    const next = applyOwnedPaths(completeDocument(), { kinematics: {} });
    expect(getPath(next, 'kinematics.target_altitude_m')).toBe(getPath(completeDocument(), 'kinematics.target_altitude_m'));
  });

  it('owns every sheet field and its mirror', () => {
    expect(MODE_OWNED_PATHS).toContain('kinematics.target_altitude_m');
    expect(MODE_OWNED_PATHS).toContain('rtl.reverse_cruise_velocity');
    expect(MODE_OWNED_PATHS).toContain('rtl.target_aruco_id');
    expect(MODE_OWNED_PATHS).not.toContain('lateral_pid.kp');
    expect(MODE_OWNED_PATHS).not.toContain('no_fly');
    expect(MODE_OWNED_PATHS).not.toContain('mission_mode');
  });
});

describe('storage', () => {
  it('round-trips a document', () => {
    const storage = new MemoryStorage();
    expect(writeStoredDoc(storage, workingKey('simple'), { a: 1 })).toBe(true);
    expect(readStoredDoc(storage, workingKey('simple'))).toEqual({ a: 1 });
  });

  it('reads a missing, corrupt or non-object entry as nothing', () => {
    const storage = new MemoryStorage();
    expect(readStoredDoc(storage, 'k')).toBeNull();
    storage.setItem('k', '{not json');
    expect(readStoredDoc(storage, 'k')).toBeNull();
    storage.setItem('k', '[1,2]');
    expect(readStoredDoc(storage, 'k')).toBeNull();
    expect(readStoredDoc(null, 'k')).toBeNull();
  });

  it('survives a storage that throws', () => {
    const hostile = {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('quota'); },
    };
    expect(readStoredDoc(hostile, 'k')).toBeNull();
    expect(writeStoredDoc(hostile, 'k', {})).toBe(false);
  });
});

describe('migrateLegacyKeys', () => {
  it('moves the single v2 document and favourite to the advanced mode, keeping the originals', () => {
    const storage = new MemoryStorage();
    storage.setItem('bmg.working-parameters.v2', JSON.stringify({ w: 1 }));
    storage.setItem('bmg.operator-preset.v2', JSON.stringify({ p: 1 }));
    migrateLegacyKeys(storage);
    expect(readStoredDoc(storage, workingKey('advanced'))).toEqual({ w: 1 });
    expect(readStoredDoc(storage, presetKey('advanced'))).toEqual({ p: 1 });
    expect(storage.getItem('bmg.working-parameters.v2')).not.toBeNull();
    expect(readStoredDoc(storage, workingKey('simple'))).toBeNull();
  });

  it('never overwrites what the advanced mode already holds', () => {
    const storage = new MemoryStorage();
    storage.setItem('bmg.working-parameters.v2', JSON.stringify({ w: 1 }));
    storage.setItem(workingKey('advanced'), JSON.stringify({ w: 2 }));
    migrateLegacyKeys(storage);
    expect(readStoredDoc(storage, workingKey('advanced'))).toEqual({ w: 2 });
  });
});

describe('switchModeDocument', () => {
  const factory = factoryDocument();
  const advanced = setPath(setPath(completeDocument(), 'mission_mode', 'advanced'), 'kinematics.target_altitude_m', 2.4);

  it('uses a copy of the current values the first time a mode is entered', () => {
    const { doc, stash, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'intermediate', stored: null, factory });
    expect(usedStored).toBe(false);
    expect(getPath(doc, 'mission_mode')).toBe('intermediate');
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(2.4);
  });

  it('restores what the target mode last used', () => {
    const stored = setPath(completeDocument(), 'kinematics.target_altitude_m', 0.8);
    const { doc, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored, factory });
    expect(usedStored).toBe(true);
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(0.8);
    expect(getPath(doc, 'mission_mode')).toBe('simple');
  });

  it('squeezes a copied altitude into the simple range and pins the margin', () => {
    const { doc } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(1);
    expect(getPath(doc, 'kinematics.altitude_ceiling_margin_m')).toBe(getPath(factory, 'kinematics.altitude_ceiling_margin_m'));
  });

  it('takes the pad from the document being switched when it squeezes the altitude', () => {
    const padded = setPath(advanced, 'platform.height_m', 1);
    const { doc } = switchModeDocument({ working: padded, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2);
    const low = setPath(padded, 'kinematics.target_altitude_m', 1.2);
    const moved = switchModeDocument({ working: low, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(moved.doc, 'kinematics.target_altitude_m')).toBe(1.7);
  });

  it('keeps the fields the modes do not own', () => {
    const working = setPath(advanced, 'lateral_pid.kp', 0.77);
    const { doc } = switchModeDocument({ working, from: 'advanced', to: 'simple', stored: completeDocument(), factory });
    expect(getPath(doc, 'lateral_pid.kp')).toBe(0.77);
  });

  it('stashes the leaving mode with its unsaved edits', () => {
    const { stash } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null, factory });
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(2.4);
    expect(getPath(stash, 'mission_mode')).toBe('advanced');
  });

  it('keeps the return cruise coupled to the forward cruise after a restore', () => {
    const stored = setPath(setPath(completeDocument(), 'kinematics.forward_cruise_velocity', 0.3), 'rtl.reverse_cruise_velocity', -0.1);
    const { doc } = switchModeDocument({ working: advanced, from: 'advanced', to: 'intermediate', stored, factory });
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.3);
  });
});
