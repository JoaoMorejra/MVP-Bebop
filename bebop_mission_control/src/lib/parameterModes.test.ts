import { describe, expect, it } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import {
  ACTIVE_MODE_KEY,
  MODE_OWNED_PATHS,
  applyOwnedPaths,
  migrateLegacyIntermediate,
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
    expect(presetKey('advanced')).toBe('bmg.operator-preset.advanced');
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
  const advanced = setPath(setPath(completeDocument(), 'mission_mode', 'advanced'), 'kinematics.target_altitude_m', 1.6);

  it('uses a copy of the current values the first time a mode is entered', () => {
    const { doc, stash, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null });
    expect(usedStored).toBe(false);
    expect(getPath(doc, 'mission_mode')).toBe('simple');
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(1.6);
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(1.6);
  });

  it('restores what the target mode last used', () => {
    const stored = setPath(completeDocument(), 'kinematics.target_altitude_m', 0.8);
    const { doc, usedStored } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored });
    expect(usedStored).toBe(true);
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(0.8);
    expect(getPath(doc, 'mission_mode')).toBe('simple');
  });

  it('moves a copied altitude into the envelope and keeps the margin the operator set', () => {
    const margin = setPath(setPath(advanced, 'kinematics.altitude_ceiling_margin_m', 0.35), 'kinematics.target_altitude_m', 2.4);
    const { doc } = switchModeDocument({ working: margin, from: 'advanced', to: 'simple', stored: null });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2);
    expect(getPath(doc, 'kinematics.altitude_ceiling_margin_m')).toBe(0.35);
  });

  it('takes the pad from the document being switched when it moves the altitude', () => {
    const padded = setPath(setPath(advanced, 'platform.height_m', 1), 'kinematics.target_altitude_m', 2.4);
    const { doc } = switchModeDocument({ working: padded, from: 'advanced', to: 'simple', stored: null });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2);
    const low = setPath(padded, 'kinematics.target_altitude_m', 1.2);
    const moved = switchModeDocument({ working: low, from: 'advanced', to: 'simple', stored: null });
    expect(getPath(moved.doc, 'kinematics.target_altitude_m')).toBe(1.3);
  });

  it('leaves an altitude inside the envelope untouched in every mode (BUG 1.1)', () => {
    const doc18 = setPath(setPath(advanced, 'platform.height_m', 1), 'kinematics.target_altitude_m', 1.8);
    const { doc } = switchModeDocument({ working: doc18, from: 'advanced', to: 'simple', stored: null });
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(1.8);
  });

  it('keeps the fields the modes do not own', () => {
    const working = setPath(advanced, 'lateral_pid.kp', 0.77);
    const { doc } = switchModeDocument({ working, from: 'advanced', to: 'simple', stored: completeDocument() });
    expect(getPath(doc, 'lateral_pid.kp')).toBe(0.77);
  });

  it('stashes the leaving mode with its unsaved edits', () => {
    const { stash } = switchModeDocument({ working: advanced, from: 'advanced', to: 'simple', stored: null });
    expect(getPath(stash, 'kinematics.target_altitude_m')).toBe(1.6);
    expect(getPath(stash, 'mission_mode')).toBe('advanced');
  });

  it('keeps the return cruise coupled to the forward cruise after a restore', () => {
    const stored = setPath(setPath(completeDocument(), 'kinematics.forward_cruise_velocity', 0.3), 'rtl.reverse_cruise_velocity', -0.1);
    const { doc } = switchModeDocument({ working: setPath(advanced, 'mission_mode', 'simple'), from: 'simple', to: 'advanced', stored });
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.3);
  });
});

describe('migrateLegacyIntermediate', () => {
  it('moves an active Intermediate mode to Advanced without the return and drops its keys', () => {
    const storage = new MemoryStorage();
    storage.setItem(ACTIVE_MODE_KEY, 'intermediate');
    storage.setItem(workingKey('advanced'), JSON.stringify({ mission_mode: 'advanced', kinematics: { target_altitude_m: 1.6 } }));
    storage.setItem('bmg.working-parameters.intermediate', JSON.stringify({ kinematics: { target_altitude_m: 1.4 } }));
    storage.setItem('bmg.operator-preset.intermediate', JSON.stringify({ kinematics: { target_altitude_m: 1.5 } }));
    migrateLegacyIntermediate(storage);
    expect(storage.getItem(ACTIVE_MODE_KEY)).toBe('advanced');
    const advanced = readStoredDoc(storage, workingKey('advanced'));
    expect(getPath(advanced, 'rtl.enabled')).toBe(false);
    expect(getPath(advanced, 'kinematics.target_altitude_m')).toBe(1.6);
    expect(storage.getItem('bmg.working-parameters.intermediate')).toBeNull();
    expect(storage.getItem('bmg.operator-preset.intermediate')).toBeNull();
  });

  it('starts the Advanced document from the Intermediate one when Advanced has none', () => {
    const storage = new MemoryStorage();
    storage.setItem(ACTIVE_MODE_KEY, ' Intermediate ');
    storage.setItem('bmg.working-parameters.intermediate', JSON.stringify({ kinematics: { target_altitude_m: 1.4 } }));
    migrateLegacyIntermediate(storage);
    const advanced = readStoredDoc(storage, workingKey('advanced'));
    expect(getPath(advanced, 'mission_mode')).toBe('advanced');
    expect(getPath(advanced, 'rtl.enabled')).toBe(false);
    expect(getPath(advanced, 'kinematics.target_altitude_m')).toBe(1.4);
  });

  it('is idempotent and leaves a store of the current modes alone', () => {
    const storage = new MemoryStorage();
    storage.setItem(ACTIVE_MODE_KEY, 'simple');
    storage.setItem(workingKey('advanced'), JSON.stringify({ rtl: { enabled: true } }));
    migrateLegacyIntermediate(storage);
    migrateLegacyIntermediate(storage);
    expect(storage.getItem(ACTIVE_MODE_KEY)).toBe('simple');
    expect(getPath(readStoredDoc(storage, workingKey('advanced')), 'rtl.enabled')).toBe(true);
  });

  it('never throws on a store that refuses every access', () => {
    const broken = {
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('denied'); },
      removeItem: () => { throw new Error('denied'); },
    };
    expect(() => migrateLegacyIntermediate(broken)).not.toThrow();
  });
});
