import { describe, expect, it } from 'vitest';
import {
  HISTORY_STORAGE_KEY,
  HISTORY_WINDOW,
  MILESTONE_KEYS,
  PHRASE_POOLS,
  type HistoryStore,
  type MilestoneKey,
  type StorageLike,
  describeTargetLocation,
  loadHistory,
  nextPhrase,
  pickVariant,
  recordVariant,
  renderPhrase,
  saveHistory,
  spokenMeters,
} from './copilotPhrases';

/** Deterministic PRNG, so a failure is reproducible rather than flaky. */
function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
}

function memoryStorage(initial: Record<string, string> = {}): StorageLike & { data: Map<string, string> } {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

const SPEC_KEYS: MilestoneKey[] = [
  'mission.start',
  'mission.countdown_3',
  'mission.takeoff',
  'mission.scan_start',
  'mission.target_found',
  'mission.approaching',
  'mission.capture_done',
  'mission.rtl_start',
  'mission.landing',
  'inspection.intro',
  'inspection.outro',
];

describe('phrase pools', () => {
  it('cover exactly the synchronisation table keys', () => {
    expect([...MILESTONE_KEYS].sort()).toEqual([...SPEC_KEYS].sort());
    expect(Object.keys(PHRASE_POOLS).sort()).toEqual([...SPEC_KEYS].sort());
  });

  it('hold more variants than the history window, so exclusion never empties a pool', () => {
    for (const key of MILESTONE_KEYS) {
      expect(PHRASE_POOLS[key].length, key).toBeGreaterThan(HISTORY_WINDOW);
    }
  });

  it('hold distinct, non-empty variants', () => {
    for (const key of MILESTONE_KEYS) {
      const pool = PHRASE_POOLS[key];
      expect(new Set(pool).size, key).toBe(pool.length);
      for (const line of pool) expect(line.trim().length, key).toBeGreaterThan(0);
    }
  });

  it('cite the configured altitude in every takeoff variant', () => {
    for (const line of PHRASE_POOLS['mission.takeoff']) expect(line).toContain('{altitude}');
  });

  it('place the target location in every target-found variant', () => {
    for (const line of PHRASE_POOLS['mission.target_found']) expect(line).toContain('{location}');
  });

  it('leave no placeholder unresolved in keys that take none', () => {
    for (const key of MILESTONE_KEYS) {
      if (key === 'mission.takeoff' || key === 'mission.target_found') continue;
      for (const line of PHRASE_POOLS[key]) expect(line, key).not.toMatch(/\{[a-z_]+\}/);
    }
  });
});

describe('pickVariant', () => {
  it('never repeats a variant inside any sliding window of five over ten flights', () => {
    for (const key of MILESTONE_KEYS) {
      const random = seeded(42);
      let history: HistoryStore = {};
      const picks: number[] = [];
      for (let flight = 0; flight < 10; flight++) {
        const { index } = pickVariant(key, PHRASE_POOLS[key], history, random);
        picks.push(index);
        history = recordVariant(history, key, index);
      }
      for (let start = 0; start + HISTORY_WINDOW <= picks.length; start++) {
        const window = picks.slice(start, start + HISTORY_WINDOW);
        expect(new Set(window).size, `${key} window ${window.join(',')}`).toBe(HISTORY_WINDOW);
      }
    }
  });

  it('excludes every variant used in the last five flights', () => {
    const pool = ['a', 'b', 'c', 'd', 'e', 'f', 'g'];
    const history: HistoryStore = { 'mission.landing': [0, 1, 2, 3, 4] };
    for (let draw = 0; draw < 50; draw++) {
      const { index } = pickVariant('mission.landing', pool, history, seeded(draw));
      expect([5, 6]).toContain(index);
    }
  });

  it('degrades on a pool no larger than the window without ever repeating the last pick', () => {
    const pool = ['a', 'b', 'c'];
    let history: HistoryStore = {};
    let previous = -1;
    const random = seeded(7);
    for (let flight = 0; flight < 30; flight++) {
      const { index } = pickVariant('mission.landing', pool, history, random);
      expect(index).not.toBe(previous);
      previous = index;
      history = recordVariant(history, 'mission.landing', index);
    }
  });

  it('serves a single-variant pool without error', () => {
    const history: HistoryStore = { 'mission.landing': [0, 0, 0] };
    expect(pickVariant('mission.landing', ['only'], history)).toEqual({ text: 'only', index: 0 });
  });

  it('ignores recorded indices the pool no longer has', () => {
    const history: HistoryStore = { 'mission.landing': [9, 12] };
    const { index } = pickVariant('mission.landing', ['a', 'b'], history, () => 0);
    expect(index).toBe(0);
  });

  it('refuses an empty pool', () => {
    expect(() => pickVariant('mission.landing', [], {})).toThrow(RangeError);
  });
});

describe('recordVariant', () => {
  it('keeps the last five flights, oldest first out', () => {
    let history: HistoryStore = {};
    for (const index of [0, 1, 2, 3, 4, 5]) history = recordVariant(history, 'mission.takeoff', index);
    expect(history['mission.takeoff']).toEqual([1, 2, 3, 4, 5]);
  });

  it('does not mutate the store it is given', () => {
    const history: HistoryStore = { 'mission.takeoff': [1] };
    recordVariant(history, 'mission.takeoff', 2);
    expect(history).toEqual({ 'mission.takeoff': [1] });
  });
});

describe('history persistence', () => {
  it('round-trips through storage under the versioned key', () => {
    const storage = memoryStorage();
    const history: HistoryStore = { 'mission.takeoff': [3, 1], 'inspection.outro': [0] };
    expect(saveHistory(history, storage)).toBe(true);
    expect(storage.data.has(HISTORY_STORAGE_KEY)).toBe(true);
    expect(loadHistory(storage)).toEqual(history);
  });

  it('reads a corrupt document as an empty history', () => {
    expect(loadHistory(memoryStorage({ [HISTORY_STORAGE_KEY]: '{not json' }))).toEqual({});
    expect(loadHistory(memoryStorage({ [HISTORY_STORAGE_KEY]: '[1,2]' }))).toEqual({});
  });

  it('drops unknown keys, non-integer entries and anything beyond the window', () => {
    const stored = JSON.stringify({
      'mission.takeoff': [0, 1, 2, 3, 4, 5, 6],
      'mission.landing': [1, 'x', -2, 1.5, 3],
      'mission.bogus': [1],
      'mission.rtl_start': 'nope',
    });
    expect(loadHistory(memoryStorage({ [HISTORY_STORAGE_KEY]: stored }))).toEqual({
      'mission.takeoff': [2, 3, 4, 5, 6],
      'mission.landing': [1, 3],
    });
  });

  it('survives a storage that throws, and reports the failed write', () => {
    const hostile: StorageLike = {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
    };
    expect(loadHistory(hostile)).toEqual({});
    expect(saveHistory({ 'mission.takeoff': [1] }, hostile)).toBe(false);
  });

  it('works with no storage at all', () => {
    expect(loadHistory(null)).toEqual({});
    expect(saveHistory({}, null)).toBe(false);
  });
});

describe('nextPhrase', () => {
  it('six consecutive flights never repeat the takeoff call inside the window', () => {
    const storage = memoryStorage();
    const random = seeded(3);
    const lines: string[] = [];
    for (let flight = 0; flight < 6; flight++) {
      lines.push(nextPhrase('mission.takeoff', { altitude: spokenMeters(1.5) }, { storage, random }));
    }
    for (let start = 0; start + HISTORY_WINDOW <= lines.length; start++) {
      expect(new Set(lines.slice(start, start + HISTORY_WINDOW)).size).toBe(HISTORY_WINDOW);
    }
    for (const line of lines) expect(line).toContain('1,5 metro');
  });
});

describe('rendering', () => {
  it('substitutes named placeholders and leaves unknown ones untouched', () => {
    expect(renderPhrase('Subindo para {altitude}. {other}', { altitude: '2 metros' })).toBe(
      'Subindo para 2 metros. {other}'
    );
  });

  it('speaks metres the way the copilot pronounces them', () => {
    expect(spokenMeters(1)).toBe('1 metro');
    expect(spokenMeters(1.5)).toBe('1,5 metro');
    expect(spokenMeters(2)).toBe('2 metros');
    expect(spokenMeters(1.04)).toBe('1 metro');
    expect(spokenMeters(Number.NaN)).toBe('altitude configurada');
  });

  it('describes the target from the milestone payload, and falls back without geometry', () => {
    expect(describeTargetLocation({ forward_m: 2.7, bearing_deg: 3.6 })).toBe(
      'a 2,7 metros à frente, levemente à direita'
    );
    expect(describeTargetLocation({ forward_m: 4, bearing_deg: -15 })).toBe(
      'a 4 metros à frente, à esquerda'
    );
    expect(describeTargetLocation({ forward_m: 1.2, bearing_deg: 0.5 })).toBe('a 1,2 metro à frente');
    expect(describeTargetLocation({ forward_m: null, bearing_deg: null })).toBe('na pista');
    expect(describeTargetLocation(undefined)).toBe('na pista');
  });
});
