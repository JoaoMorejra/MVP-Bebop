import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const require = createRequire(import.meta.url);
export const MAIN = resolve(__dirname, '../../../electron/main.cjs');

export type FakeChild = EventEmitter & {
  stdout: EventEmitter & { setEncoding: () => void };
  stderr: EventEmitter & { setEncoding: () => void };
  stdin: EventEmitter & { write: () => boolean; end: () => void; setEncoding: () => void };
  kill: () => boolean;
  pid: number;
};

export interface Spawned {
  cmd: string;
  args: string[];
  env: Record<string, string | undefined>;
  child: FakeChild;
}

/**
 * The real main.cjs, with `electron` and `child_process.spawn` stood in for, so
 * the arguments and environment each child would be given can be read, and its
 * output scripted, without starting one. `env` is applied to `process.env`
 * before the module loads (for example `BMG_MISSION_CONFIG`).
 */
export function loadMain(env: Record<string, string> = {}) {
  const Module = require('node:module') as {
    _load: (request: string, parent: { filename?: string } | undefined, isMain: boolean) => unknown;
  };
  const handlers: Record<string, (...args: unknown[]) => unknown> = {};
  const spawned: Spawned[] = [];
  const realChild = require('node:child_process');
  const previousEnv: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(env)) {
    previousEnv[key] = process.env[key];
    process.env[key] = value;
  }

  const fakeChild = (): FakeChild => {
    const child = new EventEmitter() as FakeChild;
    const stream = () => Object.assign(new EventEmitter(), { setEncoding: () => undefined });
    child.stdout = stream();
    child.stderr = stream();
    child.stdin = Object.assign(new EventEmitter(), { write: () => true, end: () => undefined, setEncoding: () => undefined });
    child.kill = () => true;
    child.pid = 4242;
    return child;
  };

  const original = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'electron') {
      return {
        app: { whenReady: () => new Promise(() => undefined), on: () => undefined, getPath: () => '/tmp', quit: () => undefined },
        BrowserWindow: function BrowserWindow() {},
        dialog: {},
        shell: {},
        ipcMain: { handle: (name: string, fn: (...args: unknown[]) => unknown) => (handlers[name] = fn), on: () => undefined },
      };
    }
    if (request === 'child_process' && parent?.filename === MAIN) {
      return {
        ...realChild,
        spawn: (cmd: string, args: string[], opts: { env: Record<string, string> }) => {
          const child = fakeChild();
          spawned.push({ cmd, args, env: opts?.env ?? {}, child });
          return child;
        },
      };
    }
    return original.call(this, request, parent, isMain);
  };
  delete require.cache[MAIN];
  require(MAIN);
  return {
    handlers,
    spawned,
    restore: () => {
      Module._load = original;
      for (const [key, value] of Object.entries(previousEnv)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    },
  };
}
