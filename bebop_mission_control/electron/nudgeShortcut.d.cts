import type { ElectronKeyInput } from './abortShortcut.cjs';

export declare function isNudgeKeyInput(input: ElectronKeyInput | null | undefined): boolean;

export interface NudgeShortcutGuard {
  setArmed(value: unknown): void;
  isArmed(): boolean;
  handle(event: { preventDefault: () => void }, input: ElectronKeyInput): boolean;
}

export declare function createNudgeShortcutGuard(options: {
  deliver: (key: { type: 'keydown' | 'keyup'; code: string }) => void;
}): NudgeShortcutGuard;
