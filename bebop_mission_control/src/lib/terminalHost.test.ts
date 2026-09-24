import { describe, expect, it } from 'vitest';
import { RC_FILE, createTerminalHost, type PtyLike } from '../../electron/terminal.cjs';

function fakePty() {
  const spawned: Array<{ file: string; args: string[]; options: Record<string, unknown>; term: PtyLike & Record<string, unknown> }> = [];
  const module = {
    spawn(file: string, args: string[], options: Record<string, unknown>) {
      const listeners: { data?: (d: string) => void; exit?: (e: { exitCode: number; signal?: number }) => void } = {};
      const term = {
        pid: 900 + spawned.length,
        cols: Number(options.cols),
        rows: Number(options.rows),
        written: [] as string[],
        killed: false,
        write(data: string) {
          this.written.push(data);
        },
        resize(cols: number, rows: number) {
          this.cols = cols;
          this.rows = rows;
        },
        kill() {
          this.killed = true;
        },
        onData(listener: (d: string) => void) {
          listeners.data = listener;
        },
        onExit(listener: (e: { exitCode: number; signal?: number }) => void) {
          listeners.exit = listener;
        },
        emit: (d: string) => listeners.data?.(d),
        exit: (code: number) => listeners.exit?.({ exitCode: code }),
      };
      spawned.push({ file, args, options, term });
      return term;
    },
  };
  return { module, spawned };
}

function host() {
  const pty = fakePty();
  const sent: Array<[string, Record<string, unknown>]> = [];
  const terminal = createTerminalHost({
    loadPty: () => pty.module,
    env: () => ({ ROS_DOMAIN_ID: '14', PATH: '/usr/bin', TERM: 'dumb' }),
    cwd: '/home/op/ros2_ws',
    activator: '/home/op/ros2_ws/bin/nectar-activate',
    send: (channel, payload) => sent.push([channel, payload as Record<string, unknown>]),
  });
  return { terminal, pty, sent };
}

describe('terminal host', () => {
  it('starts an interactive bash on the rcfile, in the mission environment', () => {
    const { terminal, pty } = host();
    const result = terminal.spawn({ cols: 132, rows: 40 });

    expect(result).toMatchObject({ success: true, id: 'pty-1', cols: 132, rows: 40 });
    const [{ file, args, options }] = pty.spawned;
    expect(file).toBe('/bin/bash');
    expect(args).toEqual(['--rcfile', RC_FILE, '-i']);
    expect(options.cwd).toBe('/home/op/ros2_ws');
    expect(options.env).toMatchObject({
      ROS_DOMAIN_ID: '14',
      TERM: 'xterm-256color',
      BMG_NECTAR_ACTIVATE: '/home/op/ros2_ws/bin/nectar-activate',
    });
  });

  it('writes raw bytes, Tab, arrows and Ctrl+C included', () => {
    const { terminal, pty } = host();
    const { id } = terminal.spawn() as { id: string };
    for (const bytes of ['ros2 to', '\t', '\x1b[A', '\x03']) terminal.write(id, bytes);
    expect(pty.spawned[0].term.written).toEqual(['ros2 to', '\t', '\x1b[A', '\x03']);
  });

  it('streams output and the exit to the renderer, tagged with the session', () => {
    const { terminal, pty, sent } = host();
    const { id } = terminal.spawn() as { id: string };
    (pty.spawned[0].term.emit as (d: string) => void)('\x1b[1;32moperator@bmg\x1b[0m$ ');
    (pty.spawned[0].term.exit as (c: number) => void)(0);

    expect(sent).toEqual([
      ['bmg:terminal-data', { id, data: '\x1b[1;32moperator@bmg\x1b[0m$ ' }],
      ['bmg:terminal-exit', { id, exitCode: 0, signal: null }],
    ]);
    expect(terminal.write(id, 'ls\r').success).toBe(false);
  });

  it('resizes within sane bounds', () => {
    const { terminal, pty } = host();
    const { id } = terminal.spawn() as { id: string };
    terminal.resize(id, 100, 30);
    expect([pty.spawned[0].term.cols, pty.spawned[0].term.rows]).toEqual([100, 30]);
    terminal.resize(id, 1, 100000);
    expect([pty.spawned[0].term.cols, pty.spawned[0].term.rows]).toEqual([20, 300]);
  });

  it('kills one session or all of them', () => {
    const { terminal, pty } = host();
    const a = terminal.spawn() as { id: string };
    terminal.spawn();
    expect(terminal.kill(a.id).success).toBe(true);
    expect(pty.spawned[0].term.killed).toBe(true);
    terminal.killAll();
    expect(pty.spawned[1].term.killed).toBe(true);
    expect(terminal.sessions.size).toBe(0);
  });

  it('says why when node-pty cannot be loaded', () => {
    const terminal = createTerminalHost({
      loadPty: () => {
        throw new Error('module did not self-register');
      },
      env: () => ({}),
      cwd: '/',
      activator: '',
      send: () => undefined,
    });
    expect(terminal.spawn()).toEqual({ success: false, error: 'node-pty indisponível: module did not self-register' });
  });
});
