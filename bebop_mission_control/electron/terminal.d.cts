export interface PtyLike {
  pid: number;
  cols: number;
  rows: number;
  write(data: string): void;
  resize(cols: number, rows: number): void;
  kill(): void;
  onData(listener: (data: string) => void): void;
  onExit(listener: (event: { exitCode: number; signal?: number }) => void): void;
}

export interface TerminalHost {
  spawn(options?: { cols?: number; rows?: number }):
    | {
        success: true;
        id: string;
        pid: number;
        cols: number;
        rows: number;
        cwd: string;
        shell: string;
        user: string;
        host: string;
        home: string;
      }
    | { success: false; error: string };
  write(id: string, data: string): { success: boolean };
  resize(id: string, cols: number, rows: number): { success: boolean; error?: string };
  kill(id: string): { success: boolean };
  killAll(): void;
  sessions: Map<string, PtyLike>;
}

export declare const RC_FILE: string;

export declare function createTerminalHost(options: {
  loadPty: () => { spawn: (file: string, args: string[], options: Record<string, unknown>) => PtyLike };
  env: () => Record<string, string | undefined>;
  cwd: string;
  activator: string;
  send: (channel: string, payload: object) => void;
  shell?: string;
  rcFile?: string;
  identity?: { user: string; host: string; home: string };
}): TerminalHost;
