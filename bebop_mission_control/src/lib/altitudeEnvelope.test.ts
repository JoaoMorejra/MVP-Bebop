import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  ALTITUDE_MAX_M,
  ALTITUDE_MIN_M,
  ALTITUDE_STEP_M,
  PAD_CLEARANCE_M,
  VENUE_HEADROOM_M,
  altitudeEnvelope,
  altitudeEnvelopeViolation,
  altitudeSliderRange,
  ceilingFloorFrame,
  launchesFromGround,
  marginUnlocked,
  NATIVE_HOVER_HEIGHT_M,
  TOTEM_CEILING_HEADROOM_M,
  TOTEM_MARGIN_UNLOCK_ABOVE_M,
} from './altitudeEnvelope';
import { FIRMWARE_HOVER_HEIGHT_M } from './parameterConflicts';
import { factoryDocument } from './__fixtures__/missionDocument';
import { launchBlockers, normalizeForMode } from './launchDocument';
import { getPath } from './paths';

interface EnvelopeCase {
  name: string;
  document: Record<string, unknown>;
  expected: {
    min: number;
    max: number;
    accepted: boolean;
    field: string | null;
    relative_target_m: number | null;
    ceiling_floor_m: number;
  };
}

const CASES = (
  JSON.parse(
    readFileSync(resolve(__dirname, '../../../test/fixtures/altitude_envelope_cases.json'), 'utf-8')
  ) as { cases: EnvelopeCase[] }
).cases;

describe('altitude envelope parity with launch_envelope.py', () => {
  it('holds the constants of the mission', () => {
    expect([ALTITUDE_MIN_M, ALTITUDE_MAX_M, ALTITUDE_STEP_M]).toEqual([0.1, 3.0, 0.05]);
    expect([PAD_CLEARANCE_M, VENUE_HEADROOM_M]).toEqual([0.3, 0.1]);
    expect(PAD_CLEARANCE_M).toBe(getPath(factoryDocument(), 'kinematics.takeoff_settle_min_altitude_m'));
    expect(NATIVE_HOVER_HEIGHT_M).toBe(FIRMWARE_HOVER_HEIGHT_M);
    expect(TOTEM_CEILING_HEADROOM_M).toBe(0.6);
    expect(TOTEM_MARGIN_UNLOCK_ABOVE_M).toBe(2.0);
  });

  it.each(CASES.map((c) => [c.name, c] as const))('%s', (_name, { document, expected }) => {
    const range = altitudeEnvelope(document);
    expect(range.min).toBeCloseTo(expected.min, 9);
    expect(range.max).toBeCloseTo(expected.max, 9);
    expect(altitudeEnvelopeViolation(document)).toBe(expected.field);
    if (expected.field === null || expected.field === 'kinematics.target_altitude_m') {
      expect(ceilingFloorFrame(document)).toBeCloseTo(expected.ceiling_floor_m, 9);
    }
  });

  it('blocks exactly the documents the mission refuses, on the field it names', () => {
    for (const { name, document, expected } of CASES) {
      const factory = factoryDocument();
      const doc = {
        ...factory,
        ...document,
        kinematics: { ...(factory.kinematics as object), ...(document.kinematics as object) },
        platform: { ...(factory.platform as object), ...(document.platform as object) },
        rtl: { ...(factory.rtl as object), ...(document.rtl as object), reverse_cruise_velocity: -0.2 },
      };
      const blocked = launchBlockers(doc);
      if (expected.field === null) expect(blocked, name).not.toContain('kinematics.target_altitude_m');
      else expect(blocked, name).toContain(expected.field);
    }
  });

  it('normalizes every refused altitude into a value the mission accepts', () => {
    for (const { document, expected } of CASES) {
      if (expected.field !== 'kinematics.target_altitude_m' || expected.min > expected.max) continue;
      expect(altitudeEnvelopeViolation(normalizeForMode(document))).toBeNull();
    }
  });
});

describe('altitudeSliderRange', () => {
  it('rounds the bounds inward to the slider resolution', () => {
    expect(altitudeSliderRange({ platform: { height_m: 1 } })).toEqual({ min: 1.3, max: 3 });
    expect(altitudeSliderRange({ platform: { height_m: 0.125 } })).toEqual({ min: 0.45, max: 3 });
    expect(altitudeSliderRange({ platform: { height_m: 1, venue_ceiling_agl_m: 2.07 } })).toEqual({ min: 1.3, max: 1.95 });
  });

  it('treats a pad of zero, or a missing one, as the floor', () => {
    expect(launchesFromGround({ platform: { height_m: 0 } })).toBe(true);
    expect(launchesFromGround(undefined)).toBe(true);
    expect(altitudeSliderRange(undefined)).toEqual({ min: 0.1, max: 3 });
  });

  it('follows platform.takeoff_from_ground when it is true', () => {
    const doc = { platform: { height_m: 1, takeoff_from_ground: true } };
    expect(launchesFromGround(doc)).toBe(true);
    expect(altitudeEnvelope(doc)).toEqual({ min: 0.1, max: 3 });
  });

  it('reaches 3.00 m under a 3.2 m venue and 2.40 m under a 2.5 m one', () => {
    expect(altitudeSliderRange({ platform: { height_m: 1, venue_ceiling_agl_m: 3.2 } })).toEqual({ min: 1.3, max: 3 });
    expect(altitudeSliderRange({ platform: { height_m: 1, venue_ceiling_agl_m: 2.5 } })).toEqual({ min: 1.3, max: 2.4 });
  });
});

describe('marginUnlocked', () => {
  const pad = (target: unknown) => ({ platform: { height_m: 1, takeoff_from_ground: false }, kinematics: { target_altitude_m: target } });

  it('is always unlocked from the floor', () => {
    expect(marginUnlocked({ platform: { height_m: 0 }, kinematics: { target_altitude_m: 1 } })).toBe(true);
    expect(marginUnlocked({ platform: { height_m: 1, takeoff_from_ground: true }, kinematics: { target_altitude_m: 1.8 } })).toBe(true);
  });

  it.each([
    [1.8, false],
    [2.0, false],
    [2.05, true],
    [3.0, true],
  ])('on the pad top, target %s m over the floor -> %s', (target, unlocked) => {
    expect(marginUnlocked(pad(target))).toBe(unlocked);
  });

  it.each([[Number.NaN], ['2.5'], [null], [undefined], [true]])('keeps the pad top locked for a target of %s', (target) => {
    expect(marginUnlocked(pad(target))).toBe(false);
  });
});

describe('ceilingFloorFrame with the pad-top margin', () => {
  const pad = (target: number, margin: unknown, venue: number | null = null) => ({
    platform: { height_m: 1, takeoff_from_ground: false, venue_ceiling_agl_m: venue },
    kinematics: { target_altitude_m: target, altitude_ceiling_margin_m: margin },
  });

  it('ignores the margin at or below 2.0 m', () => {
    expect(ceilingFloorFrame(pad(2.0, 0.4))).toBeCloseTo(2.6, 9);
    expect(ceilingFloorFrame(pad(1.8, 2.0))).toBeCloseTo(2.6, 9);
  });

  it.each([
    [2.05, 0.4, 2.45],
    [2.5, 0.6, 3.1],
    [3.0, 0.6, 3.6],
  ])('applies it above 2.0 m: target %s, margin %s -> %s over the floor', (target, margin, expected) => {
    expect(ceilingFloorFrame(pad(target, margin))).toBeCloseTo(expected, 9);
  });

  it('keeps the venue cap', () => {
    expect(ceilingFloorFrame(pad(2.7, 0.6, 3.2))).toBeCloseTo(3.2, 9);
    expect(ceilingFloorFrame(pad(3.0, 0.6, 3.2))).toBeCloseTo(3.2, 9);
  });

  it('is null for an unlocked pad top without a finite margin', () => {
    expect(ceilingFloorFrame(pad(2.5, Number.NaN))).toBeNull();
    expect(ceilingFloorFrame(pad(2.5, undefined))).toBeNull();
  });
});
