// @vitest-environment jsdom
import React, { act } from 'react';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TrackPoint } from '../../types/mission';
import { MAX_DISPLAY_ZOOM, MIN_DISPLAY_ZOOM, ZOOM_STEP } from '../../lib/mapView';

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const operatorLocation = vi.fn();
vi.mock('../../hooks/useOperatorLocation', () => ({ useOperatorLocation: operatorLocation }));

const { TacticalMap, TILE_ATTRIBUTION, TILE_SOURCES } = await import('./TacticalMap');

const MANIFEST = resolve(__dirname, '../../../public/tiles/manifest.json');
const HAS_MANIFEST = existsSync(MANIFEST);

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  act(() => root.unmount());
  container.remove();
});

const point = (x: number, y: number): TrackPoint => ({ x, y, alt: 1, at: 0 });

function render(track: TrackPoint[]) {
  act(() =>
    root.render(<TacticalMap track={track} heading={0} arrivalRadius={0.2} stale={false} flyingState={null} />)
  );
}

const baseMarker = () => container.querySelector('[data-base-marker]')!;
const tilePositions = () =>
  Array.from(container.querySelectorAll('img')).map((img) => `${img.style.left}|${img.style.top}|${img.src}`);
const scaleLabel = () => container.querySelector('svg text[data-scale-label]')?.textContent ?? undefined;
const zoomInDisabled = () => (container.querySelector('[aria-label="Aproximar o mapa"]') as HTMLButtonElement).disabled;

describe('TacticalMap anchored at the site', () => {
  it('keeps the basemap, the scale and the base still while the aircraft flies away', () => {
    render([point(0, 0)]);
    const before = { tiles: tilePositions(), base: baseMarker().getAttribute('transform') };
    const scaleBefore = scaleLabel();
    expect(scaleBefore).toBeDefined();
    expect(before.tiles.length).toBeGreaterThan(0);

    render([point(0, 0), point(40, 25), point(180, -120)]);
    expect(tilePositions()).toEqual(before.tiles);
    expect(baseMarker().getAttribute('transform')).toBe(before.base);
    expect(scaleLabel()).toBe(scaleBefore);
    expect(zoomInDisabled()).toBe(false);
  });

  it('draws the aircraft at the base at rest and away from it in flight', () => {
    render([point(0, 0)]);
    const marker = () => container.querySelector('[data-aircraft]')!.getAttribute('transform');
    const atRest = marker();
    expect(atRest).toBe(baseMarker().getAttribute('transform'));
    render([point(0, 0), point(30, 0)]);
    expect(marker()).not.toBe(atRest);
  });

  it('prints no metric position over the aircraft', () => {
    render([point(0, 0), point(12.3, 4.5)]);
    expect(container.textContent).not.toMatch(/E -?\d+\.\d · N -?\d+\.\d m/);
  });

  it('reads the fixed base and never the operator position', () => {
    render([point(0, 0)]);
    expect(operatorLocation).not.toHaveBeenCalled();
    expect(container.textContent).toContain('BASE FIXA');
    expect(container.textContent).toContain('-23.648891, -46.718712');
    expect(container.textContent).toContain('Transamerica Expo Center');
    expect(container.querySelector('[data-coarse-base]')).toBeNull();
  });

  it('footer shows the fixed base, city and location', () => {
    render([point(0, 0)]);
    const text = (container.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toContain('BASE FIXA: -23.648891, -46.718712');
    expect(text).toContain('Cidade: São Paulo');
    expect(text).toContain('Localização: Transamerica Expo Center');
  });

  it('does not move the base coordinates with the aircraft', () => {
    const coordinates = () => (container.textContent ?? '').match(/-?\d+\.\d{6}, -?\d+\.\d{6}/)?.[0];
    render([point(0, 0)]);
    const before = coordinates();
    render([point(0, 0), point(120, -80)]);
    expect(before).toBe('-23.648891, -46.718712');
    expect(coordinates()).toBe(before);
  });

  it('requests tiles on first render', () => {
    render([point(0, 0)]);
    expect(container.querySelectorAll('img').length).toBeGreaterThan(0);
  });
});

const tileImages = () => Array.from(container.querySelectorAll('img'));
const mapStatus = () => container.querySelector('.sr-only')?.textContent ?? '';
const gridShown = () => /GRADE TÁTICA LOCAL/.test(container.textContent ?? '');
const button = (label: string) => container.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement;
const click = (label: string) => act(() => button(label).click());
const fire = (target: EventTarget, event: Event) => act(() => void target.dispatchEvent(event));
const tileZooms = () => tileImages().map((img) => Number(new URL(img.src).pathname.split('/')[2]));
const host = () => container.querySelector('.touch-none') as HTMLDivElement;

function pointer(type: string, pointerId: number, clientX: number): Event {
  const event = new MouseEvent(type, { bubbles: true, clientX, clientY: 100 });
  Object.defineProperty(event, 'pointerId', { value: pointerId });
  return event;
}

/** Network-shaped calls the map could make outside its own tile grid. */
function watchRequests() {
  const fetchSpy = vi.fn();
  let images = 0;
  const NativeImage = globalThis.Image;
  vi.stubGlobal('fetch', fetchSpy);
  vi.stubGlobal(
    'Image',
    class extends NativeImage {
      constructor(width?: number, height?: number) {
        super(width, height);
        images += 1;
      }
    }
  );
  return { fetchSpy, images: () => images };
}

function zoomTo(display: number) {
  while (!button('Afastar o mapa').disabled) click('Afastar o mapa');
  for (let zoom = MIN_DISPLAY_ZOOM; zoom < display; zoom += ZOOM_STEP) click('Aproximar o mapa');
}

const px = (value: string) => Number(value.replace('px', ''));

describe('TacticalMap local tile source', () => {
  it('draws only same-origin tile URLs', () => {
    const { fetchSpy, images } = watchRequests();
    render([point(0, 0)]);
    expect(tileImages().length).toBeGreaterThan(0);
    for (const img of tileImages()) {
      expect(img.src).toMatch(/^http:\/\/localhost(:\d+)?\/tiles\/\d+\/\d+\/\d+\.jpg$/);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(images()).toBe(0);
  });

  it('has exactly one source, the local satellite tree up to z20', () => {
    expect(TILE_SOURCES).toHaveLength(1);
    expect(TILE_SOURCES[0]).toMatchObject({ id: 'local-satellite', maxZoom: 20, attribution: TILE_ATTRIBUTION });
    expect(TILE_SOURCES[0].url('a', 18, 97012, 148812)).toBe('/tiles/18/97012/148812.jpg');
  });

  it('keeps the satellite layer and hides the grid once a tile loads', () => {
    render([point(0, 0)]);
    expect(mapStatus()).toContain('carregando mapa');
    fire(tileImages()[0], new Event('load'));
    expect(gridShown()).toBe(false);
    expect(tileImages().length).toBeGreaterThan(0);
    expect(mapStatus()).toContain('mapa local');
    expect(container.textContent).toContain(TILE_ATTRIBUTION);
  });

  it('falls back to the grid when every tile fails, and requests nothing after', () => {
    vi.useFakeTimers();
    const { fetchSpy, images } = watchRequests();
    render([point(0, 0)]);
    for (const img of tileImages()) fire(img, new Event('error'));
    expect(gridShown()).toBe(true);
    expect(tileImages()).toHaveLength(0);
    expect(mapStatus()).toContain('grade offline');

    act(() => void vi.advanceTimersByTime(120_000));
    click('Aproximar o mapa');
    expect(tileImages()).toHaveLength(0);
    expect(images()).toBe(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('drops to the grid when no tile answers within the load timeout', () => {
    vi.useFakeTimers();
    render([point(0, 0)]);
    act(() => void vi.advanceTimersByTime(1999));
    expect(tileImages().length).toBeGreaterThan(0);
    act(() => void vi.advanceTimersByTime(1));
    expect(tileImages()).toHaveLength(0);
    expect(gridShown()).toBe(true);
    expect(mapStatus()).toContain('grade offline');
  });

  it('ignores a failing tile after another loaded', () => {
    render([point(0, 0)]);
    const [first, ...rest] = tileImages();
    fire(first, new Event('load'));
    for (const img of rest) fire(img, new Event('error'));
    expect(tileImages().length).toBe(rest.length + 1);
    expect(gridShown()).toBe(false);
    expect(mapStatus()).toContain('mapa local');
  });

  it('keeps the imagery when every tile but one fails and that one loads', () => {
    render([point(0, 0)]);
    const images = tileImages();
    const last = images[images.length - 1];
    for (const img of images.slice(0, -1)) fire(img, new Event('error'));
    expect(tileImages()).toHaveLength(images.length);
    expect(mapStatus()).toContain('carregando mapa');
    fire(last, new Event('load'));
    expect(gridShown()).toBe(false);
    expect(tileImages()).toHaveLength(images.length);
    expect(mapStatus()).toContain('mapa local');
  });
});

describe('TacticalMap zoom', () => {
  it('zoom in and out change the scale and respect the limits', () => {
    render([point(0, 0)]);
    const initial = scaleLabel();
    click('Aproximar o mapa');
    expect(scaleLabel()).not.toBe(initial);
    click('Afastar o mapa');
    expect(scaleLabel()).toBe(initial);

    while (!button('Afastar o mapa').disabled) click('Afastar o mapa');
    expect(Math.min(...tileZooms())).toBe(MIN_DISPLAY_ZOOM);
    while (!button('Aproximar o mapa').disabled) click('Aproximar o mapa');
    expect(new Set(tileZooms())).toEqual(new Set([20]));

    click('Voltar ao enquadramento padrão');
    expect(scaleLabel()).toBe(initial);
  });

  it('scale legend updates when the zoom buttons are pressed', () => {
    render([point(0, 0)]);
    while (!button('Afastar o mapa').disabled) click('Afastar o mapa');
    const labels = [scaleLabel()];
    while (!button('Aproximar o mapa').disabled) {
      click('Aproximar o mapa');
      labels.push(scaleLabel());
    }
    expect(labels.length).toBe((MAX_DISPLAY_ZOOM - MIN_DISPLAY_ZOOM) / ZOOM_STEP + 1);
    expect(labels.every((label) => typeof label === 'string' && !label.includes('NaN'))).toBe(true);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('scale legend never repeats between two adjacent wheel steps', () => {
    render([point(0, 0)]);
    let previous = scaleLabel();
    for (let i = 0; i < 12; i += 1) {
      fire(host(), new WheelEvent('wheel', { deltaY: -50, bubbles: true, cancelable: true }));
      const next = scaleLabel();
      if (button('Aproximar o mapa').disabled && next === previous) break;
      expect(next).not.toBe(previous);
      previous = next;
    }
  });

  it('does not cap overzoomed tiles at the panel width', () => {
    render([point(0, 0)]);
    while (!button('Aproximar o mapa').disabled) click('Aproximar o mapa');
    const widest = Math.max(...tileImages().map((img) => px(img.style.width)));
    expect(widest).toBeGreaterThan(640);
    for (const img of tileImages()) {
      expect(img.className).toMatch(/\bmax-w-none\b/);
      expect(img.style.width).toBe(img.style.height);
    }
  });

  it('wheel and pinch zoom', () => {
    render([point(0, 0)]);
    const initial = scaleLabel();
    fire(host(), new WheelEvent('wheel', { deltaY: -100, bubbles: true, cancelable: true }));
    const wheeled = scaleLabel();
    expect(wheeled).not.toBe(initial);
    fire(host(), new WheelEvent('wheel', { deltaY: 100, bubbles: true, cancelable: true }));
    expect(scaleLabel()).toBe(initial);

    fire(host(), pointer('pointerdown', 1, 100));
    fire(host(), pointer('pointerdown', 2, 200));
    fire(host(), pointer('pointermove', 2, 300));
    fire(host(), pointer('pointerup', 1, 100));
    fire(host(), pointer('pointerup', 2, 300));
    expect(scaleLabel()).not.toBe(initial);
  });

  it('overzooms z20 without seams: integer positions, each tile ending where the next begins', () => {
    for (const display of [21, 21.5, MAX_DISPLAY_ZOOM]) {
      render([point(0, 0)]);
      zoomTo(display);
      expect(new Set(tileZooms())).toEqual(new Set([20]));
      const boxes = tileImages().map((img) => ({
        left: px(img.style.left),
        top: px(img.style.top),
        width: px(img.style.width),
        height: px(img.style.height),
      }));
      for (const box of boxes) {
        for (const value of Object.values(box)) expect(Number.isInteger(value)).toBe(true);
      }
      const rows = new Map<number, typeof boxes>();
      const cols = new Map<number, typeof boxes>();
      for (const box of boxes) {
        rows.set(box.top, [...(rows.get(box.top) ?? []), box]);
        cols.set(box.left, [...(cols.get(box.left) ?? []), box]);
      }
      expect(rows.size).toBeGreaterThan(1);
      expect(cols.size).toBeGreaterThan(1);
      for (const row of rows.values()) {
        row.sort((a, b) => a.left - b.left);
        for (let i = 0; i + 1 < row.length; i++) expect(row[i + 1].left - row[i].left).toBe(row[i].width);
      }
      for (const col of cols.values()) {
        col.sort((a, b) => a.top - b.top);
        for (let i = 0; i + 1 < col.length; i++) expect(col[i + 1].top - col[i].top).toBe(col[i].height);
      }
      click('Voltar ao enquadramento padrão');
    }
  });
});

describe.skipIf(!HAS_MANIFEST)('TacticalMap against public/tiles/manifest.json (skipped: no tiles in this clone)', () => {
  const manifest = () => JSON.parse(readFileSync(MANIFEST, 'utf-8')) as { attribution: string; maxZoom: number };

  it('credits the imagery with the manifest attribution', () => {
    expect(TILE_ATTRIBUTION).toBe(manifest().attribution);
  });

  it('serves up to the manifest maxZoom', () => {
    expect(TILE_SOURCES[0].maxZoom).toBe(manifest().maxZoom);
  });
});

describe('TacticalMap frame and aircraft', () => {
  it('has no border and no title, and lays the legend over the map', () => {
    render([point(0, 0)]);
    const panel = container.firstElementChild as HTMLElement;
    expect(panel.className).not.toMatch(/\bborder\b/);
    expect(container.textContent).not.toContain('MAPA TÁTICO');
    const legend = container.querySelector('[data-map-legend]')!;
    expect(legend.className).toContain('absolute');
    expect(legend.parentElement!.querySelector('svg')).not.toBeNull();
  });

  it('turns the aircraft to the compass heading', () => {
    act(() => root.render(<TacticalMap track={[point(0, 0)]} heading={90} arrivalRadius={0.2} stale={false} flyingState={null} />));
    expect(container.querySelector('[data-drone-marker]')!.getAttribute('transform')).toBe('rotate(90)');
    act(() => root.render(<TacticalMap track={[point(0, 0)]} heading={-30} arrivalRadius={0.2} stale={false} flyingState={null} />));
    expect(container.querySelector('[data-drone-marker]')!.getAttribute('transform')).toBe('rotate(330)');
  });

  it('spins the rotors only while the aircraft reports flying', () => {
    act(() => root.render(<TacticalMap track={[point(0, 0)]} heading={0} arrivalRadius={0.2} stale={false} flyingState={0} />));
    expect(container.querySelector('[data-drone-marker]')!.hasAttribute('data-spinning')).toBe(false);
    act(() => root.render(<TacticalMap track={[point(0, 0)]} heading={0} arrivalRadius={0.2} stale={false} flyingState={2} />));
    expect(container.querySelector('[data-drone-marker]')!.hasAttribute('data-spinning')).toBe(true);
  });

  it('traces a subtle minimalist line from the home marker along the flown path', () => {
    render([point(0, 0)]);
    expect(container.querySelector('[data-flight-trail]')).toBeNull();

    render([point(0, 0), point(30, 0), point(30, 20)]);
    const trails = container.querySelectorAll('[data-flight-trail]');
    expect(trails).toHaveLength(1);
    const trail = trails[0];
    const d = trail.getAttribute('d') ?? '';
    expect(d).toMatch(/^M 320\.0 160\.0 L \d+\.\d \d+\.\d L \d+\.\d \d+\.\d$/);
    expect(Number(trail.getAttribute('stroke-width'))).toBeLessThanOrEqual(2);
    expect(Number(trail.getAttribute('stroke-opacity'))).toBeLessThanOrEqual(0.5);

    render([point(30, 0)]);
    expect(container.querySelector('[data-flight-trail]')?.getAttribute('d')).toMatch(/^M 320\.0 160\.0 L \d+\.\d \d+\.\d$/);
  });
});

describe('TacticalMap base and zoom', () => {
  it('marks the base without a text label', () => {
    render([point(0, 0)]);
    expect(container.querySelector('[data-base-marker]')).not.toBeNull();
    expect(container.textContent).not.toContain('BASE · DECOLAGEM');
  });

  it('draws no glow under the aircraft', () => {
    render([point(0, 0)]);
    expect(container.querySelector('[data-aircraft] circle')).toBeNull();
    expect(container.querySelector('radialGradient')).toBeNull();
  });

  it('centres the zoom control on the map\'s height, at the right edge', () => {
    render([point(0, 0)]);
    const zoom = container.querySelector('[data-map-zoom]') as HTMLElement;
    expect(zoom.className).toContain('top-1/2');
    expect(zoom.className).toContain('-translate-y-1/2');
    expect(zoom.className).toContain('right-2');
  });
});
