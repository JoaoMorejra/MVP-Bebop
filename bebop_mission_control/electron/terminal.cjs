/**
 * PTY sessions for the diagnostics terminal.
 *
 * One interactive bash per session on a real pseudo-terminal, so the shell
 * owns line editing, history, job control and Tab completion exactly as in any
 * terminal emulator. Bytes flow raw both ways: the renderer's keystrokes are
 * written as they are (Tab, arrows, Ctrl+C included) and the PTY's output goes
 * back untouched for xterm.js to render. The emergency `land` shortcut is not
 * here: it is caught at the renderer's input layer before any byte reaches the
 * PTY (`src/lib/terminalInput.ts`).
 *
 * `node-pty` is loaded on the first spawn rather than at startup. It is a
 * native module rebuilt for Electron (`npm run rebuild:native`); a station
 * whose build is missing it keeps everything except the terminal, which then
 * reports why it cannot open.
 */

const RC_FILE = require('path').join(__dirname, 'terminal-rc.bash');

/** Bounds on a terminal size the renderer may ask for. */
const MIN_COLS = 20;
const MAX_COLS = 500;
const MIN_ROWS = 5;
const MAX_ROWS = 300;

const clamp = (value, lo, hi, fallback) => {
  const n = Math.floor(Number(value));
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
};

/**
 * @param {object} options
 * @param {() => {spawn: Function}} options.loadPty  Returns the node-pty module.
 * @param {() => Record<string, string>} options.env  The mission environment.
 * @param {string} options.cwd  Where each shell starts.
 * @param {string} options.activator  nectar-activate, sourced by the rcfile.
 * @param {(channel: string, payload: object) => void} options.send  To the renderer.
 */
function createTerminalHost({ loadPty, env, cwd, activator, send, shell = '/bin/bash', rcFile = RC_FILE }) {
  const sessions = new Map();
  let seq = 0;

  function spawn(options = {}) {
    let pty;
    try {
      pty = loadPty();
    } catch (error) {
      return { success: false, error: `node-pty indisponível: ${error.message}` };
    }

    const cols = clamp(options.cols, MIN_COLS, MAX_COLS, 80);
    const rows = clamp(options.rows, MIN_ROWS, MAX_ROWS, 24);
    let term;
    try {
      term = pty.spawn(shell, ['--rcfile', rcFile, '-i'], {
        name: 'xterm-256color',
        cols,
        rows,
        cwd,
        env: {
          ...env(),
          TERM: 'xterm-256color',
          COLORTERM: 'truecolor',
          BMG_NECTAR_ACTIVATE: activator,
        },
      });
    } catch (error) {
      return { success: false, error: error.message };
    }

    seq += 1;
    const id = `pty-${seq}`;
    sessions.set(id, term);
    term.onData((data) => send('bmg:terminal-data', { id, data }));
    term.onExit(({ exitCode, signal }) => {
      sessions.delete(id);
      send('bmg:terminal-exit', { id, exitCode, signal: signal || null });
    });
    return { success: true, id, pid: term.pid, cols, rows };
  }

  function write(id, data) {
    const term = sessions.get(String(id));
    if (!term || typeof data !== 'string') return { success: false };
    term.write(data);
    return { success: true };
  }

  function resize(id, cols, rows) {
    const term = sessions.get(String(id));
    if (!term) return { success: false };
    try {
      term.resize(clamp(cols, MIN_COLS, MAX_COLS, term.cols), clamp(rows, MIN_ROWS, MAX_ROWS, term.rows));
    } catch (error) {
      return { success: false, error: error.message };
    }
    return { success: true };
  }

  function kill(id) {
    const term = sessions.get(String(id));
    if (!term) return { success: false };
    sessions.delete(String(id));
    try {
      term.kill();
    } catch (_error) {
      // Already gone.
    }
    return { success: true };
  }

  function killAll() {
    for (const id of Array.from(sessions.keys())) kill(id);
  }

  return { spawn, write, resize, kill, killAll, sessions };
}

module.exports = { createTerminalHost, RC_FILE };
