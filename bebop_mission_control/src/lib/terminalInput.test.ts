import { describe, expect, it } from 'vitest';
import { KILL_LINE, createLandInterceptor } from './terminalInput';

/** Feed keystrokes one xterm onData chunk at a time, as the terminal delivers them. */
function type(chunks: string[]) {
  const interceptor = createLandInterceptor();
  let forwarded = '';
  let lands = 0;
  for (const chunk of chunks) {
    const result = interceptor.feed(chunk);
    forwarded += result.forward;
    if (result.land) lands += 1;
  }
  return { forwarded, lands };
}

describe('land interceptor', () => {
  it('lands on "land" + Enter, and the Enter never reaches the shell', () => {
    const { forwarded, lands } = type(['l', 'a', 'n', 'd', '\r']);
    expect(lands).toBe(1);
    // The letters were echoed as typed; the line is killed instead of submitted.
    expect(forwarded).toBe(`land${KILL_LINE}`);
    expect(forwarded).not.toContain('\r');
  });

  it('lands on a pasted line and on surrounding spaces', () => {
    expect(type(['land\r']).lands).toBe(1);
    expect(type(['  land  ', '\r']).lands).toBe(1);
  });

  it('follows backspace and line-kill edits', () => {
    expect(type(['l', 'a', 'n', 'x', '\x7f', 'd', '\r']).lands).toBe(1);
    expect(type(['e', 'c', 'h', 'o', '\x15', 'land', '\r']).lands).toBe(1);
  });

  it('passes every other line straight through', () => {
    for (const line of ['island', 'lands', 'land now', 'ros2 topic list', 'echo land', '']) {
      const { forwarded, lands } = type([line, '\r']);
      expect(lands, line).toBe(0);
      expect(forwarded).toBe(`${line}\r`);
    }
  });

  it('forwards raw bytes untouched: Tab, arrows, Ctrl+C', () => {
    const { forwarded, lands } = type(['ros2 to', '\t', '\x1b[A', '\x1b[B', '\x03']);
    expect(lands).toBe(0);
    expect(forwarded).toBe('ros2 to\t\x1b[A\x1b[B\x03');
  });

  it('does not guess a line it can no longer see', () => {
    // After Tab completion or history the shell's line is not the typed one.
    expect(type(['la', '\t', 'nd', '\r']).lands).toBe(0);
    expect(type(['\x1b[A', 'land', '\r']).lands).toBe(0);
  });

  it('starts clean after Enter, Ctrl+C and a landing', () => {
    expect(type(['ls', '\r', 'land', '\r']).lands).toBe(1);
    expect(type(['la', '\t', '\x03', 'land', '\r']).lands).toBe(1);
    expect(type(['land\r', 'land\r']).lands).toBe(2);
  });

  it('handles several lines in one chunk', () => {
    const { forwarded, lands } = type(['ls\rland\rpwd\r']);
    expect(lands).toBe(1);
    expect(forwarded).toBe(`ls\rland${KILL_LINE}pwd\r`);
  });
});
