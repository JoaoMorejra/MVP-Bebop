import { describe, expect, it, vi } from 'vitest';
import { SPEECH_MS_KEY, estimateSpeechMs, readSpeechMs, recordSpeechMs } from './speechTiming';

class MemoryStorage {
  data = new Map<string, string>();
  getItem(key: string) {
    return this.data.has(key) ? this.data.get(key)! : null;
  }
  setItem(key: string, value: string) {
    this.data.set(key, value);
  }
}

describe('speechTiming', () => {
  it('estimate is clamped at both ends', () => {
    expect(estimateSpeechMs('')).toBe(1500);
    expect(estimateSpeechMs('ok')).toBe(1500);
    expect(estimateSpeechMs('x'.repeat(1000))).toBe(14000);
  });

  it('estimate grows with text length', () => {
    const short = estimateSpeechMs('Decolagem autorizada, iniciando a missão.');
    const long = estimateSpeechMs('Decolagem autorizada, iniciando a missão com varredura lateral e retorno.');
    expect(long).toBeGreaterThan(short);
    expect(short).toBe(Math.round('Decolagem autorizada, iniciando a missão.'.length * 68));
  });

  it('record then read returns the measured ms', () => {
    const storage = new MemoryStorage();
    recordSpeechMs(storage, 'Linha', 2900);
    expect(readSpeechMs(storage, 'Linha')).toBe(2900);
    expect(readSpeechMs(storage, 'Outra')).toBeNull();
    expect(storage.getItem(SPEECH_MS_KEY)).not.toBeNull();
  });

  it('record ignores implausible values', () => {
    const storage = new MemoryStorage();
    recordSpeechMs(storage, 'Linha', 120);
    recordSpeechMs(storage, 'Linha', 20000);
    recordSpeechMs(storage, 'Linha', Number.NaN);
    expect(readSpeechMs(storage, 'Linha')).toBeNull();
  });

  it('record caps the store at 32 texts', () => {
    const storage = new MemoryStorage();
    for (let i = 0; i < 40; i += 1) recordSpeechMs(storage, `linha ${i}`, 1000 + i);
    const stored = JSON.parse(storage.getItem(SPEECH_MS_KEY)!);
    expect(Object.keys(stored)).toHaveLength(32);
    expect(readSpeechMs(storage, 'linha 39')).toBe(1039);
    expect(readSpeechMs(storage, 'linha 0')).toBeNull();
  });

  it('corrupt storage reads as null and never throws', () => {
    const storage = new MemoryStorage();
    storage.setItem(SPEECH_MS_KEY, '{not json');
    expect(readSpeechMs(storage, 'Linha')).toBeNull();
    storage.setItem(SPEECH_MS_KEY, JSON.stringify({ Linha: 'muito' }));
    expect(readSpeechMs(storage, 'Linha')).toBeNull();
    expect(readSpeechMs(null, 'Linha')).toBeNull();
  });

  it('a storage that throws on write is tolerated', () => {
    const storage = {
      getItem: () => null,
      setItem: vi.fn(() => {
        throw new Error('quota');
      }),
    };
    expect(() => recordSpeechMs(storage, 'Linha', 2000)).not.toThrow();
    expect(() => recordSpeechMs(null, 'Linha', 2000)).not.toThrow();
  });
});
