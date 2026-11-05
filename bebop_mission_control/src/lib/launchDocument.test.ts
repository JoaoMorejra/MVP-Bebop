import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { completeDocument, factoryDocument, validDocument } from './__fixtures__/missionDocument';
import {
  BACKFILL_PATHS,
  LAUNCH_COUNTDOWN_SEC,
  REQUIRED_LAUNCH_NUMBERS,
  advancedFieldErrors,
  applySchemaFields,
  envelopeBlockers,
  fillMissing,
  launchBlockers,
  REQUIRED_LAUNCH_BOOLEANS,
  altitudeAdjustment,
  launchCountdownSec,
  migrateLegacyMode,
  normalizeForMode,
  reconcileCoupledPaths,
  setLinkedPath,
} from './launchDocument';
import {
  ADVANCED_PATHS,
  ALL_PARAMETERS,
  NUMBER_PARAMETERS,
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
    for (const spec of NUMBER_PARAMETERS) expect(REQUIRED_LAUNCH_NUMBERS).toContain(spec.path);
    for (const spec of ALL_PARAMETERS.filter((item) => item.kind === 'boolean')) {
      expect(REQUIRED_LAUNCH_NUMBERS).not.toContain(spec.path);
    }
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

describe('the fixed countdown at the launch gate', () => {
  it('lets a document with the 10 s countdown through', () => {
    expect(launchBlockers(validDocument({ 'kinematics.countdown_sec': 10 }))).not.toContain('kinematics.countdown_sec');
  });

  it.each([[-1], [31]])('still bounds a countdown of %s s to 0..30 s outside the sheet', (value) => {
    expect(launchBlockers(validDocument({ 'kinematics.countdown_sec': value }))).toContain('kinematics.countdown_sec');
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

const forwardSpec = NUMBER_PARAMETERS.find((spec) => spec.path === 'kinematics.forward_cruise_velocity')!;

describe('slider ranges', () => {
  it.each([
    ['kinematics.target_altitude_m', 0.1, 3.0, 0.05, 2],
    ['kinematics.altitude_ceiling_margin_m', 0.0, 2.0, 0.05, 2],
    ['kinematics.forward_cruise_velocity', 0.01, 0.6, 0.01, 2],
    ['gimbal.search_tilt_deg', -45, 0, 1, 0],
    ['gimbal.nadir_tilt_deg', -80, 0, 1, 0],
  ])('%s spans [%s, %s] in steps of %s', (path, min, max, step, precision) => {
    const spec = NUMBER_PARAMETERS.find((item) => item.path === path)!;
    expect([spec.min, spec.max, spec.step, spec.precision]).toEqual([min, max, step, precision]);
  });

  it.each([
    ['kinematics.target_altitude_m', 0.1],
    ['kinematics.target_altitude_m', 3.0],
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
    ['kinematics.target_altitude_m', 3.01],
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
  it('fixes the launch countdown at 10 s, the window FlightKinematicsConfig.countdown_sec holds', () => {
    expect(LAUNCH_COUNTDOWN_SEC).toBe(10);
    for (const value of [3, 0, 30, undefined, Number.NaN]) {
      const doc = reconcileCoupledPaths(validDocument({ 'kinematics.countdown_sec': value }));
      expect(getPath(doc, 'kinematics.countdown_sec'), String(value)).toBe(10);
    }
  });

  it('returns the same document when the countdown is already 10 s', () => {
    const doc = reconcileCoupledPaths(validDocument());
    expect(getPath(doc, 'kinematics.countdown_sec')).toBe(10);
    expect(reconcileCoupledPaths(doc)).toBe(doc);
  });

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
      'rtl.visual_descent_enabled',
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
  it('does not block a launch without the return on a field it never uses', () => {
    const doc = setPath({ ...validDocument(), mission_mode: 'advanced' }, 'rtl.enabled', false) as Record<string, unknown>;
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
    expect(getPath(fillMissing({}, factoryDocument()), 'yaw_pattern.scan_vyaw')).toBe(0.08);
  });

  it('returns a document that already holds the value unchanged', () => {
    const doc = validDocument({ 'yaw_pattern.scan_vyaw': 0.2 });
    expect(fillMissing(doc, factoryDocument())).toBe(doc);
  });

  it('replaces a value that is not a finite number', () => {
    for (const value of [Number.NaN, null, '0.2', Number.POSITIVE_INFINITY]) {
      const doc = validDocument({ 'yaw_pattern.scan_vyaw': value });
      expect(getPath(fillMissing(doc, factoryDocument()), 'yaw_pattern.scan_vyaw')).toBe(0.08);
    }
  });

  it('leaves the path alone when the factory does not hold a finite number either', () => {
    const doc = { no_fly: true };
    expect(getPath(fillMissing(doc, {}), 'yaw_pattern.scan_vyaw')).toBeUndefined();
  });

  it('backfills the booleans a store written before them lacks, from the factory or their default', () => {
    expect(fillMissing({}, {})).toEqual({
      rtl: { enabled: true, visual_descent_enabled: true },
      platform: { takeoff_from_ground: false },
      vision: { victim_detection_enabled: true },
    });
    expect(getPath(fillMissing({}, { rtl: { enabled: false } }), 'rtl.enabled')).toBe(false);
    expect(getPath(fillMissing({}, { vision: { victim_detection_enabled: false } }), 'vision.victim_detection_enabled')).toBe(false);
    expect(getPath(fillMissing({}, { rtl: { visual_descent_enabled: false } }), 'rtl.visual_descent_enabled')).toBe(false);
    const doc = {
      rtl: { enabled: false, visual_descent_enabled: false },
      platform: { takeoff_from_ground: true },
      vision: { victim_detection_enabled: false },
    };
    expect(fillMissing(doc, {})).toBe(doc);
  });
});

describe('normalizeForMode', () => {
  it('moves an altitude over the floor envelope to its bound', () => {
    const next = normalizeForMode({ kinematics: { target_altitude_m: 3.5, altitude_ceiling_margin_m: 0.1 } });
    expect(next.kinematics).toMatchObject({ target_altitude_m: 3, altitude_ceiling_margin_m: 0.1 });
  });

  it('raises an altitude under the pad minimum to 1.30 m over a 1 m pad', () => {
    const next = normalizeForMode({ platform: { height_m: 1 }, kinematics: { target_altitude_m: 0.5 } });
    expect((next.kinematics as { target_altitude_m: number }).target_altitude_m).toBe(1.3);
  });

  it('returns the same document when the altitude is inside the envelope', () => {
    const doc = { platform: { height_m: 1 }, kinematics: { target_altitude_m: 1.8, altitude_ceiling_margin_m: 0.1 } };
    expect(normalizeForMode(doc)).toBe(doc);
  });

  it('leaves a missing altitude for the launch checks to name', () => {
    const doc = { platform: { height_m: 1 } };
    expect(normalizeForMode(doc)).toBe(doc);
  });

  it('rounds the bound to the slider resolution, inside the envelope', () => {
    const next = normalizeForMode({ platform: { height_m: 0.125 }, kinematics: { target_altitude_m: 0.2 } });
    expect((next.kinematics as { target_altitude_m: number }).target_altitude_m).toBe(0.45);
  });
});

describe('launch checks on the shared envelope', () => {
  it('does not block on a ceiling margin the pad top ignores, in either mode, and does from the floor', () => {
    for (const mode of ['simple', 'advanced']) {
      const doc = { ...completeDocument(), mission_mode: mode } as Record<string, unknown>;
      const pad = setPath(setPath(doc, 'platform.height_m', 1), 'kinematics.target_altitude_m', 1.8);
      const broken = setPath(pad, 'kinematics.altitude_ceiling_margin_m', -3);
      expect(launchBlockers(broken)).not.toContain('kinematics.altitude_ceiling_margin_m');
      expect(launchBlockers(setPath(broken, 'platform.takeoff_from_ground', true))).toContain(
        'kinematics.altitude_ceiling_margin_m'
      );
    }
  });

  it('judges the altitude of every mode over the pad of the document (BUG 1.1)', () => {
    const base = setPath(completeDocument(), 'platform.height_m', 1) as Record<string, unknown>;
    for (const mode of ['simple', 'advanced']) {
      const doc = setPath(base, 'mission_mode', mode);
      expect(launchBlockers(setPath(doc, 'kinematics.target_altitude_m', 1.25))).toContain('kinematics.target_altitude_m');
      expect(launchBlockers(setPath(doc, 'kinematics.target_altitude_m', 1.8))).not.toContain('kinematics.target_altitude_m');
      expect(launchBlockers(setPath(doc, 'kinematics.target_altitude_m', 3.0))).not.toContain('kinematics.target_altitude_m');
      expect(launchBlockers(setPath(doc, 'kinematics.target_altitude_m', 3.05))).toContain('kinematics.target_altitude_m');
    }
  });

  it('blocks a malformed pad height the mission would refuse', () => {
    const doc = setPath(completeDocument(), 'platform.height_m', -1);
    expect(launchBlockers(doc)).toContain('platform.height_m');
  });

  it('blocks a boolean field written as a string', () => {
    expect(launchBlockers(setPath(completeDocument(), 'rtl.enabled', 'false'))).toContain('rtl.enabled');
    expect(launchBlockers(setPath(completeDocument(), 'platform.takeoff_from_ground', 1))).toContain(
      'platform.takeoff_from_ground'
    );
  });

  it('raises no conflict citing the margin for any altitude of the simple slider over a 1 m pad', () => {
    for (let step = 26; step <= 40; step += 1) {
      const doc = normalizeForMode(
        setPath(
          setPath(setPath(validDocument(), 'platform.height_m', 1), 'mission_mode', 'simple'),
          'kinematics.target_altitude_m',
          step / 20
        )
      );
      expect(activeConflicts(doc).filter((c) => c.paths.includes('kinematics.altitude_ceiling_margin_m'))).toEqual([]);
    }
  });
});

describe('migrateLegacyMode', () => {
  it('carries an Intermediate document over to Advanced without the return', () => {
    const doc = { mission_mode: ' Intermediate ', rtl: { tag_size: 0.2 } };
    const next = migrateLegacyMode(doc);
    expect(next).toEqual({ mission_mode: 'advanced', rtl: { tag_size: 0.2, enabled: false } });
    expect(doc).toEqual({ mission_mode: ' Intermediate ', rtl: { tag_size: 0.2 } });
  });

  it('keeps an explicit return plan and is idempotent', () => {
    const next = migrateLegacyMode({ mission_mode: 'intermediate', rtl: { enabled: true } });
    expect(getPath(next, 'rtl.enabled')).toBe(true);
    expect(migrateLegacyMode(next)).toBe(next);
  });

  it('returns a current document itself', () => {
    for (const doc of [{}, { mission_mode: 'simple' }, { mission_mode: 'advanced', rtl: { enabled: false } }]) {
      expect(migrateLegacyMode(doc)).toBe(doc);
    }
  });
});

describe('altitudeAdjustment', () => {
  it('says what the envelope did to the altitude, in the operator\'s words', () => {
    const before = { platform: { height_m: 1, takeoff_from_ground: false }, kinematics: { target_altitude_m: 0.5 } };
    const after = normalizeForMode(before);
    expect(getPath(after, 'kinematics.target_altitude_m')).toBe(1.3);
    expect(altitudeAdjustment(before, after)).toBe('Cota ajustada para 1,30 m: mínimo sobre o totem.');
    expect(altitudeAdjustment(after, after)).toBeNull();
    const high = { kinematics: { target_altitude_m: 3.4 } };
    expect(altitudeAdjustment(high, normalizeForMode(high))).toBe('Cota ajustada para 3,00 m: máximo permitido.');
  });

  it('re-applies the envelope when the surface goes back to the pad top', () => {
    const ground = { platform: { height_m: 1, takeoff_from_ground: true }, kinematics: { target_altitude_m: 0.5 } };
    expect(normalizeForMode(ground)).toBe(ground);
    const padTop = setPath(ground, 'platform.takeoff_from_ground', false);
    expect(getPath(normalizeForMode(padTop), 'kinematics.target_altitude_m')).toBe(1.3);
  });

  it('does not block a launch on a boolean the document does not hold, and backfills it', () => {
    const doc = validDocument();
    for (const path of Object.keys(REQUIRED_LAUNCH_BOOLEANS)) {
      expect(launchBlockers(setPath(doc, path, undefined))).not.toContain(path);
    }
    expect(REQUIRED_LAUNCH_BOOLEANS).toEqual({
      'platform.takeoff_from_ground': false,
      'rtl.enabled': true,
      'vision.victim_detection_enabled': true,
      'rtl.visual_descent_enabled': true,
    });
  });
});
