const fs = require('fs');
const path = require('path');

const TILE_PATH = /^\/tiles\/(\d+)\/(\d+)\/(\d+)\.jpg$/;
const MANIFEST_PATH = '/tiles/manifest.json';

/**
 * Maps a request path onto an offline map tile or the tile manifest.
 *
 * Only `/tiles/<z>/<x>/<y>.jpg` (non-negative decimal integers) and
 * `/tiles/manifest.json` are accepted. The path is matched exactly as received
 * on the wire, before any percent-decoding: a `%` or a backslash anywhere in
 * it is refused, so encoded traversal (`%2e%2e`, `%2f`) never reaches the
 * filesystem.
 *
 * Parameters
 * ----------
 * distDir : string
 *     Absolute directory holding the built renderer; tiles live in `distDir/tiles`.
 * urlPath : string
 *     Raw request path without the query string.
 *
 * Returns
 * -------
 * string | null
 *     Absolute file path confined to `distDir/tiles`, or `null` when the
 *     request is not an exact tile or manifest route.
 */
function resolveTilePath(distDir, urlPath) {
  if (typeof distDir !== 'string' || typeof urlPath !== 'string') return null;
  if (urlPath.includes('%') || urlPath.includes('\\') || urlPath.includes('\0')) return null;

  let relative;
  if (urlPath === MANIFEST_PATH) {
    relative = 'manifest.json';
  } else {
    const match = TILE_PATH.exec(urlPath);
    if (!match) return null;
    relative = path.join(match[1], match[2], match[3] + '.jpg');
  }

  const root = path.resolve(distDir, 'tiles');
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) return null;
  return file;
}

/**
 * Answers a `/tiles/` request without ever falling through to the SPA shell.
 *
 * A missing or invalid tile is a plain 404 with no body: Leaflet treats any
 * 200 as image data, so the `index.html` fallback would be rendered as a
 * broken tile and cached.
 *
 * Parameters
 * ----------
 * distDir : string
 *     Absolute directory holding the built renderer.
 * urlPath : string
 *     Raw request path without the query string.
 * res : http.ServerResponse
 *     Response to complete.
 */
function serveTile(distDir, urlPath, res) {
  const file = resolveTilePath(distDir, urlPath);
  let stat = null;
  if (file) {
    try {
      const root = fs.realpathSync(path.resolve(distDir, 'tiles'));
      if (fs.realpathSync(file).startsWith(root + path.sep)) stat = fs.statSync(file);
    } catch {
      stat = null;
    }
  }
  if (!file || !stat || !stat.isFile()) {
    res.writeHead(404, { 'Content-Length': 0 });
    res.end();
    return;
  }
  const isManifest = file.endsWith('.json');
  res.writeHead(200, {
    'Content-Type': isManifest ? 'application/json' : 'image/jpeg',
    'Content-Length': stat.size,
    'Cache-Control': isManifest ? 'no-cache' : 'public, max-age=86400',
  });
  // Headers are already on the wire when a read fails (permissions, file
  // removed after stat); the only honest signal left is to drop the socket.
  const stream = fs.createReadStream(file);
  stream.once('error', () => res.destroy());
  stream.pipe(res);
}

module.exports = { resolveTilePath, serveTile };
