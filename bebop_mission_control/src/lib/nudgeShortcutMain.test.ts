import { describe, expect, it, vi } from 'vitest';
import { createNudgeShortcutGuard, isNudgeKeyInput } from '../../electron/nudgeShortcut.cjs';

const down = (code: string, overrides: Record<string, unknown> = {}) => ({ type: 'keyDown', code, isAutoRepeat: false, ...overrides });
const up = (code: string) => ({ type: 'keyUp', code });

describe('isNudgeKeyInput', () => {
  it('takes W A S D Q R by physical key and nothing with a modifier', () => {
    for (const code of ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyR']) expect(isNudgeKeyInput(down(code))).toBe(true);
    expect(isNudgeKeyInput(down('KeyR', { control: true }))).toBe(false);
    expect(isNudgeKeyInput(down('KeyQ', { alt: true }))).toBe(false);
    expect(isNudgeKeyInput(down('KeyW', { meta: true }))).toBe(false);
    expect(isNudgeKeyInput(down('KeyC'))).toBe(false);
    expect(isNudgeKeyInput(down('Escape'))).toBe(false);
  });
});

describe('createNudgeShortcutGuard', () => {
  it('passes every key untouched while disarmed', () => {
    const deliver = vi.fn();
    const guard = createNudgeShortcutGuard({ deliver });
    const event = { preventDefault: vi.fn() };
    expect(guard.handle(event, down('KeyW'))).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(deliver).not.toHaveBeenCalled();
  });

  it('consumes and delivers the keys while armed, once per press', () => {
    const deliver = vi.fn();
    const guard = createNudgeShortcutGuard({ deliver });
    guard.setArmed(true);
    const event = { preventDefault: vi.fn() };
    expect(guard.handle(event, down('KeyW'))).toBe(true);
    expect(guard.handle(event, down('KeyW', { isAutoRepeat: true }))).toBe(true);
    expect(guard.handle(event, up('KeyW'))).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledTimes(3);
    expect(deliver.mock.calls.map(([key]) => key)).toEqual([
      { type: 'keydown', code: 'KeyW' },
      { type: 'keyup', code: 'KeyW' },
    ]);
  });

  it('still delivers the release of a key held when the nudge was disarmed', () => {
    const deliver = vi.fn();
    const guard = createNudgeShortcutGuard({ deliver });
    guard.setArmed(true);
    guard.handle({ preventDefault: vi.fn() }, down('KeyD'));
    guard.setArmed(false);
    expect(guard.handle({ preventDefault: vi.fn() }, up('KeyD'))).toBe(true);
    expect(deliver).toHaveBeenLastCalledWith({ type: 'keyup', code: 'KeyD' });
    expect(guard.handle({ preventDefault: vi.fn() }, down('KeyD'))).toBe(false);
  });

  it('leaves Ctrl+R, Ctrl+C and Escape to their own handlers', () => {
    const deliver = vi.fn();
    const guard = createNudgeShortcutGuard({ deliver });
    guard.setArmed(true);
    for (const input of [down('KeyR', { control: true }), down('KeyC', { control: true }), down('Escape')]) {
      expect(guard.handle({ preventDefault: vi.fn() }, input)).toBe(false);
    }
    expect(deliver).not.toHaveBeenCalled();
  });
});
