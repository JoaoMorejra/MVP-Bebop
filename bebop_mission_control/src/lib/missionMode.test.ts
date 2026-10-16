import { describe, expect, it } from 'vitest';
import { simpleAltitudeRange } from './missionMode';
import { PARAMETER_GROUPS, groupsForMode, TRACKED_PATHS } from './parameterSchema';
import {
  DEFAULT_MISSION_MODE,
  MISSION_MODES,
  hasInvalidMissionMode,
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
    expect(readMissionMode({ mission_mode: 'intermediate' })).toBe('intermediate');
    expect(readMissionMode({ mission_mode: 'expert' })).toBe('advanced');
    expect(readMissionMode({ mission_mode: 3 })).toBe('advanced');
  });

  it('trims and lowercases a string mode, as the mission does', () => {
    expect(readMissionMode({ mission_mode: 'Simple' })).toBe('simple');
    expect(readMissionMode({ mission_mode: ' INTERMEDIATE ' })).toBe('intermediate');
    expect(readMissionMode({ mission_mode: 'SIMPLE' })).toBe('simple');
    expect(readMissionMode({ mission_mode: '  ' })).toBe('advanced');
  });
});

describe('MISSION_MODES', () => {
  it('lists Simples, Intermediária, Avançada in that order', () => {
    expect(MISSION_MODES.map((m) => m.id)).toEqual(['simple', 'intermediate', 'advanced']);
    expect(MISSION_MODES.map((m) => m.label)).toEqual(['Simples', 'Intermediária', 'Avançada']);
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

  it('drops the return timeout and the turn rate in the intermediate mode', () => {
    const shown = paths('intermediate');
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

  it('drops the ceiling margin from the simple sheet and keeps the flight altitude', () => {
    const shown = paths('simple');
    expect(shown).not.toContain('kinematics.altitude_ceiling_margin_m');
    expect(shown).toContain('kinematics.target_altitude_m');
    expect(shown).toContain('kinematics.takeoff_stabilize_duration_sec');
    expect(paths('intermediate')).toContain('kinematics.altitude_ceiling_margin_m');
    expect(paths('advanced')).toContain('kinematics.altitude_ceiling_margin_m');
  });

  it('limits the simple flight altitude to the native takeoff height', () => {
    const altitude = (mode: Parameters<typeof groupsForMode>[0]) =>
      groupsForMode(mode).flatMap((g) => g.items).find((i) => i.path === 'kinematics.target_altitude_m')!;
    expect(altitude('simple')).toMatchObject({ min: 0.7, max: 1 });
    expect(altitude('advanced').max).toBe(4);
    expect(altitude('intermediate').max).toBe(4);
  });

  it('follows the pad: with platform.height_m = 1 the simple altitude slider is 1.7 to 2.0', () => {
    const altitude = groupsForMode('simple', { platform: { height_m: 1 } })
      .flatMap((g) => g.items)
      .find((i) => i.path === 'kinematics.target_altitude_m')!;
    expect(altitude).toMatchObject({ min: 1.7, max: 2 });
    const advanced = groupsForMode('advanced', { platform: { height_m: 1 } })
      .flatMap((g) => g.items)
      .find((i) => i.path === 'kinematics.target_altitude_m')!;
    expect(advanced).toMatchObject({ min: 0.1, max: 4 });
  });

  it('never rounds the range outside what the backend accepts, for pads with 3 decimals', () => {
    expect(simpleAltitudeRange({ platform: { height_m: 0 } })).toEqual({ min: 0.7, max: 1 });
    expect(simpleAltitudeRange({ platform: { height_m: 1 } })).toEqual({ min: 1.7, max: 2 });
    expect(simpleAltitudeRange({ platform: { height_m: 0.125 } })).toEqual({ min: 0.83, max: 1.12 });
    expect(simpleAltitudeRange({ platform: { height_m: 0.335 } })).toEqual({ min: 1.04, max: 1.33 });
  });

  it('ignores a non-finite pad', () => {
    expect(simpleAltitudeRange({ platform: { height_m: Number.NaN } })).toEqual({ min: 0.7, max: 1 });
    expect(simpleAltitudeRange({ platform: { height_m: 0.1 } })).toEqual({ min: 0.8, max: 1.1 });
    expect(simpleAltitudeRange(undefined)).toEqual({ min: 0.7, max: 1 });
  });

  it('marks the ceiling margin inactive only in the simple mode', () => {
    expect(isPathActive('simple', 'kinematics.altitude_ceiling_margin_m')).toBe(false);
    expect(isPathActive('intermediate', 'kinematics.altitude_ceiling_margin_m')).toBe(true);
  });

  it('keeps the three group cards in every mode', () => {
    for (const mode of ['simple', 'intermediate', 'advanced'] as const) {
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
    expect(isPathActive('intermediate', 'yaw_pattern.scan_vyaw')).toBe(false);
    expect(isPathActive('simple', 'yaw_pattern.scan_vyaw')).toBe(true);
  });

  it('deactivates the marker fields only in the intermediate mode', () => {
    expect(isPathActive('intermediate', 'rtl.target_aruco_id')).toBe(false);
    expect(isPathActive('intermediate', 'rtl.tag_size')).toBe(false);
    expect(isPathActive('simple', 'rtl.target_aruco_id')).toBe(true);
    expect(isPathActive('simple', 'rtl.centering_timeout_sec')).toBe(true);
  });
});

describe('stageLabel', () => {
  it('relabels the last stage by mode and leaves the others alone', () => {
    expect(stageLabel('advanced', 5, 'Retornando base')).toBe('Retornando base');
    expect(stageLabel('intermediate', 5, 'Retornando base')).toBe('Pouso no local');
    expect(stageLabel('simple', 5, 'Retornando base')).toBe('Pouso na base');
    expect(stageLabel('simple', 2, 'Varredura')).toBe('Varredura');
  });
});

describe('parseMissionMode', () => {
  it('is strict: only the three modes, ignoring case and spaces', () => {
    expect(parseMissionMode('Simple')).toBe('simple');
    expect(parseMissionMode(' INTERMEDIATE ')).toBe('intermediate');
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
