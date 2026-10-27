import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Building2, Crosshair, LocateFixed, MapPin, Minus, Plus } from 'lucide-react';
import type { TrackPoint } from '../../types/mission';
import { fromEnu, niceGridStep } from '../../lib/geo';
import {
  MAX_DISPLAY_ZOOM,
  MIN_DISPLAY_ZOOM,
  ZOOM_STEP,
  clampDisplayZoom,
  fixedView,
  type FixedView,
  pinchZoom,
  wheelZoom,
} from '../../lib/mapView';
import { MAP_ANCHOR } from '../../lib/siteAnchor';
import { cn } from '../../lib/format';
import { rotorsTurning } from '../../lib/flightState';

interface TacticalMapProps {
  track: TrackPoint[];
  /** Degrees, compass convention: 0 is north, positive clockwise. */
  heading: number;
  /** Metres; `null` when the parameter document does not hold it, and no circle is drawn. */
  arrivalRadius: number | null;
  stale: boolean;
  /** ARSDK flying state, null when unknown or stale; drives the rotor animation. */
  flyingState?: number | null;
}

const TILE_SIZE = 256;

/**
 * A raster tile source.
 *
 * Satellite imagery only, darkened in CSS into the station's palette: every
 * street basemap bakes street and place names into the raster, where no CSS
 * can remove them, and imagery carries no labels at all.
 */
export interface TileSource {
  id: string;
  url: (s: string, z: number, x: number, y: number) => string;
  subdomains: readonly string[];
  /** Deepest zoom the source serves; the map overzooms past it. */
  maxZoom: number;
  filter: string;
  attribution: string;
}

/**
 * Credit the imagery's licence requires on the map. Equal to the
 * `attribution` of `public/tiles/manifest.json`.
 */
export const TILE_ATTRIBUTION = '© Prefeitura de São Paulo · GeoSampa';

/**
 * Imagery toned down until the overlay reads over it: dimmed, most of its
 * colour taken out, a little contrast back so roads and roofs stay legible.
 */
const IMAGERY_FILTER = 'brightness(0.5) saturate(0.35) contrast(1.15)';

/**
 * The site's imagery, pre-rendered to `public/tiles` and served from disk by
 * the station itself: same origin, no key, nothing leaves the machine. The
 * aircraft's Wi-Fi has no route to the internet, so a remote source is never
 * tried. `maxZoom` is the manifest's deepest level.
 */
export const TILE_SOURCES: readonly TileSource[] = [
  {
    id: 'local-satellite',
    url: (_s: string, z: number, x: number, y: number) => `/tiles/${z}/${x}/${y}.jpg`,
    subdomains: ['a'],
    maxZoom: 20,
    filter: IMAGERY_FILTER,
    attribution: TILE_ATTRIBUTION,
  },
];

/**
 * Drawn size of the aircraft marker relative to its geometry. The marker is
 * what the operator's eye goes to first, across the room from the station.
 */
const MARKER_SCALE = 1.45;

/**
 * Without a single tile loaded in this long, the basemap is given up for the
 * grid. Tiles come from the local disk in milliseconds; silence this long
 * means the tile tree is missing, not slow. The fallback is terminal until the
 * panel remounts: no zoom change requests tiles again.
 */
const TILE_LOAD_TIMEOUT_MS = 2000;

type GeoSource = 'site';

const GEO_LABEL: Record<GeoSource, string> = {
  site: 'odometria + base fixa do local',
};

/** The base the map is drawn around; never moves. */
const ANCHOR = { lat: MAP_ANCHOR.latitude, lng: MAP_ANCHOR.longitude, source: 'site' as GeoSource };

/**
 * Where the aircraft is, over the ground it is actually flying.
 *
 * Two layers. The basemap is raster tiles fetched straight as images — no map
 * library, because a tile grid is sixty lines and a dependency is forever. The
 * overlay is a local projection of the mission's own frame: launch point,
 * trail, arrival radius and the aircraft with its heading, drawn from odometry
 * alone.
 *
 * The launch point is the fixed site anchor (`MAP_ANCHOR`), and the view is
 * centred on it at a fixed scale: the basemap and the base never move, and the
 * aircraft crosses them by its odometry offset. Neither the aircraft's GPS,
 * the operator's position nor the bridge's base re-anchor it, so nothing a
 * fix or a cache does in flight makes the map jump.
 *
 * The tiles are the site's imagery stored with the station and served from
 * disk, so the map needs no network at all. The tactical grid is drawn
 * underneath until a tile actually arrives; a clone without the tile tree
 * (every tile failing, or none answering within a couple of seconds) falls
 * back to the grid for good, with no further requests.
 */
export const TacticalMap: React.FC<TacticalMapProps> = ({
  track,
  heading,
  arrivalRadius,
  stale,
  flyingState = null,
}) => {
  const uid = useId().replace(/:/g, '');
  const [size, setSize] = useState({ width: 640, height: 320 });
  const [tilesOk, setTilesOk] = useState<boolean | null>(null);
  /** Keys of the tiles that failed; only those of the grid on screen count. */
  const failedTiles = useRef(new Set<string>());
  /** Keys of the grid on screen, read by the error handler. */
  const gridKeys = useRef(new Set<string>());
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  /**
   * The operator's zoom, in display-zoom levels; null for the default framing.
   * Set by the buttons, the wheel and a pinch, and cleared by the recentre
   * control, which restores the default scale.
   */
  const [manualZoom, setManualZoom] = useState<number | null>(null);

  const source = TILE_SOURCES[0];

  /**
   * One tile failed.
   *
   * Only every tile of the grid on screen failing, with none loaded, is a
   * clone without the tile tree: the panel then falls back to the local
   * chart. A margin ring outside the stored coverage fails first, since the
   * browser fetches in row order; while any tile of the grid may still load,
   * the basemap is kept. A tile that fails after another has already loaded is
   * left alone — the map is working, and one square missing at the edge of
   * the stored coverage is not worth dropping the whole basemap over.
   */
  const onTileError = React.useCallback((key: string) => {
    const grid = gridKeys.current;
    if (!grid.has(key)) return;
    failedTiles.current.add(key);
    for (const tile of grid) if (!failedTiles.current.has(tile)) return;
    setTilesOk((previous) => (previous === null ? false : previous));
  }, []);

  const onTileLoad = React.useCallback(() => {
    setTilesOk(true);
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(() => {
      const rect = host.getBoundingClientRect();
      setSize({ width: Math.max(1, rect.width), height: Math.max(1, rect.height) });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // The aircraft's own frame: metres east and north of the launch point.
  const last = track[track.length - 1];
  const droneEast = last?.x ?? 0;
  const droneNorth = last?.y ?? 0;

  const geo = ANCHOR;
  /** Where the aircraft is on the ground: the anchor plus the odometry offset. */
  const aircraft = useMemo(() => fromEnu(droneEast, droneNorth, ANCHOR.lat, ANCHOR.lng), [droneEast, droneNorth]);
  const basemapUsable = tilesOk !== false;

  useEffect(() => {
    if (tilesOk !== null) return;
    const id = window.setTimeout(() => {
      setTilesOk((previous) => (previous === null ? false : previous));
    }, TILE_LOAD_TIMEOUT_MS);
    return () => window.clearTimeout(id);
  }, [tilesOk]);

  /**
   * Both layers are centred on the base at a fixed scale: the tiles are laid
   * out around the anchor and the overlay is drawn in metres from it, so the
   * streets and the trail share one metres-per-pixel and neither scrolls.
   * Without tiles the local grid is registered to the same base.
   */
  const view = useMemo(
    () => fixedView(size, ANCHOR, manualZoom, source.maxZoom),
    [size, manualZoom, source.maxZoom]
  );

  // The zoom on screen, read by input handlers that outlive a render.
  const zoomRef = useRef(view.zoom);
  zoomRef.current = view.zoom;

  const zoomBy = useCallback((levels: number) => {
    setManualZoom(clampDisplayZoom(zoomRef.current + levels));
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // Not passive: the wheel over the map zooms it, and must not also scroll
    // the cockpit it sits in.
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      setManualZoom(wheelZoom(zoomRef.current, event.deltaY, event.deltaMode));
    };
    host.addEventListener('wheel', onWheel, { passive: false });
    return () => host.removeEventListener('wheel', onWheel);
  }, []);

  // Two pointers down pinch-zoom around the base, which stays centred.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; zoom: number } | null>(null);
  const spread = () => {
    const [a, b] = Array.from(pointers.current.values());
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : 0;
  };
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.current.size === 2) pinch.current = { distance: spread(), zoom: zoomRef.current };
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pinch.current && pointers.current.size === 2) {
      setManualZoom(pinchZoom(pinch.current.zoom, pinch.current.distance, spread()));
    }
  };
  const onPointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
  };

  const tiles = useMemo(() => (basemapUsable ? tileGrid(view, size, source) : []), [view, size, basemapUsable, source]);
  gridKeys.current = useMemo(() => new Set(tiles.map((tile) => tile.key)), [tiles]);

  const toPath = (points: TrackPoint[]) =>
    points
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${view.sx(p.x).toFixed(1)} ${view.sy(p.y).toFixed(1)}`)
      .join(' ');

  const fullPath = track.length > 1 ? toPath(track) : '';
  const recentPath = track.length > 1 ? toPath(track.slice(Math.max(0, track.length - 30))) : '';

  const homeX = view.sx(0);
  const homeY = view.sy(0);
  const distance = Math.hypot(droneEast, droneNorth);
  const basemapState = tilesOk === true ? 'mapa local' : tilesOk === null ? 'carregando mapa' : 'grade offline';
  const mapStatus = [`${GEO_LABEL[geo.source]} (${MAP_ANCHOR.name})`, basemapState, `${distance.toFixed(1)} m da base`].join(
    ' · '
  );

  // A round number of metres near a fifth of the panel.
  const scaleMetres = niceGridStep((size.width / Math.max(view.pxPerMetre, 0.0001)) * 0.9);
  const scaleBarPx = scaleMetres * view.pxPerMetre;
  const scaleLabel = scaleMetres >= 1 ? `${scaleMetres} m` : `${Math.round(scaleMetres * 100)} cm`;
  const compass = ((heading % 360) + 360) % 360;
  const showGrid = tilesOk !== true;

  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded-panel border border-strut-soft bg-hull-deep">
      {/* The panel's name only. How the position was obtained, whether the
          basemap is live and the range to the base stay available on hover:
          they are diagnostics, not something the operator reads in flight. */}
      <div
        className="flex h-9 shrink-0 items-center gap-2 border-b border-strut-soft px-3"
        title={mapStatus}
      >
        <Crosshair size={12} strokeWidth={2} aria-hidden className="text-frost" />
        <span className="font-cond text-2xs font-semibold tracking-wide text-frost">MAPA TÁTICO</span>
        <span className="sr-only">{mapStatus}</span>
      </div>

      <div
        ref={hostRef}
        className="relative min-h-0 flex-1 touch-none overflow-clip bg-hull-deep"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
      >
        {/* Basemap. */}
        {basemapUsable ? (
          <div aria-hidden className="absolute inset-0">
            {tiles.map((tile) => (
              <img
                key={tile.key}
                src={tile.url}
                alt=""
                width={TILE_SIZE}
                height={TILE_SIZE}
                loading="eager"
                draggable={false}
                onLoad={onTileLoad}
                onError={() => onTileError(tile.key)}
                className="absolute max-w-none select-none"
                style={{
                  left: tile.left,
                  top: tile.top,
                  width: tile.width,
                  height: tile.height,
                  filter: source.filter,
                }}
              />
            ))}
            {/* Sink the basemap into the station's palette so the overlay reads
                over it without fighting the tiles' own greys. */}
            <div
              className="absolute inset-0"
              style={{
                background:
                  'linear-gradient(rgba(0,26,47,0.2), rgba(0,26,47,0.2)), radial-gradient(80% 80% at 50% 50%, transparent 55%, rgba(0,19,31,0.45) 100%)',
              }}
            />
          </div>
        ) : null}

        {/* Overlay: the mission's own frame. */}
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox={`0 0 ${size.width} ${size.height}`}
          role="img"
          aria-label="Trajetória da aeronave sobre o terreno"
        >
          <defs>
            <radialGradient id={`${uid}-halo`}>
              <stop offset="0%" stopColor="#01D5A3" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#01D5A3" stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* The local tactical grid: ruled in real metres, registered to the
              launch point, drawn whenever there is no loaded basemap to read
              distances against — which on the aircraft's own Wi-Fi is always. */}
          {showGrid ? <LocalGrid view={view} size={size} /> : null}

          {/* The radius the return has to land inside. */}
          {arrivalRadius !== null ? (
            <circle
              cx={homeX}
              cy={homeY}
              r={Math.max(arrivalRadius * view.pxPerMetre, 7)}
              fill="#01D5A3"
              fillOpacity="0.08"
              stroke="#5CF2CE"
              strokeOpacity="0.7"
              strokeWidth="1.5"
              strokeDasharray="4 3"
            />
          ) : null}

          {/* A dark casing under the trail keeps it legible over bright tiles. */}
          {fullPath ? (
            <path
              d={fullPath}
              fill="none"
              stroke="#001A2F"
              strokeOpacity="0.6"
              strokeWidth="7.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {fullPath ? (
            <path
              d={fullPath}
              fill="none"
              stroke="#01D5A3"
              strokeOpacity="0.5"
              strokeWidth="4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {recentPath ? (
            <path
              d={recentPath}
              fill="none"
              stroke="#5CF2CE"
              strokeOpacity="0.95"
              strokeWidth="4.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}

          {/* Launch point. */}
          <g transform={`translate(${homeX}, ${homeY})`}>
            <circle r="9" fill="#001A2F" stroke="#E8F2F0" strokeWidth="2.5" />
            <circle r="3" fill="#E8F2F0" />
          </g>
          <text
            x={homeX}
            y={homeY + 27}
            textAnchor="middle"
            className="fill-frost/85 font-cond"
            fontSize="12.5"
            style={{ paintOrder: 'stroke', stroke: '#001A2F', strokeWidth: 3.5 }}
          >
            BASE · DECOLAGEM (0, 0)
          </text>

          {/* The aircraft, at its odometry offset from the fixed base. */}
          <g
            data-aircraft
            transform={`translate(${view.sx(droneEast)}, ${view.sy(droneNorth)})`}
            opacity={stale ? 0.45 : 1}
          >
            <circle r="44" fill={`url(#${uid}-halo)`} />
            <g transform={`scale(${MARKER_SCALE})`}>
              <Quadcopter heading={compass} spinning={rotorsTurning(flyingState)} />
            </g>
          </g>

          {/* Tactical compass: north fixed, heading needle live. */}
          <g transform={`translate(${size.width - 46}, 46) scale(1.35)`}>
            <TacticalCompass x={0} y={0} heading={compass} stale={stale} />
          </g>

          {/* Scale. */}
          <g transform={`translate(20, ${size.height - 18})`}>
            <rect x={-8} y={-25} width={scaleBarPx + 16} height={33} rx={4} fill="#001A2F" fillOpacity="0.62" />
            <line x1="0" y1="0" x2={scaleBarPx} y2="0" stroke="#E8F2F0" strokeOpacity="0.8" strokeWidth="2" />
            <line x1={scaleBarPx / 2} y1="-3" x2={scaleBarPx / 2} y2="3" stroke="#E8F2F0" strokeOpacity="0.6" strokeWidth="1.2" />
            <line x1="0" y1="-5" x2="0" y2="5" stroke="#E8F2F0" strokeOpacity="0.8" strokeWidth="2" />
            <line x1={scaleBarPx} y1="-5" x2={scaleBarPx} y2="5" stroke="#E8F2F0" strokeOpacity="0.8" strokeWidth="2" />
            <text x={scaleBarPx / 2} y="-9" textAnchor="middle" className="fill-frost/90 font-mono" fontSize="11.5">
              {scaleLabel}
            </text>
          </g>
        </svg>

        {/* Manual zoom, under the compass. */}
        <div className="absolute right-2 top-[92px] z-10 flex flex-col overflow-hidden rounded-bezel border border-strut-soft bg-abyss/75 backdrop-blur-sm">
          <button
            type="button"
            onClick={() => zoomBy(ZOOM_STEP)}
            disabled={view.zoom >= MAX_DISPLAY_ZOOM}
            aria-label="Aproximar o mapa"
            title="Aproximar"
            className="grid h-7 w-7 place-items-center text-frost transition-colors hover:bg-hull-raise disabled:cursor-not-allowed disabled:text-haze-deep"
          >
            <Plus size={14} strokeWidth={2} />
          </button>
          <button
            type="button"
            onClick={() => zoomBy(-ZOOM_STEP)}
            disabled={view.zoom <= MIN_DISPLAY_ZOOM}
            aria-label="Afastar o mapa"
            title="Afastar"
            className="grid h-7 w-7 place-items-center border-t border-strut-soft text-frost transition-colors hover:bg-hull-raise disabled:cursor-not-allowed disabled:text-haze-deep"
          >
            <Minus size={14} strokeWidth={2} />
          </button>
          <button
            type="button"
            onClick={() => setManualZoom(null)}
            disabled={manualZoom === null}
            aria-label="Voltar ao enquadramento padrão"
            aria-pressed={manualZoom === null}
            title={manualZoom === null ? 'Enquadramento padrão ativo' : 'Voltar ao enquadramento padrão'}
            className={cn(
              'grid h-7 w-7 place-items-center border-t border-strut-soft transition-colors hover:bg-hull-raise disabled:cursor-default',
              manualZoom === null ? 'text-mint' : 'text-amber'
            )}
          >
            <LocateFixed size={13} strokeWidth={2} />
          </button>
        </div>

        {/* Attribution is a condition of using the tileset. */}
        {tilesOk ? (
          <span className="pointer-events-none absolute bottom-1 right-2 font-mono text-[9px] text-frost/35">
            {source.attribution}
          </span>
        ) : null}
      </div>

      {/* The coordinates, and where that is on the ground. */}
      <div className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-4 gap-y-0.5 border-t border-strut-soft px-3 py-1">
        <span className="flex min-w-0 max-w-full items-center gap-1.5" title={GEO_LABEL[geo.source]}>
          <MapPin size={11} strokeWidth={2} className="shrink-0 text-frost" />
          <span className="shrink-0 font-cond text-3xs tracking-wide text-haze-deep">BASE FIXA:</span>{' '}
          <span className="tnum truncate font-mono text-2xs text-frost">
            {`${MAP_ANCHOR.latitude.toFixed(6)}, ${MAP_ANCHOR.longitude.toFixed(6)}`}
          </span>
        </span>
        <GeoField icon={<Building2 size={11} strokeWidth={2} />} label="Cidade" value={MAP_ANCHOR.city} />
        <GeoField icon={<MapPin size={11} strokeWidth={2} />} label="Localização" value={MAP_ANCHOR.name} />
      </div>
    </div>
  );
};

/** One tile of the basemap, placed in panel pixels. */
export interface PlacedTile {
  key: string;
  url: string;
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The tile grid covering a panel of `size`, centred on the base.
 *
 * `scripts/offline_tiles.py` (`map_loop_tiles`) mirrors the column, row and
 * origin arithmetic to plan the stored tile set; a change here must be made
 * there too. Edges are rounded to whole pixels and each tile spans to where
 * the next one starts: an overzoomed tile is a fractional number of pixels
 * wide, and fractional edges leave hairline seams between neighbours.
 */
export function tileGrid(
  view: Pick<FixedView, 'centre' | 'tile' | 'tileSize' | 'cx' | 'cy'>,
  size: { width: number; height: number },
  source: TileSource
): PlacedTile[] {
  const { centre, tile, tileSize } = view;
  const cols = Math.ceil(size.width / tileSize) + 2;
  const rows = Math.ceil(size.height / tileSize) + 2;
  const originX = Math.floor(centre.x) - Math.floor(cols / 2);
  const originY = Math.floor(centre.y) - Math.floor(rows / 2);
  const n = 2 ** tile.z;
  const edgeX = (tx: number) => Math.round(view.cx + (tx - centre.x) * tileSize);
  const edgeY = (ty: number) => Math.round(view.cy + (ty - centre.y) * tileSize);

  const out: PlacedTile[] = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const tx = originX + col;
      const ty = originY + row;
      if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
      const shard = source.subdomains[Math.abs(tx + ty) % source.subdomains.length];
      const left = edgeX(tx);
      const top = edgeY(ty);
      out.push({
        // The zoom is part of the identity: a tile at z18 and one at z19
        // share coordinates only by coincidence.
        key: `${source.id}-${tile.z}-${tx}-${ty}`,
        url: source.url(shard, tile.z, tx, ty),
        left,
        top,
        width: edgeX(tx + 1) - left,
        height: edgeY(ty + 1) - top,
      });
    }
  }
  return out;
}

/** North at the top, the four cardinals, and the aircraft's heading as a needle. */
const TacticalCompass: React.FC<{ x: number; y: number; heading: number; stale: boolean }> = ({
  x,
  y,
  heading,
  stale,
}) => {
  const ticks = Array.from({ length: 24 }, (_, i) => i * 15);
  return (
    <g transform={`translate(${x}, ${y})`} aria-hidden>
      <circle r="24" fill="#001A2F" fillOpacity="0.78" stroke="#14455D" strokeWidth="1" />
      {ticks.map((deg) => {
        const major = deg % 90 === 0;
        const rad = (deg * Math.PI) / 180;
        const r1 = major ? 16 : 19;
        return (
          <line
            key={deg}
            x1={Math.sin(rad) * r1}
            y1={-Math.cos(rad) * r1}
            x2={Math.sin(rad) * 22}
            y2={-Math.cos(rad) * 22}
            stroke="#7C99A4"
            strokeOpacity={major ? 0.9 : 0.45}
            strokeWidth={major ? 1.3 : 0.8}
          />
        );
      })}
      <text y="-7" textAnchor="middle" fontSize="7.5" className="fill-mint font-mono" fontWeight={700}>
        N
      </text>
      <text x="10" y="2.6" textAnchor="middle" fontSize="6.5" className="fill-frost/60 font-mono">
        L
      </text>
      <text y="12" textAnchor="middle" fontSize="6.5" className="fill-frost/60 font-mono">
        S
      </text>
      <text x="-10" y="2.6" textAnchor="middle" fontSize="6.5" className="fill-frost/60 font-mono">
        O
      </text>
      <g transform={`rotate(${heading})`} opacity={stale ? 0.35 : 1}>
        <path d="M 0 -21 L 3 -4 L 0 -6 L -3 -4 Z" fill="#5CF2CE" />
      </g>
      <circle r="1.6" fill="#E8F2F0" />
    </g>
  );
};

/**
 * A metric grid in the mission's own frame.
 *
 * Every line is a round number of metres from the launch point, so the operator
 * reads distance off the chart rather than estimating it against a scale bar in
 * the corner. The step is redrawn as the flight spreads out, which keeps the
 * square count roughly constant instead of collapsing to one cell or to noise.
 */
const LocalGrid: React.FC<{
  view: { pxPerMetre: number; sx: (east: number) => number; sy: (north: number) => number };
  size: { width: number; height: number };
}> = ({ view, size }) => {
  const spanMetres = Math.max(size.width, size.height) / Math.max(view.pxPerMetre, 0.0001);
  const step = niceGridStep(spanMetres);
  const stepPx = step * view.pxPerMetre;
  if (!Number.isFinite(stepPx) || stepPx < 6) return null;

  // Walk out from the launch point in both directions until the viewport is
  // covered, so the lines stay pinned to the ground and not to the panel.
  const lines: React.ReactNode[] = [];
  const maxEast = Math.ceil((size.width / view.pxPerMetre) / step) + 2;
  const maxNorth = Math.ceil((size.height / view.pxPerMetre) / step) + 2;

  for (let i = -maxEast; i <= maxEast; i++) {
    const x = view.sx(i * step);
    if (x < -stepPx || x > size.width + stepPx) continue;
    lines.push(
      <line
        key={`v${i}`}
        x1={x}
        y1={0}
        x2={x}
        y2={size.height}
        stroke="#14455D"
        strokeOpacity={i === 0 ? 0.85 : 0.35}
        strokeWidth={i === 0 ? 1.5 : 1}
      />
    );
  }
  for (let i = -maxNorth; i <= maxNorth; i++) {
    const y = view.sy(i * step);
    if (y < -stepPx || y > size.height + stepPx) continue;
    lines.push(
      <line
        key={`h${i}`}
        x1={0}
        y1={y}
        x2={size.width}
        y2={y}
        stroke="#14455D"
        strokeOpacity={i === 0 ? 0.85 : 0.35}
        strokeWidth={i === 0 ? 1.5 : 1}
      />
    );
  }

  return (
    <g aria-hidden>
      {lines}
      <text
        x={12}
        y={18}
        className="fill-frost/55 font-cond"
        fontSize="12"
        style={{ letterSpacing: '0.08em' }}
      >
        GRADE TÁTICA LOCAL · {step >= 1 ? `${step} m` : `${(step * 100).toFixed(0)} cm`}
      </text>
    </g>
  );
};

/**
 * The airframe, drawn as what it is.
 *
 * A Bebop 2 seen from above: four arms off a central body, a guarded rotor at
 * each tip, and a nose the operator can read at a glance. The generic dot and cone
 * this replaces told you where the aircraft was but not which way it was
 * pointing — on a map where the whole point is the heading relative to the
 * street under it, the marker has to carry its own orientation.
 *
 * The whole drawing rotates with the compass, so the nose is the heading. The
 * rotors turn only while the aircraft reports them turning (its ARSDK flying
 * state), not merely while telemetry is live: on the ground, armed or
 * landed, they stand still. A stale link reads through the marker's fade.
 */
const Quadcopter: React.FC<{ heading: number; spinning: boolean }> = ({ heading, spinning }) => {
  // Arm tips, clockwise from front-right, in the airframe's own frame.
  const arms: [number, number][] = [
    [11, -11],
    [11, 11],
    [-11, 11],
    [-11, -11],
  ];

  return (
    <g transform={`rotate(${heading})`}>
      {/* Heading wedge: where the camera is looking. */}
      <path d="M 0 -6 L -9 -27 A 26 26 0 0 1 9 -27 Z" fill="#5CF2CE" fillOpacity="0.28" />

      {/* Arms. */}
      {arms.map(([x, y]) => (
        <line
          key={`arm-${x}-${y}`}
          x1="0"
          y1="0"
          x2={x}
          y2={y}
          stroke="#001A2F"
          strokeWidth="5.5"
          strokeLinecap="round"
        />
      ))}
      {arms.map(([x, y]) => (
        <line
          key={`arm-fill-${x}-${y}`}
          x1="0"
          y1="0"
          x2={x}
          y2={y}
          stroke="#01D5A3"
          strokeWidth="3"
          strokeLinecap="round"
        />
      ))}

      {/* Rotors inside their guards: the guard ring is the Bebop 2's
          silhouette from above, the inner disc the rotor that spins. */}
      {arms.map(([x, y], index) => (
        <g key={`rotor-${x}-${y}`} transform={`translate(${x}, ${y})`}>
          <circle r="7.5" fill="none" stroke="#001A2F" strokeWidth="3.6" />
          <circle r="7.5" fill="none" stroke="#01D5A3" strokeWidth="1.6" />
          <circle r="5.6" fill="#001A2F" fillOpacity="0.85" stroke="#01D5A3" strokeOpacity="0.55" strokeWidth="0.8" />
          <g>
            <line
              x1="-4.5"
              y1="0"
              x2="4.5"
              y2="0"
              stroke="#5CF2CE"
              strokeWidth="1.4"
              strokeLinecap="round"
              opacity="0.9"
            />
            <line
              x1="0"
              y1="-4.5"
              x2="0"
              y2="4.5"
              stroke="#5CF2CE"
              strokeWidth="1.4"
              strokeLinecap="round"
              opacity="0.55"
            />
            {spinning ? (
              <animateTransform
                attributeName="transform"
                type="rotate"
                from="0"
                to={index % 2 === 0 ? 360 : -360}
                dur="0.42s"
                repeatCount="indefinite"
              />
            ) : null}
          </g>
        </g>
      ))}

      {/* Fuselage, with the nose marked. */}
      <rect x="-6" y="-8.5" width="12" height="17" rx="4.5" fill="#001A2F" stroke="#01D5A3" strokeWidth="2" />
      <path d="M 0 -11.5 L 3.2 -7 L -3.2 -7 Z" fill="#5CF2CE" />
      <circle cy="1.5" r="2.2" fill="#5CF2CE" fillOpacity="0.85" />
    </g>
  );
};

const GeoField: React.FC<{ icon?: React.ReactNode; label: string; value: string | null }> = ({
  icon,
  label,
  value,
}) => (
  <span className="flex min-w-0 max-w-full items-center gap-1.5" title={value ?? 'não resolvido'}>
    {icon ? <span className="shrink-0 text-haze">{icon}</span> : null}
    <span className="shrink-0 font-cond text-3xs tracking-wide text-haze-deep">{`${label}:`}</span>{' '}
    <span className={cn('min-w-0 truncate text-2xs', value ? 'text-frost' : 'text-haze-deep')}>
      {value ?? '—'}
    </span>
  </span>
);
