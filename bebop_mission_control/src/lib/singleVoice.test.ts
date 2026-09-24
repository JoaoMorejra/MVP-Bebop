import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { cues } from '../audio/cues';
import { alertSentence } from './copilotPhrases';

const require = createRequire(import.meta.url);
const MAIN = resolve(__dirname, '../../electron/main.cjs');

interface Spawned {
  cmd: string;
  args: string[];
  env: Record<string, string | undefined>;
}

/**
 * The real main.cjs, with `electron` and `child_process.spawn` stood in for, so
 * the environment each child would be given can be read without starting one.
 */
function loadMain() {
  const Module = require('node:module') as {
    _load: (request: string, parent: { filename?: string } | undefined, isMain: boolean) => unknown;
  };
  const handlers: Record<string, (...args: unknown[]) => unknown> = {};
  const spawned: Spawned[] = [];
  const realChild = require('node:child_process');

  const fakeChild = () => {
    const child = new EventEmitter() as EventEmitter & Record<string, unknown>;
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
          spawned.push({ cmd, args, env: opts?.env ?? {} });
          return fakeChild();
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
    },
  };
}

describe('single-voice architecture', () => {
  let main: ReturnType<typeof loadMain>;

  beforeAll(() => {
    main = loadMain();
  });
  afterAll(() => {
    main.restore();
  });

  it('silences the mission process and only it', async () => {
    const started = (await main.handlers['bmg:start-mission']({}, {
      countdown: 0,
      noFly: true,
      paramsJson: '{}',
    })) as { success: boolean };
    expect(started.success).toBe(true);

    const mission = main.spawned.find((child) => child.args.some((arg) => arg.endsWith('mission.py')));
    const daemon = main.spawned.find((child) => child.args.some((arg) => arg.includes('--serve')));
    expect(mission?.env.BMG_GCS_SESSION).toBe('1');
    expect(daemon, 'the copilot daemon is started alongside the mission').toBeDefined();
    expect(daemon?.env.BMG_GCS_SESSION).toBeUndefined();
  });

  it('plays no synthetic cues', () => {
    expect(cues.isEnabled()).toBe(false);
  });
});

describe('alertSentence', () => {
  it('reads the sentence the mission composed for the fault', () => {
    expect(alertSentence({ text: ' Alerta de voo: odometria perdida. Executando pouso seguro imediatamente. ' })).toBe(
      'Alerta de voo: odometria perdida. Executando pouso seguro imediatamente.'
    );
  });

  it('falls back to a generic call without one', () => {
    for (const payload of [{}, { text: '' }, { text: 12 }, null, undefined]) {
      expect(alertSentence(payload as never)).toBe('Alerta de voo. Executando pouso seguro.');
    }
  });
});
