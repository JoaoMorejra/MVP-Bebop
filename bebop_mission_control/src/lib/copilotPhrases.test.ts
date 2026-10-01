import { describe, expect, it } from 'vitest';
import {
  DESCRIPTIVE_KEYS,
  FLIGHT_CALL_MAX_SECONDS,
  HISTORY_STORAGE_KEY,
  HISTORY_WINDOW,
  FIXED_POOLS,
  MILESTONE_KEYS,
  PHRASE_POOLS,
  POOL_KEYS,
  phraseForMilestone,
  type HistoryStore,
  type MilestoneKey,
  type StorageLike,
  describeTargetLocation,
  estimateSpeechSeconds,
  loadHistory,
  nextPhrase,
  pickVariant,
  recordVariant,
  reserveLaunchPhrase,
  takeLaunchPhrase,
  renderPhrase,
  saveHistory,
  spokenMeters,
  spokenSpeed,
  placeholdersOf,
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
  'mission.touchdown',
  'mission.battery_warning',
  'inspection.intro',
  'inspection.outro',
];

describe('phrase pools', () => {
  it('cover exactly the synchronisation table keys', () => {
    expect([...MILESTONE_KEYS].sort()).toEqual([...SPEC_KEYS].sort());
    for (const key of MILESTONE_KEYS) expect(POOL_KEYS).toContain(key);
  });

  it('hold more variants than the history window, so exclusion never empties a pool', () => {
    for (const key of POOL_KEYS) {
      if (FIXED_POOLS.includes(key)) continue;
      expect(PHRASE_POOLS[key].length, key).toBeGreaterThan(HISTORY_WINDOW);
    }
  });

  it('hold distinct, non-empty variants', () => {
    for (const key of POOL_KEYS) {
      const pool = PHRASE_POOLS[key];
      expect(new Set(pool).size, key).toBe(pool.length);
      for (const line of pool) expect(line.trim().length, key).toBeGreaterThan(0);
    }
  });

  it('offer takeoff variants with the configured altitude and without any number', () => {
    const pool = PHRASE_POOLS['mission.takeoff'];
    expect(pool.filter((line) => line.includes('{altitude}')).length).toBeGreaterThan(HISTORY_WINDOW);
    expect(pool.filter((line) => !/\{[a-z_]+\}/.test(line)).length).toBeGreaterThanOrEqual(2);
  });

  it('place the target location in every target-found variant', () => {
    for (const line of PHRASE_POOLS['mission.target_found']) expect(line).toContain('{location}');
  });

  it('keep every flight call within four spoken seconds at its longest rendering', () => {
    const longest = {
      altitude: spokenMeters(12.5),
      location: describeTargetLocation({ forward_m: 12.5, bearing_deg: 5 }),
      speed: spokenSpeed(0.35) ?? '',
      threshold: '100 por cento',
    };
    for (const key of POOL_KEYS) {
      if (DESCRIPTIVE_KEYS.includes(key)) continue;
      for (const line of PHRASE_POOLS[key]) {
        const rendered = renderPhrase(line, longest);
        expect(estimateSpeechSeconds(rendered), `${key}: ${rendered}`).toBeLessThanOrEqual(
          FLIGHT_CALL_MAX_SECONDS
        );
      }
    }
  });

  it('leave no placeholder unresolved in keys that take none', () => {
    for (const key of POOL_KEYS) {
      if (['mission.takeoff', 'mission.target_found', 'mission.scan_start', 'mission.battery_warning'].includes(key)) continue;
      for (const line of PHRASE_POOLS[key]) expect(line, key).not.toMatch(/\{[a-z_]+\}/);
    }
  });

  it('never count seconds in the countdown call, which lands after synthesis latency', () => {
    for (const line of PHRASE_POOLS['mission.countdown_3']) {
      expect(line).not.toMatch(/segundo|\b(um|dois|tr[eê]s|quatro|cinco)\b|\d/i);
    }
  });
});

describe('estimateSpeechSeconds', () => {
  it('counts words at two and a half per second', () => {
    expect(estimateSpeechSeconds('Iniciando retorno à base.')).toBe(1.6);
    expect(estimateSpeechSeconds('  um   dois\ttres \n')).toBe(1.2);
    expect(estimateSpeechSeconds('')).toBe(0);
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

describe('launch phrase reservation', () => {
  it('holds one drawn line per launch key until the milestone takes it', () => {
    const storage = memoryStorage();
    const first = reserveLaunchPhrase('mission.countdown_3', { storage, random: seeded(1) });
    expect(PHRASE_POOLS['mission.countdown_3']).toContain(first);
    expect(reserveLaunchPhrase('mission.countdown_3', { storage, random: seeded(2) })).toBe(first);
    expect(takeLaunchPhrase('mission.countdown_3')).toBe(first);
    expect(takeLaunchPhrase('mission.countdown_3')).toBeUndefined();
  });

  it('records the draw in the cross-flight history once', () => {
    const storage = memoryStorage();
    reserveLaunchPhrase('mission.start', { storage, random: seeded(3) });
    reserveLaunchPhrase('mission.start', { storage, random: seeded(4) });
    const history = loadHistory(storage);
    expect(history['mission.start']).toHaveLength(1);
    takeLaunchPhrase('mission.start');
  });

  it('never hands over a line for a key with placeholders', () => {
    expect(takeLaunchPhrase('mission.takeoff')).toBeUndefined();
  });
});

describe('parameter-sourced numbers (D2)', () => {
  it('speaks speeds with two decimals and the formal singular below two', () => {
    expect(spokenSpeed(0.35)).toBe('0,35 metro por segundo');
    expect(spokenSpeed(0.2)).toBe('0,2 metro por segundo');
    expect(spokenSpeed(2.5)).toBe('2,5 metros por segundo');
    expect(spokenSpeed(Number.NaN)).toBeNull();
  });

  it('lists the placeholders a variant needs', () => {
    expect(placeholdersOf('Subindo para {altitude} a {speed}.')).toEqual(['altitude', 'speed']);
    expect(placeholdersOf('Sem números.')).toEqual([]);
  });

  it('never draws a variant whose number the mission did not send', () => {
    for (let seed = 1; seed <= 30; seed += 1) {
      const line = nextPhrase('mission.scan_start', {}, { storage: null, random: seeded(seed) });
      expect(line).not.toMatch(/\{|por segundo/);
    }
  });

  it('draws the speed variants when the mission sent the configured speed', () => {
    const lines = new Set<string>();
    for (let step = 0; step < 20; step += 1) {
      lines.add(nextPhrase('mission.scan_start', { speed: spokenSpeed(0.35) ?? '' }, { storage: null, random: () => step / 20 }));
    }
    expect([...lines].some((line) => line.includes('0,35 metro por segundo'))).toBe(true);
  });
});

describe('touchdown (3.2)', () => {
  const opts = { storage: null, random: () => 0.5 };

  it('says "na base" only for a confirmed touchdown at the base', () => {
    for (let step = 0; step < 10; step += 1) {
      const random = () => step / 10;
      const atBase = phraseForMilestone('mission.touchdown', { confirmed: true, at_base: true }, {}, { storage: null, random });
      expect(atBase).toMatch(/base/);
      const inPlace = phraseForMilestone('mission.touchdown', { confirmed: true, at_base: false }, {}, { storage: null, random });
      expect(inPlace).not.toMatch(/base/);
      expect(inPlace).toMatch(/[Pp]ouso/);
    }
  });

  it('an unconfirmed touchdown says so, in the specified words', () => {
    expect(phraseForMilestone('mission.touchdown', { confirmed: false, at_base: true }, {}, opts)).toBe(
      'Pouso comandado. Confirmação de toque indisponível. Verifique visualmente.'
    );
  });

  it('a payload without the confirmation flag is not a confirmed touchdown', () => {
    expect(phraseForMilestone('mission.touchdown', {}, {}, opts)).toBe(
      'Pouso comandado. Confirmação de toque indisponível. Verifique visualmente.'
    );
  });
});

describe('countdown call (3.4)', () => {
  it('claims no checklist, validation or readiness the mission never performed', () => {
    for (const line of PHRASE_POOLS['mission.countdown_3']) {
      expect(line).not.toMatch(/checklist|valida|prontos?\b/i);
    }
  });
});

describe('conditional phrases (3.5)', () => {
  const every = (key: MilestoneKey, payload: Record<string, unknown>) =>
    Array.from({ length: 12 }, (_, step) =>
      phraseForMilestone(key, payload, {}, { storage: null, random: () => step / 12 })
    );

  it('a capture without a confirmed target says so and claims no target', () => {
    for (const line of every('mission.capture_done', { target_confirmed: false, settled: false })) {
      expect(line).toMatch(/sem alvo confirmado/);
      expect(line).not.toMatch(/sinistro|pericial/i);
    }
  });

  it('no capture claims high fidelity before a native photo exists', () => {
    for (const payload of [{ target_confirmed: true, settled: true }, { target_confirmed: false }]) {
      for (const line of every('mission.capture_done', payload)) expect(line).not.toMatch(/alta fidelidade/i);
    }
  });

  it('only an inspected return says the inspection is done or that it leaves the scene', () => {
    for (const payload of [
      { via_jump: true, inspected: true, marker_guided: true },
      { via_jump: false, inspected: false, marker_guided: true },
      {},
    ]) {
      for (const line of every('mission.rtl_start', payload)) {
        expect(line).not.toMatch(/Inspeção concluída|Deixando o local/);
      }
    }
    const inspected = every('mission.rtl_start', { via_jump: false, inspected: true, marker_guided: true });
    expect(inspected.some((line) => /Inspeção concluída|Deixando o local/.test(line))).toBe(true);
  });

  it('a return without the marker never mentions the marker', () => {
    for (const payload of [{ inspected: true, marker_guided: false }, { via_jump: true }]) {
      for (const line of every('mission.rtl_start', payload)) expect(line).not.toMatch(/marcador/i);
    }
  });

  it('a jump to the return says the station commanded it', () => {
    for (const line of every('mission.rtl_start', { via_jump: true })) expect(line).toMatch(/comandad/);
  });

  it('a landing away from the base never says base', () => {
    for (const line of every('mission.landing', { at_base: false })) expect(line).not.toMatch(/base/i);
    for (const line of every('mission.landing', {})) expect(line).not.toMatch(/base/i);
  });

  it('an approach without IBVS claims no visual guidance', () => {
    for (const line of every('mission.approaching', { ibvs: false })) {
      expect(line).not.toMatch(/visual|Centralizando/i);
    }
    const guided = every('mission.approaching', { ibvs: true });
    expect(guided.some((line) => /visual/i.test(line))).toBe(true);
  });
});

describe('battery warning (3.7)', () => {
  it('is narrated, citing the configured threshold and never the reading', () => {
    expect(MILESTONE_KEYS).toContain('mission.battery_warning');
    const lines = Array.from({ length: 10 }, (_, step) =>
      phraseForMilestone('mission.battery_warning', { battery_pct: 13.4, threshold_pct: 15 }, {}, { storage: null, random: () => step / 10 })
    );
    for (const line of lines) {
      expect(line).toMatch(/15 por cento/);
      expect(line).not.toMatch(/13/);
    }
  });

  it('without a threshold in the payload, speaks no number', () => {
    const line = phraseForMilestone('mission.battery_warning', { battery_pct: 13.4 }, {}, { storage: null, random: () => 0 });
    expect(line).not.toMatch(/\d/);
    expect(line).toMatch(/[Bb]ateria/);
  });
});
