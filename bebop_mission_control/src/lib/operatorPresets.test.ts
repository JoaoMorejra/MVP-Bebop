import { describe, expect, it } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import { FAVORITE_NAME, latestPreset, presetValues, singleFavorite, type OperatorPreset } from './operatorPresets';

const preset = (id: string, savedAt: string): OperatorPreset => ({ id, name: id, savedAt, values: {} });

describe('latestPreset', () => {
  it('latestPreset returns null for an empty list', () => {
    expect(latestPreset([])).toBeNull();
  });

  it('latestPreset picks the most recent savedAt among several', () => {
    const list = [
      preset('a', '2026-01-01T00:00:00.000Z'),
      preset('b', '2026-03-01T00:00:00.000Z'),
      preset('c', '2026-02-01T00:00:00.000Z'),
    ];
    expect(latestPreset(list)?.id).toBe('b');
  });

  it('breaks a tie by the order of the list', () => {
    const list = [preset('a', '2026-01-01T00:00:00.000Z'), preset('b', '2026-01-01T00:00:00.000Z')];
    expect(latestPreset(list)?.id).toBe('a');
  });
});

describe('singleFavorite', () => {
  it('singleFavorite returns exactly one item named Favorito with the mode-owned values of the doc', () => {
    const doc = completeDocument({ 'kinematics.target_altitude_m': 1.7, 'lateral_pid.kp': 9 });
    const now = new Date('2026-10-27T12:00:00.000Z');
    const list = singleFavorite(doc, 'fav-1', now);
    expect(list).toHaveLength(1);
    expect(list[0]).toEqual({
      id: 'fav-1',
      name: FAVORITE_NAME,
      savedAt: now.toISOString(),
      values: presetValues(doc),
    });
    expect(FAVORITE_NAME).toBe('Favorito');
    expect(list[0].values['kinematics.target_altitude_m']).toBe(1.7);
    expect('lateral_pid.kp' in list[0].values).toBe(false);
  });
});
