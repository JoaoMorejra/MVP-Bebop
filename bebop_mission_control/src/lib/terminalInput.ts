/**
 * The emergency `land` shortcut of the diagnostics terminal, at the input layer.
 *
 * Every keystroke goes to the real shell (a PTY) as it is typed, so the shell
 * owns editing, history and Tab completion. `land` + Enter is the one line that
 * must not depend on that shell: it has to work instantly and while a command
 * such as `ros2 topic echo` holds the foreground, where no shell is reading the
 * line at all. So the typed line is tracked here, and when Enter would submit
 * exactly `land`, the Enter is withheld, the line is killed in the shell
 * instead, and the caller lands the aircraft.
 *
 * The tracking follows what can be known from the keystrokes alone: printable
 * characters, backspace, Ctrl+U and Ctrl+C. After Tab completion or history
 * navigation the shell's line is no longer the typed one, so the tracker stops
 * guessing until the next Enter or Ctrl+C: a line it cannot see is never
 * mistaken for `land`.
 */

/** Ctrl+U: kills the line in readline, so a withheld `land` leaves no residue. */
export const KILL_LINE = '\x15';

const ENTER = /[\r\n]/;
const BACKSPACE = new Set(['\x7f', '\b']);
const CTRL_C = '\x03';

export interface InterceptResult {
  /** Bytes to write to the PTY, in order. */
  forward: string;
  /** Whether this chunk submitted `land`. */
  land: boolean;
}

export interface LandInterceptor {
  feed(data: string): InterceptResult;
}

export function createLandInterceptor(command = 'land'): LandInterceptor {
  let line = '';
  let blind = false;

  const resetLine = () => {
    line = '';
    blind = false;
  };

  return {
    feed(data: string): InterceptResult {
      let forward = '';
      let land = false;
      for (let i = 0; i < data.length; i++) {
        const ch = data[i];
        if (ENTER.test(ch)) {
          if (!blind && line.trim() === command) {
            land = true;
            forward += KILL_LINE;
          } else {
            forward += ch;
          }
          resetLine();
          continue;
        }
        forward += ch;
        if (ch === CTRL_C || ch === KILL_LINE) {
          resetLine();
        } else if (BACKSPACE.has(ch)) {
          line = line.slice(0, -1);
        } else if (ch === '\x1b') {
          // An escape sequence: an arrow, history, a function key. Pass the
          // whole sequence through and stop trusting the tracked line.
          blind = true;
          const rest = /^\x1b(\[[0-9;?]*[ -/]*[@-~]|O.|.)?/.exec(data.slice(i));
          const length = rest ? rest[0].length : 1;
          forward += data.slice(i + 1, i + length);
          i += length - 1;
        } else if (ch < ' ') {
          blind = true;
        } else {
          line += ch;
        }
      }
      return { forward, land };
    },
  };
}
