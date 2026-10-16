import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { completeDocument, factoryDocument, validDocument } from './__fixtures__/missionDocument';
import {
  BACKFILL_PATHS,
  REQUIRED_LAUNCH_NUMBERS,
  advancedFieldErrors,
  applySchemaFields,
  envelopeBlockers,
  fillMissing,
  launchBlockers,
  launchCountdownSec,
  normalizeForMode,
  reconcileCoupledPaths,
  setLinkedPath,
  SIMPLE_CEILING_MARGIN_M,
} from './launchDocument';
import {
  ADVANCED_PATHS,
  ALL_PARAMETERS,
  MARKER_DICTIONARIES,
  TRACKED_PATHS,
  isKnownMarkerDictionary,
  markerCount,
} from './parameterSchema';
import { activeConflicts } from './parameterConflicts';
import { getPath, setPath } from './paths';

describe('launchBlockers', () => {
  it('passes a document holding every required number', () => {
    expect(launchBlockers(validDocument())).toEqual([]);
  });

  it('names every required field that is not a finite number', () => {
    const doc = validDocument({
      'kinematics.target_altitude_m': null,
      'rtl.max_speed': 'fast',
      'vision.confidence_threshold': Number.NaN,
    });
    expect(launchBlockers(doc)).toEqual([
      'kinematics.target_altitude_m',
      'rtl.max_speed',
      'vision.confidence_threshold',
    ]);
  });

  it('blocks a missing document outright', () => {
    expect(launchBlockers(null)).toEqual([...REQUIRED_LAUNCH_NUMBERS, 'rtl.marker_dict']);
  });

  it('requires every number the operator edits and every one the old flags carried', () => {
    for (const spec of ALL_PARAMETERS) expect(REQUIRED_LAUNCH_NUMBERS).toContain(spec.path);
    for (const path of [
      'kinematics.hover_duration_sec',
      'rtl.max_speed',
      'rtl.arrival_radius_m',
      'vision.confidence_threshold',
      'kinematics.countdown_sec',
    ]) {
      expect(REQUIRED_LAUNCH_NUMBERS).toContain(path);
    }
  });
});

describe('launchCountdownSec', () => {
  it('takes the document literally, zero included', () => {
    expect(launchCountdownSec(completeDocument({ 'kinematics.countdown_sec': 0 }))).toBe(0);
    expect(launchCountdownSec(completeDocument({ 'kinematics.countdown_sec': 7.5 }))).toBe(7.5);
  });

  it('has no value for a missing or negative countdown', () => {
    expect(launchCountdownSec(completeDocument({ 'kinematics.countdown_sec': undefined }))).toBeNull();
    expect(launchCountdownSec(completeDocument({ 'kinematics.countdown_sec': -1 }))).toBeNull();
  });
});

describe('applySchemaFields', () => {
  it('copies only the schema fields and keeps PID, calibration and the arming mode', () => {
    const working = completeDocument({ no_fly: true, 'calibration.samples': 40 });
    let preset = completeDocument({ no_fly: false, 'lateral_pid.kp': 9, 'calibration.samples': 1 });
    ALL_PARAMETERS.forEach((spec, index) => {
      preset = setPath(preset, spec.path, 100 + index);
    });

    const applied = applySchemaFields(working, preset);
    ALL_PARAMETERS.forEach((spec, index) => expect(getPath(applied, spec.path)).toBe(100 + index));
    const forwardIndex = ALL_PARAMETERS.findIndex((spec) => spec.path === 'kinematics.forward_cruise_velocity');
    expect(getPath(applied, 'rtl.reverse_cruise_velocity')).toBe(-(100 + forwardIndex));
    expect(getPath(applied, 'no_fly')).toBe(true);
    expect(getPath(applied, 'lateral_pid.kp')).toBe(0.42);
    expect(getPath(applied, 'calibration.samples')).toBe(40);
  });

  it('leaves a field the source does not hold as it was', () => {
    const working = completeDocument();
    const applied = applySchemaFields(working, {});
    expect(applied).toEqual(working);
  });
});

describe('launchBlockers outside the envelope', () => {
  const valid = () => validDocument();

  it('passes a document inside every range', () => {
    expect(launchBlockers(valid())).toEqual([]);
  });

  it('names a sheet value outside its slider range', () => {
    const doc = setPath(setPath(valid(), 'gimbal.nadir_tilt_deg', 5), 'kinematics.forward_cruise_velocity', 2);
    expect(envelopeBlockers(doc)).toEqual(['kinematics.forward_cruise_velocity', 'gimbal.nadir_tilt_deg']);
    expect(launchBlockers(doc)).toEqual(expect.arrayContaining(['kinematics.forward_cruise_velocity', 'gimbal.nadir_tilt_deg']));
  });

  it.each([0, 12, -0.1])('refuses a detection confidence of %s', (value) => {
    expect(launchBlockers(setPath(valid(), 'vision.confidence_threshold', value))).toEqual(['vision.confidence_threshold']);
  });

  it.each([
    ['rtl.max_speed', 0],
    ['rtl.arrival_radius_m', -1],
    ['kinematics.hover_duration_sec', 0],
  ])('refuses a non-positive %s', (path, value) => {
    expect(launchBlockers(setPath(valid(), path, value))).toEqual([path]);
  });

  it('refuses the fixture document that reached the station store', () => {
    expect(launchBlockers(completeDocument()).length).toBeGreaterThan(0);
  });
});

const forwardSpec = ALL_PARAMETERS.find((spec) => spec.path === 'kinematics.forward_cruise_velocity')!;

describe('slider ranges', () => {
  it.each([
    ['kinematics.target_altitude_m', 0.1, 4.0, 0.1, 1],
    ['kinematics.altitude_ceiling_margin_m', 0.0, 2.0, 0.05, 2],
    ['kinematics.forward_cruise_velocity', 0.01, 0.6, 0.01, 2],
    ['gimbal.search_tilt_deg', -45, 0, 1, 0],
    ['gimbal.nadir_tilt_deg', -80, 0, 1, 0],
  ])('%s spans [%s, %s] in steps of %s', (path, min, max, step, precision) => {
    const spec = ALL_PARAMETERS.find((item) => item.path === path)!;
    expect([spec.min, spec.max, spec.step, spec.precision]).toEqual([min, max, step, precision]);
  });

  it.each([
    ['kinematics.target_altitude_m', 0.1],
    ['kinematics.target_altitude_m', 4.0],
    ['kinematics.altitude_ceiling_margin_m', 0.0],
    ['kinematics.altitude_ceiling_margin_m', 2.0],
    ['kinematics.forward_cruise_velocity', 0.01],
    ['kinematics.forward_cruise_velocity', 0.6],
    ['gimbal.search_tilt_deg', -45],
    ['gimbal.search_tilt_deg', 0],
    ['gimbal.nadir_tilt_deg', -80],
  ])('accepts %s at its inclusive limit %s', (path, value) => {
    const doc = reconcileCoupledPaths(setLinkedPath(validDocument(), path, value));
    expect(envelopeBlockers(doc)).toEqual([]);
  });

  it.each([
    ['kinematics.target_altitude_m', 0.09],
    ['kinematics.target_altitude_m', 4.01],
    ['kinematics.altitude_ceiling_margin_m', -0.01],
    ['kinematics.altitude_ceiling_margin_m', 2.01],
    ['kinematics.forward_cruise_velocity', 0.0],
    ['kinematics.forward_cruise_velocity', 0.61],
    ['gimbal.search_tilt_deg', -46],
    ['gimbal.search_tilt_deg', 1],
    ['gimbal.nadir_tilt_deg', -81],
    ['gimbal.nadir_tilt_deg', 1],
  ])('refuses %s at %s, outside its range', (path, value) => {
    expect(envelopeBlockers(setPath(validDocument(), path, value))).toContain(path);
    expect(launchBlockers(setPath(validDocument(), path, value))).toContain(path);
  });
});

describe('setLinkedPath', () => {
  it('writes the return cruise as the negated forward cruise', () => {
    const doc = setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.35);
    expect(getPath(doc, 'kinematics.forward_cruise_velocity')).toBe(0.35);
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.35);
  });

  it('rounds to the spec precision before mirroring, so both sides are exact', () => {
    const doc = setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.30000000000000004);
    expect(getPath(doc, 'kinematics.forward_cruise_velocity')).toBe(0.3);
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.3);
  });

  it('never writes a negative zero', () => {
    const doc = setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0);
    expect(Object.is(getPath(doc, 'rtl.reverse_cruise_velocity'), -0)).toBe(false);
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(0);
  });

  it('writes a path with no mirror as it is', () => {
    const doc = setLinkedPath(validDocument(), 'kinematics.target_altitude_m', 2.5);
    expect(getPath(doc, 'kinematics.target_altitude_m')).toBe(2.5);
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.2);
  });

  it('declares the return cruise as the mirror of the forward cruise', () => {
    expect(forwardSpec.mirrors).toEqual([{ path: 'rtl.reverse_cruise_velocity', sign: -1 }]);
  });
});

describe('reconcileCoupledPaths', () => {
  it('couples a document stored with the old independent return cruise', () => {
    const doc = reconcileCoupledPaths(validDocument({ 'rtl.reverse_cruise_velocity': -0.1 }));
    expect(getPath(doc, 'rtl.reverse_cruise_velocity')).toBe(-0.2);
  });

  it('lifts the horizontal speed ceiling to the cruise plus 0.05, rounded up to 0.05', () => {
    const doc = reconcileCoupledPaths(setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.35));
    expect(getPath(doc, 'kinematics.max_horizontal_speed')).toBe(0.4);
    const top = reconcileCoupledPaths(setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.6));
    expect(getPath(top, 'kinematics.max_horizontal_speed')).toBe(0.65);
    const odd = reconcileCoupledPaths(setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.33));
    expect(getPath(odd, 'kinematics.max_horizontal_speed')).toBe(0.4);
  });

  it('never lowers a ceiling the operator already has', () => {
    const doc = reconcileCoupledPaths(validDocument({ 'kinematics.max_horizontal_speed': 0.8 }));
    expect(getPath(doc, 'kinematics.max_horizontal_speed')).toBe(0.8);
    expect(getPath(reconcileCoupledPaths(validDocument()), 'kinematics.max_horizontal_speed')).toBe(0.3);
  });

  it('caps the lifted ceiling at 0.95', () => {
    const doc = reconcileCoupledPaths(validDocument({ 'kinematics.forward_cruise_velocity': 2 }));
    expect(getPath(doc, 'kinematics.max_horizontal_speed')).toBe(0.95);
  });

  it('is idempotent and leaves a coupled document as it was', () => {
    const once = reconcileCoupledPaths(validDocument({ 'kinematics.forward_cruise_velocity': 0.45 }));
    expect(reconcileCoupledPaths(once)).toEqual(once);
    const coupled = validDocument();
    expect(reconcileCoupledPaths(coupled)).toBe(coupled);
  });

  it('does not touch a document whose forward cruise is missing or not finite', () => {
    const missing = validDocument({ 'kinematics.forward_cruise_velocity': undefined, 'rtl.reverse_cruise_velocity': -0.1 });
    expect(reconcileCoupledPaths(missing)).toEqual(missing);
    const nan = validDocument({ 'kinematics.forward_cruise_velocity': Number.NaN });
    expect(getPath(reconcileCoupledPaths(nan), 'rtl.reverse_cruise_velocity')).toBe(-0.2);
  });
});

describe('launchBlockers on the coupled cruise and the speed ceiling', () => {
  it('requires the return cruise and the horizontal speed ceiling', () => {
    expect(REQUIRED_LAUNCH_NUMBERS).toContain('rtl.reverse_cruise_velocity');
    expect(REQUIRED_LAUNCH_NUMBERS).toContain('kinematics.max_horizontal_speed');
  });

  it.each([-0.6, -0.01])('accepts a return cruise of %s', (value) => {
    const doc = reconcileCoupledPaths(setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', -value));
    expect(launchBlockers(doc)).toEqual([]);
  });

  it.each([-0.61, 0, 0.2])('refuses a return cruise of %s', (value) => {
    expect(launchBlockers(validDocument({ 'rtl.reverse_cruise_velocity': value }))).toContain('rtl.reverse_cruise_velocity');
  });

  it('refuses a return cruise that is not the negated forward cruise', () => {
    expect(launchBlockers(validDocument({ 'rtl.reverse_cruise_velocity': -0.1 }))).toEqual([
      'rtl.reverse_cruise_velocity',
      'kinematics.forward_cruise_velocity',
    ]);
  });

  it.each([0.15, 1.0, Number.NaN])('refuses a horizontal speed ceiling of %s', (value) => {
    expect(launchBlockers(validDocument({ 'kinematics.max_horizontal_speed': value }))).toContain(
      'kinematics.max_horizontal_speed'
    );
  });
});

describe('advanced marker settings', () => {
  it('offers exactly the dictionaries the shared fixture lists', () => {
    const shared = JSON.parse(
      readFileSync(resolve(__dirname, '../../../test/fixtures/marker_dictionaries.json'), 'utf-8')
    ) as Array<{ name: string; markers: number }>;
    expect(MARKER_DICTIONARIES).toEqual(shared);
  });

  it('tracks the advanced fields outside the sheet groups', () => {
    expect([...ADVANCED_PATHS]).toEqual([
      'rtl.target_aruco_id',
      'rtl.marker_dict',
      'rtl.tag_size',
      'rtl.centering_timeout_sec',
    ]);
    for (const path of ADVANCED_PATHS) {
      expect(TRACKED_PATHS).toContain(path);
      expect(ALL_PARAMETERS.map((spec) => spec.path)).not.toContain(path);
    }
    expect(REQUIRED_LAUNCH_NUMBERS).toContain('rtl.target_aruco_id');
    expect(REQUIRED_LAUNCH_NUMBERS).toContain('rtl.tag_size');
    expect(REQUIRED_LAUNCH_NUMBERS).toContain('rtl.centering_timeout_sec');
    expect(REQUIRED_LAUNCH_NUMBERS).not.toContain('rtl.marker_dict');
  });

  it('passes the factory pad marker', () => {
    expect(launchBlockers(validDocument())).toEqual([]);
  });

  it.each([0, 586])('accepts ID %s in DICT_APRILTAG_36h11', (id) => {
    expect(launchBlockers(validDocument({ 'rtl.target_aruco_id': id }))).toEqual([]);
  });

  it.each([587, -1, 1.5, '8'])('refuses ID %s in DICT_APRILTAG_36h11', (id) => {
    expect(launchBlockers(validDocument({ 'rtl.target_aruco_id': id }))).toContain('rtl.target_aruco_id');
  });

  it('bounds the ID by the dictionary chosen, without correcting it', () => {
    const doc = validDocument({ 'rtl.marker_dict': 'DICT_4X4_50', 'rtl.target_aruco_id': 50 });
    expect(launchBlockers(doc)).toEqual(['rtl.target_aruco_id', 'rtl.marker_dict']);
    expect(getPath(doc, 'rtl.target_aruco_id')).toBe(50);
    expect(launchBlockers(setPath(doc, 'rtl.target_aruco_id', 49))).toEqual([]);
  });

  it('accepts a legacy integer dictionary, bounding the ID only below', () => {
    expect(launchBlockers(validDocument({ 'rtl.marker_dict': 5, 'rtl.target_aruco_id': 900 }))).toEqual([]);
    expect(isKnownMarkerDictionary(5)).toBe(false);
    expect(isKnownMarkerDictionary('DICT_5X5_100')).toBe(true);
  });

  it.each([null, true, -1, 2.5, { name: 'x' }])('refuses a dictionary of type or value %s', (value) => {
    expect(launchBlockers(validDocument({ 'rtl.marker_dict': value }))).toContain('rtl.marker_dict');
  });

  it('refuses a dictionary name the sheet does not list, and accepts a numeric string as the integer it is', () => {
    expect(launchBlockers(validDocument({ 'rtl.marker_dict': 'tag36h11' }))).toContain('rtl.marker_dict');
    expect(launchBlockers(validDocument({ 'rtl.marker_dict': '20' }))).toEqual([]);
  });

  it.each([0.005, 0.16, 1.0])('accepts a tag of %s m', (size) => {
    expect(launchBlockers(validDocument({ 'rtl.tag_size': size }))).toEqual([]);
  });

  it.each([0, -0.1, 1.01, Number.NaN, '0.2'])('refuses a tag of %s', (size) => {
    expect(launchBlockers(validDocument({ 'rtl.tag_size': size }))).toContain('rtl.tag_size');
  });

  it.each([1, 25, 120])('accepts a centering window of %s s', (seconds) => {
    expect(launchBlockers(validDocument({ 'rtl.centering_timeout_sec': seconds }))).toEqual([]);
  });

  it.each([0, 0.5, 121, Number.NaN, '25'])('refuses a centering window of %s', (seconds) => {
    expect(launchBlockers(validDocument({ 'rtl.centering_timeout_sec': seconds }))).toEqual(['rtl.centering_timeout_sec']);
    expect(advancedFieldErrors(validDocument({ 'rtl.centering_timeout_sec': seconds }))['rtl.centering_timeout_sec']).toBe(
      'A janela de centragem precisa ficar entre 1 e 120 s.'
    );
  });

  it('reports the dictionary size for the chosen name', () => {
    expect(markerCount('DICT_APRILTAG_36h11')).toBe(587);
    expect(markerCount('DICT_4X4_50')).toBe(50);
    expect(markerCount(5)).toBeNull();
  });
});

describe('launchBlockers by mission mode', () => {
  it('does not block an intermediate launch on a field it never uses', () => {
    const doc = { ...validDocument(), mission_mode: 'intermediate' } as Record<string, unknown>;
    const broken = setPath(setPath(doc, 'rtl.timeout_sec', 9999), 'rtl.target_aruco_id', -4);
    expect(launchBlockers(broken)).toEqual([]);
  });

  it('still blocks the same document in the advanced mode', () => {
    const doc = { ...validDocument(), mission_mode: 'advanced' } as Record<string, unknown>;
    const broken = setPath(doc, 'rtl.timeout_sec', 9999);
    expect(launchBlockers(broken)).toContain('rtl.timeout_sec');
  });

  it('keeps blocking a simple launch on a marker the landing needs', () => {
    const doc = { ...validDocument(), mission_mode: 'simple' } as Record<string, unknown>;
    const broken = setPath(doc, 'rtl.target_aruco_id', -4);
    expect(launchBlockers(broken)).toContain('rtl.target_aruco_id');
  });

  it('blocks a simple launch without a turn rate, and no other mode', () => {
    const doc = setPath(validDocument(), 'yaw_pattern.scan_vyaw', undefined);
    expect(launchBlockers(setPath(doc, 'mission_mode', 'simple'))).toEqual(['yaw_pattern.scan_vyaw']);
    expect(launchBlockers(setPath(doc, 'mission_mode', 'intermediate'))).toEqual([]);
    expect(launchBlockers(setPath(doc, 'mission_mode', 'advanced'))).toEqual([]);
  });

  it('blocks a mode the mission cannot parse', () => {
    expect(launchBlockers({ ...validDocument(), mission_mode: 'expert' })).toContain('mission_mode');
    expect(launchBlockers({ ...validDocument(), mission_mode: 3 })).toContain('mission_mode');
  });

  it('accepts an absent, blank or non-canonical mode', () => {
    const doc = validDocument() as Record<string, unknown>;
    delete doc.mission_mode;
    expect(launchBlockers(doc)).not.toContain('mission_mode');
    expect(launchBlockers({ ...validDocument(), mission_mode: '' })).not.toContain('mission_mode');
    expect(launchBlockers({ ...validDocument(), mission_mode: ' Simple ' })).toEqual([]);
  });
});

describe('fillMissing', () => {
  it('backfills the turn rate of a store written before it existed', () => {
    expect(BACKFILL_PATHS).toEqual(['yaw_pattern.scan_vyaw']);
    expect(getPath(fillMissing({}, factoryDocument()), 'yaw_pattern.scan_vyaw')).toBe(0.15);
  });

  it('returns a document that already holds the value unchanged', () => {
    const doc = validDocument({ 'yaw_pattern.scan_vyaw': 0.2 });
    expect(fillMissing(doc, factoryDocument())).toBe(doc);
  });

  it('replaces a value that is not a finite number', () => {
    for (const value of [Number.NaN, null, '0.2', Number.POSITIVE_INFINITY]) {
      const doc = validDocument({ 'yaw_pattern.scan_vyaw': value });
      expect(getPath(fillMissing(doc, factoryDocument()), 'yaw_pattern.scan_vyaw')).toBe(0.15);
    }
  });

  it('leaves the path alone when the factory does not hold a finite number either', () => {
    const doc = { no_fly: true };
    expect(fillMissing(doc, {})).toBe(doc);
  });
});

describe('normalizeForMode', () => {
  const factory = { kinematics: { altitude_ceiling_margin_m: 0.6 } };

  it('squeezes the altitude into the simple range and pins the margin', () => {
    const doc = { kinematics: { target_altitude_m: 1.8, altitude_ceiling_margin_m: 0.1 } };
    const next = normalizeForMode(doc, 'simple', factory);
    expect(next.kinematics).toMatchObject({ target_altitude_m: 1, altitude_ceiling_margin_m: 0.6 });
  });

  it('raises an altitude below the simple floor', () => {
    const next = normalizeForMode({ kinematics: { target_altitude_m: 0.3 } }, 'simple', factory);
    expect((next.kinematics as { target_altitude_m: number }).target_altitude_m).toBe(0.7);
  });

  it('leaves the other modes untouched, by reference', () => {
    const doc = { kinematics: { target_altitude_m: 1.8, altitude_ceiling_margin_m: 0.1 } };
    expect(normalizeForMode(doc, 'advanced', factory)).toBe(doc);
    expect(normalizeForMode(doc, 'intermediate', factory)).toBe(doc);
  });

  it('uses the pad of the document: clamps 1.0 to 1.7 and 3.0 to 2.0 with platform.height_m = 1', () => {
    const altitudeOf = (value: number, pad: number) =>
      (normalizeForMode({ platform: { height_m: pad }, kinematics: { target_altitude_m: value } }, 'simple', factory)
        .kinematics as { target_altitude_m: number }).target_altitude_m;
    expect(altitudeOf(1.0, 1)).toBe(1.7);
    expect(altitudeOf(3.0, 1)).toBe(2);
    expect(altitudeOf(1.8, 1)).toBe(1.8);
    expect(altitudeOf(1.8, 0)).toBe(1);
    expect(altitudeOf(0.3, 0)).toBe(0.7);
  });

  it('pins the mission margin when there is no factory value', () => {
    const doc = { kinematics: { target_altitude_m: 0.8, altitude_ceiling_margin_m: 0.1 } };
    const next = normalizeForMode(doc, 'simple', null);
    expect(next.kinematics).toMatchObject({ target_altitude_m: 0.8, altitude_ceiling_margin_m: SIMPLE_CEILING_MARGIN_M });
    expect(SIMPLE_CEILING_MARGIN_M).toBe(0.6);
  });

  it('returns the same document when the margin is pinned and the altitude is in range', () => {
    const doc = { kinematics: { target_altitude_m: 0.8, altitude_ceiling_margin_m: 0.6 } };
    expect(normalizeForMode(doc, 'simple', null)).toBe(doc);
    expect(normalizeForMode(doc, 'simple', factory)).toBe(doc);
  });
});

describe('launch checks in the simple mode', () => {
  it('does not block on a ceiling margin the mode hides', () => {
    const doc = { ...completeDocument(), mission_mode: 'simple' } as Record<string, unknown>;
    const broken = setPath(setPath(doc, 'kinematics.altitude_ceiling_margin_m', -3), 'kinematics.target_altitude_m', 0.8);
    expect(launchBlockers(broken)).not.toContain('kinematics.altitude_ceiling_margin_m');
  });

  it('blocks an altitude above the native height only in the simple mode', () => {
    const base = setPath(completeDocument(), 'kinematics.target_altitude_m', 1.4) as Record<string, unknown>;
    expect(launchBlockers({ ...base, mission_mode: 'simple' })).toContain('kinematics.target_altitude_m');
    expect(launchBlockers({ ...base, mission_mode: 'advanced' })).not.toContain('kinematics.target_altitude_m');
  });

  it('judges the simple altitude over the pad of the document', () => {
    const base = setPath(
      setPath(completeDocument(), 'platform.height_m', 1),
      'mission_mode',
      'simple'
    ) as Record<string, unknown>;
    expect(launchBlockers(setPath(base, 'kinematics.target_altitude_m', 1.0))).toContain('kinematics.target_altitude_m');
    expect(launchBlockers(setPath(base, 'kinematics.target_altitude_m', 1.8))).not.toContain('kinematics.target_altitude_m');
  });

  it('raises no conflict citing the margin for any altitude of the simple slider over a 1 m pad', () => {
    const factory = factoryDocument();
    for (let step = 17; step <= 20; step += 1) {
      const doc = normalizeForMode(
        setPath(
          setPath(setPath(validDocument(), 'platform.height_m', 1), 'mission_mode', 'simple'),
          'kinematics.target_altitude_m',
          step / 10
        ),
        'simple',
        factory
      );
      expect(activeConflicts(doc).filter((c) => c.paths.includes('kinematics.altitude_ceiling_margin_m'))).toEqual([]);
    }
  });

  it('raises no conflict citing the margin for any altitude the simple slider offers, with the pinned margin', () => {
    const factory = factoryDocument();
    for (let altitude = 0.7; altitude <= 1.0001; altitude += 0.1) {
      const doc = normalizeForMode(
        setPath(setPath(validDocument(), 'mission_mode', 'simple'), 'kinematics.target_altitude_m', Math.round(altitude * 10) / 10),
        'simple',
        factory
      );
      expect(activeConflicts(doc).filter((c) => c.paths.includes('kinematics.altitude_ceiling_margin_m'))).toEqual([]);
    }
  });
});
