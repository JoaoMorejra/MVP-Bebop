import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const BMG_ROOT = resolve(__dirname, '..', '..');
const SCAN_ROOTS = [
  join(BMG_ROOT, 'src'),
  join(BMG_ROOT, 'electron'),
  join(BMG_ROOT, 'streamer'),
  join(BMG_ROOT, 'config'),
  join(BMG_ROOT, 'scripts'),
  resolve(BMG_ROOT, '..', 'scripts'),
  resolve(BMG_ROOT, '..', 'mvp_mission_bebop'),
];
const EXTRA_FILES = [join(BMG_ROOT, 'docs', 'TABELA_DE_FALAS_COPILOTO.md')];
const EXTENSIONS = new Set(['.ts', '.tsx', '.cjs', '.py', '.json']);
const EXCLUDED_DIRS = new Set(['node_modules', 'dist', '__pycache__']);
const SELF = __filename;
const BANNED = new RegExp(['sini', 'stro'].join(''), 'i');

function collect(dir: string, out: string[]): void {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (EXCLUDED_DIRS.has(name)) continue;
    const full = join(dir, name);
    const info = statSync(full);
    if (info.isDirectory()) {
      collect(full, out);
    } else if (full !== SELF && EXTENSIONS.has(extname(name))) {
      out.push(full);
    }
  }
}

describe('banned terms', () => {
  it('no source file spells the banned word', () => {
    const files: string[] = [];
    SCAN_ROOTS.forEach((root) => collect(root, files));
    EXTRA_FILES.filter((file) => existsSync(file)).forEach((file) => files.push(file));
    const offenders = files
      .filter((file) => BANNED.test(readFileSync(file, 'utf8')))
      .map((file) => relative(BMG_ROOT, file));
    expect(offenders).toEqual([]);
  });
});
