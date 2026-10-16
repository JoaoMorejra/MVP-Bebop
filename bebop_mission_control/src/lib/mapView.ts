/**
 * Scale decisions for the tactical map.
 *
 * The map is anchored at the fixed site base and drawn at a fixed default
 * scale ({@link fixedView}); the tiles are overzoomed past what their source
 * serves when the operator zooms in.
 */

/** Web Mercator ground resolution at zoom 0 on the equator, metres per pixel. */
const EQUATOR_MPP_Z0 = 156543.03392;

/** Wide enough for a long survey leg. */
export const MIN_DISPLAY_ZOOM = 15;
/** Close enough that a one-metre move is several marker widths. */
export const MAX_DISPLAY_ZOOM = 22;

/** Base accuracy beyond which the flight cannot be read against the streets. */
export const COARSE_ACCURACY_M = 100;

export function pixelsPerMetre(zoom: number, latitude: number): number {
  return 2 ** zoom / (EQUATOR_MPP_Z0 * Math.cos((latitude * Math.PI) / 180));
}

/**
 * The display zoom that fits `extentM` metres into the viewport's short side.
 *
 * Quantized to half steps: the extent grows continuously during a flight, and
 * every change of zoom refetches the visible tiles.
 */
export function displayZoom(
  extentM: number,
  viewport: { width: number; height: number },
  latitude: number
): number {
  const side = Math.min(viewport.width, viewport.height);
  const extent = Number.isFinite(extentM) && extentM > 0 ? extentM : 1;
  if (!(side > 0)) return MIN_DISPLAY_ZOOM;
  const wanted = Math.log2((side / extent) * EQUATOR_MPP_Z0 * Math.cos((latitude * Math.PI) / 180));
  if (!Number.isFinite(wanted)) return MIN_DISPLAY_ZOOM;
  const stepped = Math.floor(wanted * 2) / 2;
  return Math.min(MAX_DISPLAY_ZOOM, Math.max(MIN_DISPLAY_ZOOM, stepped));
}

/**
 * Extent the default view frames in the panel's short side, in metres: wide
 * enough to read the site on the imagery. The view no longer widens with the
 * trail; a closer or wider look is the operator's explicit zoom.
 */
export const OVERVIEW_EXTENT_M = 300;

/** Web Mercator, in fractional tiles. */
export function project(lat: number, lng: number, zoom: number): { x: number; y: number } {
  const n = 2 ** zoom;
  const x = ((lng + 180) / 360) * n;
  const latRad = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return { x, y };
}

export interface FixedView {
  /** Display zoom on screen. */
  zoom: number;
  /** Tile zoom requested and the scale it is drawn at. */
  tile: { z: number; scale: number };
  /** The anchor, in fractional tiles at `tile.z`: the tile grid is laid out around it. */
  centre: { x: number; y: number };
  tileSize: number;
  pxPerMetre: number;
  cx: number;
  cy: number;
  /** Screen x of a point `east` metres from the base. */
  sx: (east: number) => number;
  /** Screen y of a point `north` metres from the base. */
  sy: (north: number) => number;
}

/**
 * The tactical map's view: centred on the base, at a fixed scale.
 *
 * Nothing about the flight enters it. The basemap and the base stay where
 * they are and the aircraft moves across them; only the operator's own zoom
 * (`manualZoom`, null for the default framing) changes the scale.
 */
export function fixedView(
  size: { width: number; height: number },
  anchor: { lat: number; lng: number },
  manualZoom: number | null,
  maxTileZoom: number
): FixedView {
  const zoom = manualZoom ?? displayZoom(OVERVIEW_EXTENT_M, size, anchor.lat);
  const tile = tileZoom(zoom, maxTileZoom);
  const pxPerMetre = pixelsPerMetre(zoom, anchor.lat);
  const cx = size.width / 2;
  const cy = size.height / 2;
  return {
    zoom,
    tile,
    centre: project(anchor.lat, anchor.lng, tile.z),
    tileSize: 256 * tile.scale,
    pxPerMetre,
    cx,
    cy,
    sx: (east: number) => cx + east * pxPerMetre,
    sy: (north: number) => cy - north * pxPerMetre,
  };
}

/** One press of a zoom button, in display-zoom levels. */
export const ZOOM_STEP = 0.5;

/** A display zoom held inside the range the map is drawn at. */
export function clampDisplayZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return MIN_DISPLAY_ZOOM;
  return Math.min(MAX_DISPLAY_ZOOM, Math.max(MIN_DISPLAY_ZOOM, zoom));
}

/** The display zoom whose ground resolution is `pxPerMetre` at `latitude`. */
export function zoomForPixelsPerMetre(pxPerMetre: number, latitude: number): number {
  return Math.log2(pxPerMetre * EQUATOR_MPP_Z0 * Math.cos((latitude * Math.PI) / 180));
}

/**
 * Wheel input as a zoom change. One mouse notch (`deltaY` 100 pixels) is one
 * button step; a trackpad's small deltas zoom continuously. Line-mode deltas
 * (Firefox) are about 33 pixels a line.
 */
export function wheelZoom(zoom: number, deltaY: number, deltaMode = 0): number {
  const pixels = deltaMode === 1 ? deltaY * 33 : deltaMode === 2 ? deltaY * 800 : deltaY;
  return clampDisplayZoom(zoom - (pixels / 100) * ZOOM_STEP);
}

/** Pinch as a zoom change: doubling the fingers' spread is one zoom level in. */
export function pinchZoom(startZoom: number, startDistance: number, distance: number): number {
  if (!(startDistance > 0) || !(distance > 0)) return clampDisplayZoom(startZoom);
  return clampDisplayZoom(startZoom + Math.log2(distance / startDistance));
}

/**
 * The tile zoom to request for a display zoom, and how much to scale it.
 *
 * Never above what the source serves (`maxZoom`); beyond it the deepest tiles
 * are drawn larger, which is blurrier than a native tile and far more legible
 * than a flight drawn smaller than its marker.
 */
export function tileZoom(display: number, maxZoom: number): { z: number; scale: number } {
  const z = Math.min(Math.floor(display), maxZoom);
  return { z, scale: 2 ** (display - z) };
}

const KM = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

export function formatAccuracy(metres: number): string {
  if (!Number.isFinite(metres)) return '±?';
  return metres >= 1000 ? `±${KM.format(metres / 1000)} km` : `±${Math.round(metres)} m`;
}

export function accuracyIsCoarse(metres: number): boolean {
  return !Number.isFinite(metres) || metres > COARSE_ACCURACY_M;
}
