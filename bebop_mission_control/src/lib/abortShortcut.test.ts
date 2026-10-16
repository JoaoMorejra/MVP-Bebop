import { describe, expect, it } from 'vitest';
import { isAbortShortcut } from './abortShortcut';

const key = (over: Partial<Parameters<typeof isAbortShortcut>[0]>) =>
  isAbortShortcut({ key: '', code: '', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, repeat: false, ...over });

describe('isAbortShortcut', () => {
  it('recognises Escape', () => {
    expect(key({ key: 'Escape', code: 'Escape' })).toBe('escape');
  });

  it('recognises Ctrl+C and Cmd+C', () => {
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true })).toBe('copy');
    expect(key({ key: 'c', code: 'KeyC', metaKey: true })).toBe('copy');
  });

  it('reads the physical key, so a non-QWERTY layout still aborts', () => {
    expect(key({ key: 'ç', code: 'KeyC', ctrlKey: true })).toBe('copy');
    expect(key({ key: 'С', code: 'KeyC', ctrlKey: true })).toBe('copy');
  });

  it('ignores a plain C, and C with Shift or Alt (inspector, accents)', () => {
    expect(key({ key: 'c', code: 'KeyC' })).toBeNull();
    expect(key({ key: 'C', code: 'KeyC', ctrlKey: true, shiftKey: true })).toBeNull();
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true, altKey: true })).toBeNull();
  });

  it('ignores a held key', () => {
    expect(key({ key: 'c', code: 'KeyC', ctrlKey: true, repeat: true })).toBeNull();
    expect(key({ key: 'Escape', code: 'Escape', repeat: true })).toBeNull();
  });

  it('ignores other Ctrl combinations', () => {
    expect(key({ key: 'v', code: 'KeyV', ctrlKey: true })).toBeNull();
    expect(key({ key: 'x', code: 'KeyX', ctrlKey: true })).toBeNull();
  });
});
