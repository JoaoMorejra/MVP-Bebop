import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const BMG_ROOT = resolve(__dirname, '..', '..');
const SCAN_ROOTS = ['src', 'electron', 'streamer'].map((name) => join(BMG_ROOT, name));
const EXTENSIONS = new Set(['.ts', '.tsx', '.cjs', '.py', '.json']);
const EXCLUDED_DIRS = new Set(['node_modules', 'dist', '__pycache__']);
const SELF = __filename;

const REMOVED = [
  ['bmg', 'report-ready'].join(':'),
  ['bmg', 'get-report-url'].join(':'),
  ['relatorio', ''].join('_'),
  ['get', 'ReportUrl'].join(''),
  ['on', 'ReportReady'].join(''),
];

function collect(dir: string, out: string[]): void {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (EXCLUDED_DIRS.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      collect(full, out);
    } else if (full !== SELF && EXTENSIONS.has(extname(name))) {
      out.push(full);
    }
  }
}

describe('external HTML report removal', () => {
  it('electron, src and streamer carry no report IPC channel and no report filename', () => {
    const files: string[] = [];
    SCAN_ROOTS.forEach((root) => collect(root, files));
    const offenders = files.flatMap((file) => {
      const text = readFileSync(file, 'utf8');
      return REMOVED.filter((term) => text.includes(term)).map((term) => `${relative(BMG_ROOT, file)}: ${term}`);
    });
    expect(offenders).toEqual([]);
  });
});
