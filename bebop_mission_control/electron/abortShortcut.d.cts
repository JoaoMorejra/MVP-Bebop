export interface ElectronKeyInput {
  type?: string;
  key?: string;
  code?: string;
  control?: boolean;
  meta?: boolean;
  shift?: boolean;
  alt?: boolean;
  isAutoRepeat?: boolean;
}

export declare function isAbortShortcutInput(input: ElectronKeyInput | null | undefined): boolean;

export interface AbortShortcutGuard {
  setArmed(value: unknown): void;
  isArmed(): boolean;
  handle(event: { preventDefault: () => void }, input: ElectronKeyInput): boolean;
}

export declare function createAbortShortcutGuard(options: { deliver: () => void }): AbortShortcutGuard;
