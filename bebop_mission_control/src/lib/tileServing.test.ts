import { chmodSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { resolveTilePath, serveTile } from '../../electron/tileRoute.cjs';

const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46]);
const MANIFEST = JSON.stringify({ minZoom: 17, maxZoom: 19 });

let dist: string;
let server: http.Server;
let base: string;

beforeAll(async () => {
  dist = mkdtempSync(join(tmpdir(), 'bmg-tiles-'));
  mkdirSync(join(dist, 'tiles', '18', '95432'), { recursive: true });
  writeFileSync(join(dist, 'tiles', '18', '95432', '145123.jpg'), JPEG);
  writeFileSync(join(dist, 'tiles', 'manifest.json'), MANIFEST);
  writeFileSync(join(dist, 'secret.txt'), 'secret');
  writeFileSync(join(dist, 'tiles', '18', '95432', 'note.png'), 'x');
  mkdirSync(join(dist, 'tiles', '19', '1'), { recursive: true });
  symlinkSync(join(dist, 'secret.txt'), join(dist, 'tiles', '19', '1', '2.jpg'));
  mkdirSync(join(dist, 'outside', '1'), { recursive: true });
  writeFileSync(join(dist, 'outside', '1', '5.jpg'), JPEG);
  symlinkSync(join(dist, 'outside'), join(dist, 'tiles', '20'));
  mkdirSync(join(dist, 'tiles', '21', '1'), { recursive: true });
  writeFileSync(join(dist, 'tiles', '21', '1', '3.jpg'), JPEG);
  chmodSync(join(dist, 'tiles', '21', '1', '3.jpg'), 0o000);

  server = http.createServer((req, res) => {
    const urlPath = (req.url ?? '').split('?')[0];
    if (urlPath.startsWith('/tiles/')) {
      serveTile(dist, urlPath, res);
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<html>spa</html>');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
  rmSync(dist, { recursive: true, force: true });
});

function rawGet(path: string): Promise<{ status: number; type: string | undefined; body: Buffer }> {
  return new Promise((resolve, reject) => {
    const { port } = new URL(base);
    http
      .get({ host: '127.0.0.1', port, path }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () =>
          resolve({
            status: res.statusCode ?? 0,
            type: res.headers['content-type'],
            body: Buffer.concat(chunks),
          }),
        );
      })
      .on('error', reject);
  });
}

describe('resolveTilePath', () => {
  it('accepts a well-formed tile path under distDir/tiles', () => {
    expect(resolveTilePath(dist, '/tiles/18/95432/145123.jpg')).toBe(
      join(dist, 'tiles', '18', '95432', '145123.jpg'),
    );
  });

  it('accepts the manifest', () => {
    expect(resolveTilePath(dist, '/tiles/manifest.json')).toBe(join(dist, 'tiles', 'manifest.json'));
  });

  it.each([
    '/tiles/../main.cjs',
    '/tiles/18/1/2.png',
    '/tiles/a/b/c.jpg',
    '/tiles/18/1/2.jpg/extra',
    '/tiles/18/1.jpg',
    '/tiles/-1/1/2.jpg',
    '/tiles/18/1.5/2.jpg',
    '/tiles/18/1/2.JPG.png',
    '/tiles/18//2.jpg',
    '/tiles/18/1/.jpg',
    '/tiles/18/../../secret.txt',
    '/tiles/%2e%2e/secret.txt',
    '/tiles/%2E%2E/%2e%2e/secret.txt',
    '/tiles/18%2f1%2f2.jpg',
    '/tiles/18/1/2%2ejpg',
    '/tiles/18\\1\\2.jpg',
    '/tiles/18/1\\..\\2.jpg',
    '/tiles/manifest.json/extra',
    '/tiles/',
    '/tiles',
    '/other/18/1/2.jpg',
    '/tiles/18/1/2.jpg\0.png',
  ])('rejects %s', (p) => {
    expect(resolveTilePath(dist, p)).toBeNull();
  });
});

describe('tile route over HTTP', () => {
  it('serves an existing tile as image/jpeg', async () => {
    const r = await rawGet('/tiles/18/95432/145123.jpg');
    expect(r.status).toBe(200);
    expect(r.type).toBe('image/jpeg');
    expect(r.body.equals(JPEG)).toBe(true);
  });

  it('serves the manifest as application/json', async () => {
    const r = await rawGet('/tiles/manifest.json');
    expect(r.status).toBe(200);
    expect(r.type).toBe('application/json');
    expect(r.body.toString()).toBe(MANIFEST);
  });

  it('answers 404 with an empty body for a missing tile', async () => {
    const r = await rawGet('/tiles/18/1/2.jpg');
    expect(r.status).toBe(404);
    expect(r.body.length).toBe(0);
  });

  it('answers 404 with an empty body for malformed and traversal paths', async () => {
    for (const p of ['/tiles/18/1/2.png', '/tiles/%2e%2e/secret.txt', '/tiles/18/95432/note.png', '/tiles/zzz']) {
      const r = await rawGet(p);
      expect(r.status).toBe(404);
      expect(r.body.length).toBe(0);
    }
  });
});

describe('tile route hardening', () => {
  it('answers 404 for a file symlink that escapes the tiles root', async () => {
    const r = await rawGet('/tiles/19/1/2.jpg');
    expect(r.status).toBe(404);
    expect(r.body.length).toBe(0);
  });

  it('answers 404 for a directory symlink that escapes the tiles root', async () => {
    const r = await rawGet('/tiles/20/1/5.jpg');
    expect(r.status).toBe(404);
    expect(r.body.length).toBe(0);
  });

  it.skipIf(process.getuid?.() === 0)('survives a read failure after stat without a 200 body', async () => {
    const failed = await rawGet('/tiles/21/1/3.jpg').catch(() => null);
    if (failed) expect(failed.body.length).not.toBe(JPEG.length);
    const next = await rawGet('/tiles/18/95432/145123.jpg');
    expect(next.status).toBe(200);
  });
});
