import { describe, expect, it } from 'vitest';
import { completeDocument, validDocument } from './__fixtures__/missionDocument';
import {
  REQUIRED_LAUNCH_NUMBERS,
  applySchemaFields,
  launchBlockers,
  launchCountdownSec,
} from './launchDocument';
import { ALL_PARAMETERS } from './parameterSchema';
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
    expect(launchBlockers(null)).toEqual([...REQUIRED_LAUNCH_NUMBERS]);
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
    expect(launchBlockers(doc)).toEqual(['kinematics.forward_cruise_velocity', 'gimbal.nadir_tilt_deg']);
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
