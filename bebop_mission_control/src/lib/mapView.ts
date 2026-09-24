/**
 * Scale decisions for the tactical map.
 *
 * The map used a fixed tile zoom of 18, about 0.55 m per pixel at the latitudes
 * the station flies at. A bench flight of a few metres was a dozen pixels,
 * entirely under the aircraft marker. The display zoom is now chosen from the
 * extent of the flight, the same way the offline grid already fitted itself,
 * and the tiles are overzoomed past what their source serves.
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
