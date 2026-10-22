import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { createAbortShortcutGuard, isAbortShortcutInput } from '../../electron/abortShortcut.cjs';

const keyDown = (overrides: Record<string, unknown>) => ({ type: 'keyDown', isAutoRepeat: false, ...overrides });

describe('isAbortShortcutInput', () => {
  it('takes Ctrl+C, Cmd+C (by physical key) and Escape', () => {
    expect(isAbortShortcutInput(keyDown({ key: 'c', code: 'KeyC', control: true }))).toBe(true);
    expect(isAbortShortcutInput(keyDown({ key: 'ç', code: 'KeyC', meta: true }))).toBe(true);
    expect(isAbortShortcutInput(keyDown({ key: 'Escape', code: 'Escape' }))).toBe(true);
  });

  it('leaves Ctrl+Shift+C, Alt combinations, a held key and key releases alone', () => {
    expect(isAbortShortcutInput(keyDown({ code: 'KeyC', control: true, shift: true }))).toBe(false);
    expect(isAbortShortcutInput(keyDown({ code: 'KeyC', control: true, alt: true }))).toBe(false);
    expect(isAbortShortcutInput(keyDown({ code: 'KeyC', control: true, isAutoRepeat: true }))).toBe(false);
    expect(isAbortShortcutInput({ type: 'keyUp', code: 'KeyC', control: true })).toBe(false);
    expect(isAbortShortcutInput(keyDown({ code: 'KeyV', control: true }))).toBe(false);
  });
});

describe('createAbortShortcutGuard', () => {
  it('consumes the shortcut and delivers it only while armed', () => {
    const deliver = vi.fn();
    const guard = createAbortShortcutGuard({ deliver });
    const event = { preventDefault: vi.fn() };
    expect(guard.handle(event, keyDown({ code: 'KeyC', control: true }))).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
    guard.setArmed(true);
    expect(guard.handle(event, keyDown({ code: 'KeyC', control: true }))).toBe(true);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(deliver).toHaveBeenCalledTimes(1);
    guard.setArmed('yes');
    expect(guard.isArmed()).toBe(false);
  });
});

describe('main.cjs and preload.cjs wiring', () => {
  const main = readFileSync(resolve(__dirname, '../../electron/main.cjs'), 'utf-8');
  const preload = readFileSync(resolve(__dirname, '../../electron/preload.cjs'), 'utf-8');

  it('listens to every window before its page sees the key and to the cockpit armed state', () => {
    expect(main).toContain("app.on('browser-window-created'");
    expect(main).toContain("'before-input-event'");
    expect(main).toContain("ipcMain.on('bmg:cockpit-abort-armed'");
    expect(main).toContain("send('bmg:abort-shortcut')");
  });

  it('exposes the two calls to the renderer', () => {
    expect(preload).toContain("ipcRenderer.send('bmg:cockpit-abort-armed'");
    expect(preload).toContain("onAbortShortcut: subscribe('bmg:abort-shortcut')");
  });
});
