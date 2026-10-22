import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { factoryDocument, validDocument } from './__fixtures__/missionDocument';
import { launchBlockers, reconcileCoupledPaths, setLinkedPath } from './launchDocument';
import {
  FIRMWARE_HOVER_HEIGHT_M,
  LOW_ALTITUDE_ADVISORY_M,
  LOW_CEILING_MARGIN_ADVISORY_M,
  evaluateParameterConflicts,
} from './parameterConflicts';
import { getPath, setPath, type Doc } from './paths';

interface ConflictCase {
  name: string;
  overrides: Record<string, unknown>;
  expect: { errors: string[]; warnings: string[] };
}

const shared = JSON.parse(
  readFileSync(resolve(__dirname, '../../../test/fixtures/parameter_conflict_cases.json'), 'utf-8')
) as { base_overrides: Record<string, unknown>; cases: ConflictCase[] };

function removePath(doc: Doc, path: string): Doc {
  const keys = path.split('.');
  const parent = keys.slice(0, -1).join('.');
  const node = { ...(getPath(doc, parent) as Doc) };
  delete node[keys[keys.length - 1]];
  return setPath(doc, parent, node);
}

function caseDocument(overrides: Record<string, unknown>): Doc {
  let doc = factoryDocument();
  for (const [path, value] of Object.entries({ ...shared.base_overrides, ...overrides })) {
    doc = value === null ? removePath(doc, path) : setPath(doc, path, value);
  }
  return doc;
}

describe('evaluateParameterConflicts on the shared cases', () => {
  it.each(shared.cases.map((entry) => [entry.name, entry] as const))('%s', (_name, entry) => {
    const found = evaluateParameterConflicts(caseDocument(entry.overrides));
    expect({
      errors: found.filter((c) => c.severity === 'error').map((c) => c.id),
      warnings: found.filter((c) => c.severity === 'warning').map((c) => c.id),
    }).toEqual(entry.expect);
  });
});

describe('evaluateParameterConflicts', () => {
  it('finds nothing in the factory document as the station sends it, nor in the valid fixture', () => {
    expect(evaluateParameterConflicts(validDocument())).toEqual([]);
    expect(evaluateParameterConflicts(reconcileCoupledPaths(setLinkedPath(factoryDocument(), 'kinematics.forward_cruise_velocity', 0.2)))).toEqual([]);
  });

  it('lists errors before warnings, one record per rule', () => {
    const found = evaluateParameterConflicts(caseDocument({ 'kinematics.target_altitude_m': 0.1, 'kinematics.altitude_ceiling_margin_m': 0 }));
    const severities = found.map((c) => c.severity);
    expect(severities).toEqual([...severities].sort((a, b) => (a === b ? 0 : a === 'error' ? -1 : 1)));
    expect(new Set(found.map((c) => c.id)).size).toBe(found.length);
  });

  it('no longer refuses a low ceiling: the mission ratchets it through the native takeoff (E03 and W01 retired)', () => {
    const ids = (overrides: Record<string, unknown>) => evaluateParameterConflicts(caseDocument(overrides)).map((c) => c.id);
    expect(ids({ 'kinematics.target_altitude_m': 0.5, 'kinematics.altitude_ceiling_margin_m': 0.45 })).not.toContain('E03');
    expect(ids({ 'kinematics.target_altitude_m': 0.5, 'kinematics.altitude_ceiling_margin_m': 0.5 })).not.toContain('W01');
  });

  it('refuses an altitude outside the envelope of the launch surface (E10), citing the pad-top minimum', () => {
    const pad = { 'platform.height_m': 1, 'platform.takeoff_from_ground': false };
    const [e10] = evaluateParameterConflicts(caseDocument({ ...pad, 'kinematics.target_altitude_m': 1.25 })).filter((c) => c.id === 'E10');
    expect(e10.paths).toEqual(['kinematics.target_altitude_m']);
    expect(e10.message).toContain('1.30 m sobre o totem');
    const ids = (overrides: Record<string, unknown>) => evaluateParameterConflicts(caseDocument(overrides)).map((c) => c.id);
    expect(ids({ ...pad, 'kinematics.target_altitude_m': 1.3 })).not.toContain('E10');
    expect(ids({ 'kinematics.target_altitude_m': 2.05 })).toContain('E10');
    expect(ids({ 'platform.height_m': 1, 'platform.takeoff_from_ground': true, 'kinematics.target_altitude_m': 0.5 })).not.toContain('E10');
  });

  it('judges the ceiling margin only for a launch from the floor (E04, W02)', () => {
    const ids = (overrides: Record<string, unknown>) => evaluateParameterConflicts(caseDocument(overrides)).map((c) => c.id);
    expect(ids({ 'kinematics.altitude_ceiling_margin_m': 0 })).toContain('E04');
    expect(ids({ 'platform.height_m': 1, 'kinematics.target_altitude_m': 1.8, 'kinematics.altitude_ceiling_margin_m': 0 })).not.toContain('E04');
    expect(ids({ 'platform.height_m': 1, 'kinematics.target_altitude_m': 1.8, 'kinematics.altitude_ceiling_margin_m': 0.1 })).not.toContain('W02');
  });

  it('accepts the fastest cruise once the ceiling is reconciled', () => {
    const doc = reconcileCoupledPaths(setLinkedPath(validDocument(), 'kinematics.forward_cruise_velocity', 0.6));
    expect(evaluateParameterConflicts(doc).map((c) => c.id)).not.toContain('E06');
  });

  it('keeps W04, W05 and W06 silent for the shipped configuration and for any approach geometry', () => {
    const shipped = JSON.parse(readFileSync(resolve(__dirname, '../../../mvp_mission_bebop/mission_config.json'), 'utf-8')) as Doc;
    const retired = ['W04', 'W05', 'W06'];
    const idsOf = (doc: Doc) => evaluateParameterConflicts(doc).map((c) => c.id);
    expect(idsOf(shipped).filter((id) => retired.includes(id))).toEqual([]);
    expect(idsOf(shipped)).not.toContain('W08');
    for (const search of [0, -10, -30, -60]) {
      for (const nadir of [-5, -25, -31, -90]) {
        for (const approach of [0, 0.02, 0.3]) {
          const doc = caseDocument({
            'gimbal.search_tilt_deg': search,
            'gimbal.nadir_tilt_deg': nadir,
            'kinematics.max_approach_forward_speed': approach,
            'kinematics.target_altitude_m': 0.1,
          });
          expect(idsOf(doc).filter((id) => retired.includes(id))).toEqual([]);
        }
      }
    }
  });

  it('sizes W08 on the lateral cruise of the search timeout alone', () => {
    const needed = (0.2 * 30) / 0.2;
    const find = (timeout: number, extra: Record<string, unknown> = {}) =>
      evaluateParameterConflicts(
        caseDocument({ 'kinematics.forward_cruise_velocity': 0.2, 'timeouts.search_timeout_sec': 30, 'rtl.timeout_sec': timeout, ...extra })
      ).find((c) => c.id === 'W08');
    expect(find(needed + 0.5)).toBeUndefined();
    expect(find(needed + 0.5, { 'gimbal.search_tilt_deg': 0 })).toBeUndefined();
    const w08 = find(needed - 0.5);
    expect(w08?.message).toContain(`${needed.toFixed(1)} s`);
    expect(w08?.fix).toContain('30.0 s');
    const noisy = evaluateParameterConflicts(
      caseDocument({
        'kinematics.forward_cruise_velocity': 0.2,
        'timeouts.search_timeout_sec': 42,
        'rtl.reverse_cruise_velocity': -0.3,
        'rtl.timeout_sec': 20,
      })
    ).find((c) => c.id === 'W08');
    expect(noisy?.fix).toContain('28.0 s');
    expect(noisy?.fix).not.toContain('29.0 s');
  });

  it('never invents a missing input', () => {
    expect(evaluateParameterConflicts(null)).toEqual([]);
    expect(evaluateParameterConflicts({})).toEqual([]);
  });

  it('keeps its advisory constants equal to what the factory fields derive', () => {
    const factory = factoryDocument();
    expect(FIRMWARE_HOVER_HEIGHT_M).toBe(1.0);
    expect(LOW_ALTITUDE_ADVISORY_M).toBe(
      Math.max(getPath(factory, 'vision.min_altitude_for_ibvs_m') as number, getPath(factory, 'kinematics.takeoff_settle_min_altitude_m') as number)
    );
    expect(LOW_CEILING_MARGIN_ADVISORY_M).toBeCloseTo(
      (getPath(factory, 'kinematics.climb_deadband_m') as number) + 2 * (getPath(factory, 'kinematics.climb_settle_max_position_sigma_m') as number),
      9
    );
  });
});

describe('launchBlockers with conflicts', () => {
  it('names the paths of an error conflict and not those of a warning', () => {
    expect(launchBlockers(caseDocument({ 'gimbal.search_tilt_deg': -30, 'gimbal.nadir_tilt_deg': -10 }))).toEqual(
      expect.arrayContaining(['gimbal.nadir_tilt_deg', 'gimbal.search_tilt_deg'])
    );
    expect(launchBlockers(caseDocument({ 'kinematics.countdown_sec': 4 }))).toEqual([]);
  });
});
