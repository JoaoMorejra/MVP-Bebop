import { describe, expect, it } from 'vitest';
import { PARAMETER_GROUPS, groupsForMode, TRACKED_PATHS } from './parameterSchema';
import {
  DEFAULT_MISSION_MODE,
  MISSION_MODES,
  hasInvalidMissionMode,
  inactivePaths,
  isPathActive,
  parseMissionMode,
  readMissionMode,
  stageLabel,
} from './missionMode';

describe('readMissionMode', () => {
  it('defaults to the full mission when the document says nothing', () => {
    expect(DEFAULT_MISSION_MODE).toBe('advanced');
    expect(readMissionMode({})).toBe('advanced');
    expect(readMissionMode(null)).toBe('advanced');
    expect(readMissionMode({ mission_mode: '' })).toBe('advanced');
  });

  it('reads a valid mode and refuses to guess for an invalid one', () => {
    expect(readMissionMode({ mission_mode: 'simple' })).toBe('simple');
    expect(readMissionMode({ mission_mode: 'intermediate' })).toBe('advanced');
    expect(readMissionMode({ mission_mode: 'expert' })).toBe('advanced');
    expect(readMissionMode({ mission_mode: 3 })).toBe('advanced');
  });

  it('trims and lowercases a string mode, as the mission does', () => {
    expect(readMissionMode({ mission_mode: 'Simple' })).toBe('simple');
    expect(readMissionMode({ mission_mode: 'SIMPLE' })).toBe('simple');
    expect(readMissionMode({ mission_mode: '  ' })).toBe('advanced');
  });
});

describe('MISSION_MODES', () => {
  it('lists Simples and Avançado in that order, with their summaries', () => {
    expect(MISSION_MODES.map((m) => m.id)).toEqual(['simple', 'advanced']);
    expect(MISSION_MODES.map((m) => m.label)).toEqual(['Simples', 'Avançado']);
    expect(MISSION_MODES.map((m) => m.summary)).toEqual([
      'Giro no lugar, foto e retorno do rumo',
      'Varredura lateral, foto e retorno lateral',
    ]);
  });
});

describe('parameter filtering by mode', () => {
  const paths = (mode: Parameters<typeof groupsForMode>[0]) =>
    groupsForMode(mode).flatMap((g) => g.items.map((i) => i.path));

  const everyField = PARAMETER_GROUPS.flatMap((g) => g.items.map((i) => i.path));

  it('shows every group and field of the original sheet in the advanced mode', () => {
    expect(groupsForMode('advanced')).toHaveLength(PARAMETER_GROUPS.length);
    expect(paths('advanced')).toEqual(everyField.filter((p) => p !== 'yaw_pattern.scan_vyaw'));
  });

  it('drops the return timeout and the turn rate in the advanced mode without the return', () => {
    const shown = groupsForMode('advanced', { rtl: { enabled: false } }).flatMap((g) => g.items.map((i) => i.path));
    expect(shown).not.toContain('rtl.timeout_sec');
    expect(shown).not.toContain('yaw_pattern.scan_vyaw');
    expect(shown).toContain('timeouts.search_timeout_sec');
    expect(shown).toContain('kinematics.countdown_sec');
    expect(shown).toContain('kinematics.forward_cruise_velocity');
    expect(everyField.filter((p) => !shown.includes(p)).sort()).toEqual(
      ['rtl.timeout_sec', 'yaw_pattern.scan_vyaw'].sort()
    );
  });

  it('swaps the cruise velocity for the turn rate in the simple mode', () => {
    const shown = paths('simple');
    expect(shown).not.toContain('rtl.timeout_sec');
    expect(shown).not.toContain('kinematics.forward_cruise_velocity');
    expect(shown).toContain('yaw_pattern.scan_vyaw');
    expect(shown).toContain('gimbal.search_tilt_deg');
    expect(shown).toContain('gimbal.nadir_tilt_deg');
    expect(shown).toContain('timeouts.search_timeout_sec');
    expect(shown).toContain('kinematics.countdown_sec');
  });

  it('keeps the ceiling margin on the sheet of both modes, locked on the pad top', () => {
    const shown = paths('simple');
    expect(shown).toContain('kinematics.altitude_ceiling_margin_m');
    expect(shown).toContain('kinematics.target_altitude_m');
    expect(shown).toContain('kinematics.takeoff_stabilize_duration_sec');
    expect(paths('advanced')).toContain('kinematics.altitude_ceiling_margin_m');
  });

  it('offers the same altitude slider in every mode, 0.10 to 2.00 m in 0.05 m steps over the floor', () => {
    const altitude = (mode: Parameters<typeof groupsForMode>[0], doc?: unknown) =>
      groupsForMode(mode, doc).flatMap((g) => g.items).find((i) => i.path === 'kinematics.target_altitude_m')!;
    for (const mode of ['simple', 'advanced'] as const) {
      expect(altitude(mode)).toMatchObject({ min: 0.1, max: 2, step: 0.05, precision: 2 });
    }
  });

  it('raises the slider minimum to 1.30 m over a 1 m pad, in every mode', () => {
    for (const mode of ['simple', 'advanced'] as const) {
      const altitude = groupsForMode(mode, { platform: { height_m: 1 } })
        .flatMap((g) => g.items)
        .find((i) => i.path === 'kinematics.target_altitude_m')!;
      expect(altitude).toMatchObject({ min: 1.3, max: 2 });
    }
  });

  it('marks the ceiling margin inactive on the pad top, in both modes', () => {
    const pad = { platform: { height_m: 1 } };
    const ground = { platform: { height_m: 1, takeoff_from_ground: true } };
    for (const mode of ['simple', 'advanced'] as const) {
      expect(isPathActive(mode, 'kinematics.altitude_ceiling_margin_m', pad)).toBe(false);
      expect(isPathActive(mode, 'kinematics.altitude_ceiling_margin_m', ground)).toBe(true);
      expect(isPathActive(mode, 'kinematics.altitude_ceiling_margin_m')).toBe(true);
    }
  });

  it('locks the margin on the pad top with the fixed ceiling over the floor as its legend', () => {
    const margin = (doc: unknown) =>
      groupsForMode('advanced', doc).flatMap((g) => g.items).find((i) => i.path === 'kinematics.altitude_ceiling_margin_m')!;
    const pad = { platform: { height_m: 1, venue_ceiling_agl_m: 2.5 }, kinematics: { target_altitude_m: 1.8 } };
    expect(margin(pad).disabledReason?.(pad)).toBe('Teto fixo do perfil de totem: 2,50 m sobre o piso');
    const tall = { platform: { height_m: 1 }, kinematics: { target_altitude_m: 1.8 } };
    expect(margin(tall).disabledReason?.(tall)).toBe('Teto fixo do perfil de totem: 2,60 m sobre o piso');
    const ground = { platform: { height_m: 1, takeoff_from_ground: true } };
    expect(margin(ground).disabledReason?.(ground)).toBeNull();
  });

  it('keeps the three group cards in every mode', () => {
    for (const mode of ['simple', 'advanced'] as const) {
      expect(groupsForMode(mode).map((g) => g.id)).toEqual(['envelope', 'gimbal', 'timeouts']);
    }
  });

  it('tracks the mode as a document edit', () => {
    expect(TRACKED_PATHS).toContain('mission_mode');
  });
});

describe('isPathActive', () => {
  it('treats every flight path as active in the advanced mode, the turn rate excepted', () => {
    expect(isPathActive('advanced', 'rtl.timeout_sec')).toBe(true);
    expect(isPathActive('advanced', 'rtl.target_aruco_id')).toBe(true);
    expect(isPathActive('advanced', 'yaw_pattern.scan_vyaw')).toBe(false);
    expect(isPathActive('simple', 'yaw_pattern.scan_vyaw')).toBe(true);
  });

  it('deactivates the return and marker fields only without the return, in both modes', () => {
    const noRtl = { rtl: { enabled: false } };
    for (const mode of ['simple', 'advanced'] as const) {
      for (const path of ['rtl.target_aruco_id', 'rtl.tag_size', 'rtl.marker_dict', 'rtl.centering_timeout_sec', 'rtl.camera_tilt_deg', 'rtl.timeout_sec']) {
        expect(isPathActive(mode, path, noRtl)).toBe(false);
        expect(inactivePaths(mode, noRtl).has(path)).toBe(true);
      }
    }
    expect(isPathActive('simple', 'rtl.target_aruco_id')).toBe(true);
    expect(isPathActive('simple', 'rtl.centering_timeout_sec', { rtl: { enabled: true } })).toBe(true);
    expect(isPathActive('advanced', 'rtl.timeout_sec', { rtl: {} })).toBe(true);
  });
});

describe('stageLabel', () => {
  it('relabels the last stage by plan and leaves the others alone', () => {
    expect(stageLabel('advanced', 5, 'Retornando base')).toBe('Retorno à base');
    expect(stageLabel('simple', 5, 'Retornando base')).toBe('Pouso na base');
    expect(stageLabel('advanced', 5, 'Retornando base', false)).toBe('Pouso no local');
    expect(stageLabel('simple', 5, 'Retornando base', false)).toBe('Pouso no local');
    expect(stageLabel('simple', 2, 'Varredura', false)).toBe('Varredura');
  });
});

describe('parseMissionMode', () => {
  it('is strict: only the two modes, ignoring case and spaces; the removed one is not a mode', () => {
    expect(parseMissionMode('Simple')).toBe('simple');
    expect(parseMissionMode(' INTERMEDIATE ')).toBeNull();
    expect(parseMissionMode('expert')).toBeNull();
    expect(parseMissionMode(3)).toBeNull();
    expect(parseMissionMode(undefined)).toBeNull();
    expect(parseMissionMode('')).toBeNull();
  });
});

describe('hasInvalidMissionMode', () => {
  it('is false for an absent or blank mode and for a valid one', () => {
    expect(hasInvalidMissionMode({})).toBe(false);
    expect(hasInvalidMissionMode({ mission_mode: '' })).toBe(false);
    expect(hasInvalidMissionMode({ mission_mode: null })).toBe(false);
    expect(hasInvalidMissionMode({ mission_mode: 'Simple' })).toBe(false);
  });

  it('is true for a present value that is not one of the three', () => {
    expect(hasInvalidMissionMode({ mission_mode: 'expert' })).toBe(true);
    expect(hasInvalidMissionMode({ mission_mode: 3 })).toBe(true);
  });
});
