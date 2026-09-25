/**
 * The diagnostics terminal's title: `user@host: ~/ros2_ws — bash`, as a
 * desktop terminal words it, kept current by the OSC 7 report the shell's
 * rcfile emits after every command (`electron/terminal-rc.bash`).
 */

import type { TerminalIdentity } from '../types/bmg';

/** `ESC ] 7 ; file://host/path` terminated by BEL or ST, as bash emits it. */
const OSC7_URI = /^file:\/\/[^/]*(\/.*)$/;

/**
 * The directory an OSC 7 payload reports, or null when it is not a file URI.
 *
 * The payload is the part after `7;`. The path is percent-decoded; a payload
 * whose escapes do not decode is taken as written rather than dropped.
 */
export function parseOsc7(payload: string): string | null {
  const match = OSC7_URI.exec(payload.trim());
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

/** `path` with the home directory folded to `~`, as the prompt shows it. */
export function tildePath(path: string, home: string): string {
  if (!home || home === '/') return path;
  const base = home.replace(/\/+$/, '');
  if (path === base) return '~';
  return path.startsWith(`${base}/`) ? `~${path.slice(base.length)}` : path;
}

/** The full title bar line of one session. */
export function terminalTitle(identity: TerminalIdentity): string {
  return `${identity.user}@${identity.host}: ${tildePath(identity.cwd, identity.home)} — ${identity.shell}`;
}

/** The short label of one tab: the last component of its directory. */
export function tabLabel(identity: TerminalIdentity): string {
  const path = tildePath(identity.cwd, identity.home);
  if (path === '~' || path === '/') return path;
  const parts = path.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? path;
}
