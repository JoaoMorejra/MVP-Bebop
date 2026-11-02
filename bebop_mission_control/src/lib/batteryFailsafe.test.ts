import { describe, expect, it } from 'vitest';
import { RETURNABLE_STAGES, batteryTriggerArmed, clampThreshold, failsafeAction, launchParamsJson } from './batteryFailsafe';
import { LAUNCH_COUNTDOWN_SEC } from './launchDocument';

describe('clampThreshold', () => {
  it('keeps every whole percentage from 0 to 100 exactly', () => {
    for (const pct of [0, 1, 5, 10, 40, 41, 99, 100]) expect(clampThreshold(pct)).toBe(pct);
  });

  it('rounds to the 1% step and clamps to the range', () => {
    expect(clampThreshold(12.6)).toBe(13);
    expect(clampThreshold(-3)).toBe(0);
    expect(clampThreshold(140)).toBe(100);
    expect(clampThreshold('25')).toBe(25);
  });

  it('refuses what is not a number', () => {
    expect(clampThreshold(undefined)).toBeNull();
    expect(clampThreshold('abc')).toBeNull();
    expect(clampThreshold(Number.NaN)).toBeNull();
  });
});

describe('failsafeAction', () => {
  it('flies the return from the climb, the search, the approach and the inspection', () => {
    for (const stage of [1, 2, 3, 4]) expect(failsafeAction(stage, true)).toBe('rtl');
    expect([...RETURNABLE_STAGES]).toEqual([1, 2, 3, 4]);
  });

  it('asks for the return while the mission runs even before any stage is reported', () => {
    expect(failsafeAction(0, true)).toBe('rtl');
  });

  it('leaves a return already under way alone', () => {
    expect(failsafeAction(5, true)).toBe('none');
  });

  it('lands in place only when no mission process can take the jump', () => {
    for (const stage of [0, 1, 2, 3, 4, 5]) expect(failsafeAction(stage, false)).toBe('land');
  });
});

describe('batteryTriggerArmed', () => {
  it('arms only in hovering (2) and flying (3)', () => {
    expect(batteryTriggerArmed(2)).toBe(true);
    expect(batteryTriggerArmed(3)).toBe(true);
  });

  it('stays disarmed on the ground, in the climb, in the descent and when unknown', () => {
    for (const state of [0, 1, 4, 5, 6, 7, 8]) expect(batteryTriggerArmed(state), String(state)).toBe(false);
    expect(batteryTriggerArmed(null)).toBe(false);
    expect(batteryTriggerArmed(undefined)).toBe(false);
  });
});

describe('launchParamsJson', () => {
  const sent = (doc: unknown) => JSON.parse(launchParamsJson(doc, { enabled: true, thresholdPct: 25 }));

  it('always sends the fixed 10 s countdown, whatever the document holds', () => {
    for (const value of [3, 0, 30, undefined]) {
      expect(sent({ kinematics: { countdown_sec: value, target_altitude_m: 2 } }).kinematics).toEqual({
        countdown_sec: 10,
        target_altitude_m: 2,
      });
    }
    expect(sent(undefined).kinematics.countdown_sec).toBe(10);
  });

  it('writes the threshold into a clone and leaves the document alone', () => {
    const doc = { kinematics: { countdown_sec: 3 }, battery: { land_pct: 10 } };
    expect(sent(doc).battery.land_pct).toBe(25);
    expect(doc).toEqual({ kinematics: { countdown_sec: 3 }, battery: { land_pct: 10 } });
  });
});

describe('launchParamsJson battery switch', () => {
  it('writes the switch and the threshold, off and on', () => {
    const off = JSON.parse(launchParamsJson({}, { enabled: false, thresholdPct: 35 }));
    expect(off.battery.enabled).toBe(false);
    expect(off.battery.land_pct).toBe(35);
    const on = JSON.parse(launchParamsJson({}, { enabled: true, thresholdPct: 20 }));
    expect(on.battery).toEqual({ enabled: true, land_pct: 20 });
  });

  it('works without a document and keeps the fixed countdown', () => {
    const sent = JSON.parse(launchParamsJson(null, { enabled: false, thresholdPct: 10 }));
    expect(sent.battery).toEqual({ enabled: false, land_pct: 10 });
    expect(sent.kinematics.countdown_sec).toBe(LAUNCH_COUNTDOWN_SEC);
  });
});
