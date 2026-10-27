import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { TILE_SOURCES, tileGrid } from '../components/cockpit/TacticalMap';
import { MAX_DISPLAY_ZOOM, MIN_DISPLAY_ZOOM, ZOOM_STEP, fixedView } from './mapView';
import { MAP_ANCHOR } from './siteAnchor';

const ROOT = resolve(__dirname, '../../public');
const MANIFEST = join(ROOT, 'tiles', 'manifest.json');

/** Narrowest cockpit panel the coverage is checked from. */
const MIN_PANEL_PX = 640;

describe.skipIf(!existsSync(MANIFEST))('offline tile coverage (skipped: public/tiles/manifest.json absent, clone without tiles)', () => {
  it('holds every tile the map loop requests, for panels up to the manifest viewport', () => {
    const manifest = JSON.parse(readFileSync(MANIFEST, 'utf-8')) as { viewportPx: number };
    expect(manifest.viewportPx).toBeGreaterThanOrEqual(MIN_PANEL_PX);
    const anchor = { lat: MAP_ANCHOR.latitude, lng: MAP_ANCHOR.longitude };
    const source = TILE_SOURCES[0];
    const requested = new Set<string>();
    for (let width = MIN_PANEL_PX; width <= manifest.viewportPx; width++) {
      const size = { width, height: width };
      for (let zoom = MIN_DISPLAY_ZOOM; zoom <= MAX_DISPLAY_ZOOM; zoom += ZOOM_STEP) {
        for (const tile of tileGrid(fixedView(size, anchor, zoom, source.maxZoom), size, source)) {
          requested.add(tile.url);
        }
      }
    }
    expect(requested.size).toBeGreaterThan(0);
    expect([...requested].filter((url) => !existsSync(join(ROOT, url))).sort()).toEqual([]);
  });
});
