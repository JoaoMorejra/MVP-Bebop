import React, { useEffect, useId, useMemo, useState } from 'react';
import { Building2, Crosshair, MapPin } from 'lucide-react';
import type { TrackPoint } from '../../types/mission';
import { useReverseGeocode } from '../../hooks/useReverseGeocode';
import { useOperatorLocation } from '../../hooks/useOperatorLocation';
import { fromEnu, isValidCoordinate, niceGridStep } from '../../lib/geo';
import {
  accuracyIsCoarse,
  displayZoom,
  formatAccuracy,
  pixelsPerMetre,
  tileZoom,
} from '../../lib/mapView';
import { cn } from '../../lib/format';

interface TacticalMapProps {
  track: TrackPoint[];
  /** The aircraft's own fix when `gpsFix`; otherwise the bridge's projection. */
  latitude: number;
  longitude: number;
  /** Degrees, compass convention: 0 is north, positive clockwise. */
  heading: number;
  gpsFix: boolean;
  /** The real reference the telemetry bridge placed the flight against, if any. */
  baseLatitude?: number | null;
  baseLongitude?: number | null;
  baseSource?: string;
  arrivalRadius: number;
  stale: boolean;
  /**
   * No mission yet: frame the surroundings rather than the trail. The trail is
   * not empty before a launch (odometry accumulates until the launch resets
   * it), so this is keyed to the mission, not to the track.
   */
  overview?: boolean;
}

const TILE_SIZE = 256;

/**
 * Tile sources, in the order they are tried.
 *
 * Satellite imagery only, darkened in CSS into the station's palette. Every
 * street basemap this map could reach bakes street and place names into the
 * raster, where no CSS can remove them: MapTiler `dataviz-dark` and
 * `backdrop-dark` on the station's key, Mapbox `dark-v11`, OSM's standard
 * tiles, and Esri's dark gray base; CartoDB's label-free variant now answers
 * without a key with an "API KEY REQUIRED" watermark and a 200, which no error
 * handler can catch. Imagery carries no labels at all. Without a source that
 * answers, the panel falls back to the offline tactical grid.
 *
 * Each entry lists its subdomains. A tile is requested from the shard its own
 * coordinates select, which is what keeps a viewport's worth of tiles from
 * queueing behind one host's connection limit — the cause of the black squares
 * an operator sees as a half-drawn map.
 */
interface TileSource {
  id: string;
  url: (s: string, z: number, x: number, y: number) => string;
  subdomains: readonly string[];
  /** Deepest zoom the source serves; the map overzooms past it. */
  maxZoom: number;
  filter: string;
  attribution: string;
}

const MAP_API_KEY =
  import.meta.env.VITE_MAP_API_KEY ||
  import.meta.env.VITE_MAPBOX_TOKEN ||
  import.meta.env.VITE_MAPTILER_KEY ||
  'gfppK5Nhedqq438PCaw7';

/**
 * Imagery toned down until the overlay reads over it: dimmed, most of its
 * colour taken out, a little contrast back so roads and roofs stay legible.
 */
const IMAGERY_FILTER = 'brightness(0.5) saturate(0.35) contrast(1.15)';

const TILE_SOURCES: readonly TileSource[] = (() => {
  const sources: TileSource[] = [];

  if (MAP_API_KEY.startsWith('pk.')) {
    sources.push({
      id: 'mapbox-satellite',
      url: (_s: string, z: number, x: number, y: number) =>
        `https://api.mapbox.com/v4/mapbox.satellite/${z}/${x}/${y}.jpg?access_token=${MAP_API_KEY}`,
      subdomains: ['a'],
      maxZoom: 19,
      filter: IMAGERY_FILTER,
      attribution: '© Mapbox · © Maxar',
    });
  } else if (MAP_API_KEY) {
    // satellite-v2 advertises zoom 22, but its tiles shrink past 20 where the
    // imagery is upsampled; overzooming locally past 20 costs nothing.
    sources.push({
      id: 'maptiler-satellite',
      url: (_s: string, z: number, x: number, y: number) =>
        `https://api.maptiler.com/tiles/satellite-v2/${z}/${x}/${y}.jpg?key=${MAP_API_KEY}`,
      subdomains: ['a'],
      maxZoom: 20,
      filter: IMAGERY_FILTER,
      attribution: '© MapTiler · © OpenStreetMap',
    });
  }

  return sources;
})();

/** Failures within one source before the next is tried. */
const SOURCE_FAILURE_BUDGET = 6;
/** Never zoom the overlay closer than this, or a stationary aircraft fills it. */
const MIN_EXTENT_M = 40;
/**
 * Extent framed before a mission, in metres: wide enough to read the site on
 * the imagery. Once a mission starts the view fits the flight again, down to
 * {@link MIN_EXTENT_M}.
 */
const OVERVIEW_EXTENT_M = 300;
/**
 * Drawn size of the aircraft marker relative to its geometry. The marker is
 * what the operator's eye goes to first, across the room from the station.
 */
const MARKER_SCALE = 1.45;

/** Web Mercator, in fractional tiles. */
function project(lat: number, lng: number, zoom: number) {
  const n = 2 ** zoom;
  const x = ((lng + 180) / 360) * n;
  const latRad = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return { x, y };
}

/** Without a single tile loaded in this long, the basemap is treated as offline. */
const TILE_LOAD_TIMEOUT_MS = 8000;
/** How often an offline map probes for the tiles coming back. */
const TILE_PROBE_INTERVAL_MS = 45000;

type GeoSource = 'gps' | 'device' | 'host' | 'cache' | 'bridge' | 'none';

const GEO_LABEL: Record<GeoSource, string> = {
  gps: 'GPS da aeronave',
  device: 'odometria + posição do operador',
  host: 'odometria + posição aproximada pela rede',
  cache: 'odometria + base em cache',
  bridge: 'odometria + base real',
  none: 'odometria (sem referência geográfica)',
};

/**
 * Where the aircraft is, over the ground it is actually flying.
 *
 * Two layers. The basemap is raster tiles fetched straight as images — no map
 * library, because a tile grid is sixty lines and a dependency is forever. The
 * overlay is a local projection of the mission's own frame: launch point,
 * trail, arrival radius and the aircraft with its heading, drawn from odometry
 * alone.
 *
 * The geographic position comes from the best source available, in order: the
 * aircraft's own GPS fix; otherwise the launch point placed on the operator's
 * real position (a live fix, or the one cached while the station still had
 * internet) with the odometry offset added to it. There is no hard-coded city
 * any more: with none of those, the map says so and draws the local grid.
 *
 * The aircraft's Wi-Fi has no route to the internet, so the tiles are expected
 * to fail in the field. The panel does not wait on them: the tactical grid is
 * drawn underneath until a tile actually arrives, a load that has produced
 * nothing within a few seconds is declared offline, and an offline map keeps
 * probing quietly so the basemap returns on its own when a route does.
 */
export const TacticalMap: React.FC<TacticalMapProps> = ({
  track,
  latitude,
  longitude,
  heading,
  gpsFix,
  baseLatitude,
  baseLongitude,
  baseSource,
  arrivalRadius,
  stale,
  overview = false,
}) => {
  const uid = useId().replace(/:/g, '');
  const [size, setSize] = useState({ width: 640, height: 320 });
  const [tilesOk, setTilesOk] = useState<boolean | null>(null);
  /** Index into `TILE_SOURCES`; advanced when the current source keeps failing. */
  const [sourceIndex, setSourceIndex] = useState(0);
  const failures = React.useRef(0);
  const hostRef = React.useRef<HTMLDivElement | null>(null);

  const source = TILE_SOURCES[Math.min(sourceIndex, TILE_SOURCES.length - 1)];

  /**
   * One tile failed.
   *
   * A handful of failures is a source that is not answering, not a network that
   * is down: the next source is tried before the basemap is given up on, and
   * only after the list is exhausted does the panel fall back to the local
   * chart. A tile that fails after another has already loaded is left alone —
   * the map is working, and one missing square is not worth restarting the
   * whole grid over.
   */
  const onTileError = React.useCallback(() => {
    setTilesOk((previous) => {
      if (previous === true) return previous;
      failures.current += 1;
      if (failures.current < SOURCE_FAILURE_BUDGET) return previous;
      failures.current = 0;
      let exhausted = false;
      setSourceIndex((index) => {
        if (index + 1 < TILE_SOURCES.length) return index + 1;
        exhausted = true;
        return index;
      });
      return exhausted ? false : previous;
    });
  }, []);

  const onTileLoad = React.useCallback(() => {
    failures.current = 0;
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

  /**
   * Which geographic position the flight is placed at.
   *
   * The GPS fix, when the aircraft has one, is the truth and nothing else is
   * consulted. Otherwise the launch point is placed on the operator's position
   * and the odometry offset carries the aircraft away from it — which keeps
   * the marker over the right street and the trail registered to it.
   */
  const operator = useOperatorLocation(!gpsFix);
  const geo = useMemo((): { lat: number; lng: number; source: GeoSource } => {
    if (gpsFix && isValidCoordinate(latitude, longitude)) {
      return { lat: latitude, lng: longitude, source: 'gps' };
    }
    const op = operator.location;
    if (op && isValidCoordinate(op.latitude, op.longitude)) {
      const at = fromEnu(droneEast, droneNorth, op.latitude, op.longitude);
      return { ...at, source: op.source };
    }
    if (isValidCoordinate(baseLatitude, baseLongitude)) {
      const at = fromEnu(droneEast, droneNorth, baseLatitude as number, baseLongitude as number);
      return { ...at, source: baseSource === 'operator-cache' ? 'cache' : 'bridge' };
    }
    return { lat: 0, lng: 0, source: 'none' };
  }, [gpsFix, latitude, longitude, operator.location, baseLatitude, baseLongitude, baseSource, droneEast, droneNorth]);

  const geoKnown = geo.source !== 'none';
  /**
   * How far the base itself may be from where it is drawn. A network-derived
   * position is a city centroid, kilometres from the launch point, and the
   * flight is then registered to the wrong streets however exact the odometry
   * on top of it is; saying so is the only honest fix available without a fix.
   */
  const operatorBased = geo.source === 'device' || geo.source === 'host' || geo.source === 'cache';
  const baseAccuracy = operatorBased && operator.location ? operator.location.accuracyM : null;
  const baseCoarse = baseAccuracy !== null && accuracyIsCoarse(baseAccuracy);
  // With nothing to centre on, there is no tile worth requesting.
  const basemapUsable = geoKnown && tilesOk !== false;

  // A load that never answers — DNS hanging on a network with no route — is
  // an offline map, not one that is about to appear.
  useEffect(() => {
    if (!geoKnown || tilesOk !== null) return;
    const id = window.setTimeout(() => {
      setTilesOk((previous) => (previous === null ? false : previous));
    }, TILE_LOAD_TIMEOUT_MS);
    return () => window.clearTimeout(id);
  }, [geoKnown, tilesOk, sourceIndex]);

  const place = useReverseGeocode(geo.lat, geo.lng, geoKnown && tilesOk === true);

  /**
   * Both layers are centred on the aircraft, so the basemap scrolls under a
   * fixed marker the way a navigation map does.
   *
   * The scale fits the flight: the launch point and the whole trail stay in
   * view with a margin. With tiles, that fit picks a display zoom, and the
   * tiles are requested at the deepest zoom their source serves and scaled to
   * it, so the overlay and the streets share one metres-per-pixel. Without
   * tiles the overlay fits the flight directly.
   */
  const view = useMemo(() => {
    let extent = overview ? OVERVIEW_EXTENT_M : MIN_EXTENT_M;
    for (const p of track) {
      extent = Math.max(
        extent,
        Math.abs(p.x - droneEast) * 2.4,
        Math.abs(p.y - droneNorth) * 2.4,
        Math.abs(p.x) * 2.4,
        Math.abs(p.y) * 2.4
      );
    }

    let pxPerMetre: number;
    let tile = { z: 0, scale: 1 };
    let centre = { x: 0, y: 0 };
    if (basemapUsable) {
      const zoom = displayZoom(extent, size, geo.lat);
      tile = tileZoom(zoom, source.maxZoom);
      centre = project(geo.lat, geo.lng, tile.z);
      pxPerMetre = pixelsPerMetre(zoom, geo.lat);
    } else {
      pxPerMetre = Math.min(size.width, size.height) / extent;
    }

    const cx = size.width / 2;
    const cy = size.height / 2;

    // Screen position of a point given in metres east/north of launch.
    const sx = (east: number) => cx + (east - droneEast) * pxPerMetre;
    const sy = (north: number) => cy - (north - droneNorth) * pxPerMetre;

    return { centre, tile, tileSize: TILE_SIZE * tile.scale, pxPerMetre, cx, cy, sx, sy };
  }, [geo.lat, geo.lng, basemapUsable, size, track, droneEast, droneNorth, source.maxZoom, overview]);

  // Offline, probe one tile now and then; the first that loads brings the
  // basemap back without a flicker through a half-loaded grid.
  useEffect(() => {
    if (tilesOk !== false || !geoKnown) return;
    const id = window.setInterval(() => {
      const probe = new Image();
      const tx = Math.floor(view.centre.x);
      const ty = Math.floor(view.centre.y);
      const first = TILE_SOURCES[0];
      probe.onload = () => {
        failures.current = 0;
        setSourceIndex(0);
        setTilesOk(null);
      };
      const z = view.tile.z;
      const url = first.url(first.subdomains[0], z, tx, ty);
      probe.src = `${url}${url.includes('?') ? '&' : '?'}probe=${Date.now()}`;
    }, TILE_PROBE_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [tilesOk, geoKnown, view.centre.x, view.centre.y, view.tile.z]);

  // Tile grid covering the viewport, centred on the aircraft.
  const tiles = useMemo(() => {
    if (!basemapUsable) return [];
    const { centre, tile, tileSize } = view;
    const cols = Math.ceil(size.width / tileSize) + 2;
    const rows = Math.ceil(size.height / tileSize) + 2;
    const originX = Math.floor(centre.x) - Math.floor(cols / 2);
    const originY = Math.floor(centre.y) - Math.floor(rows / 2);
    const n = 2 ** tile.z;

    const out: { key: string; url: string; left: number; top: number; size: number }[] = [];
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const tx = originX + col;
        const ty = originY + row;
        if (tx < 0 || ty < 0 || tx >= n || ty >= n) continue;
        // Shard by tile coordinate rather than at random, so the same tile
        // keeps the same host across re-renders and stays in the browser cache.
        const shard = source.subdomains[Math.abs(tx + ty) % source.subdomains.length];
        out.push({
          // The zoom is part of the identity: a tile at z18 and one at z19
          // share coordinates only by coincidence.
          key: `${source.id}-${tile.z}-${tx}-${ty}`,
          url: source.url(shard, tile.z, tx, ty),
          left: view.cx + (tx - centre.x) * tileSize,
          top: view.cy + (ty - centre.y) * tileSize,
          size: tileSize,
        });
      }
    }
    return out;
  }, [view, size, basemapUsable, source]);

  const toPath = (points: TrackPoint[]) =>
    points
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${view.sx(p.x).toFixed(1)} ${view.sy(p.y).toFixed(1)}`)
      .join(' ');

  const fullPath = track.length > 1 ? toPath(track) : '';
  const recentPath = track.length > 1 ? toPath(track.slice(Math.max(0, track.length - 30))) : '';

  const homeX = view.sx(0);
  const homeY = view.sy(0);
  const distance = Math.hypot(droneEast, droneNorth);
  const basemapState =
    tilesOk === true && geoKnown ? 'mapa online' : tilesOk === null && geoKnown ? 'carregando mapa' : 'grade offline';
  const mapStatus = [
    `${GEO_LABEL[geo.source]}${baseAccuracy !== null ? ` ${formatAccuracy(baseAccuracy)}` : ''}`,
    baseCoarse ? 'base aproximada: a posição sobre as ruas não é confiável' : null,
    !geoKnown && operator.status === 'locating' ? 'localizando a estação' : null,
    basemapState,
    `${distance.toFixed(1)} m da base`,
  ]
    .filter(Boolean)
    .join(' · ');

  // A round number of metres near a fifth of the panel.
  const scaleMetres = niceGridStep((size.width / Math.max(view.pxPerMetre, 0.0001)) * 0.9);
  const scaleBarPx = scaleMetres * view.pxPerMetre;
  const scaleLabel = scaleMetres >= 1 ? `${scaleMetres} m` : `${Math.round(scaleMetres * 100)} cm`;
  const compass = ((heading % 360) + 360) % 360;
  const showGrid = tilesOk !== true || !geoKnown;

  return (
    <div className="flex min-h-0 flex-col overflow-hidden rounded-panel border border-strut-soft bg-hull-deep">
      {/* The panel's name only. How the position was obtained, whether the
          basemap is live and the range to the base stay available on hover:
          they are diagnostics, not something the operator reads in flight. */}
      <div
        className="flex h-9 shrink-0 items-center gap-2 border-b border-strut-soft px-3"
        title={mapStatus}
      >
        <Crosshair
          size={12}
          strokeWidth={2}
          aria-hidden
          className={
            baseCoarse
              ? 'text-amber'
              : geo.source === 'gps'
              ? 'text-mint'
              : geoKnown
              ? 'text-frost'
              : 'text-haze-deep'
          }
        />
        <span className="font-cond text-2xs font-semibold tracking-wide text-frost">MAPA TÁTICO</span>
        <span className="sr-only">{mapStatus}</span>
      </div>

      <div ref={hostRef} className="relative min-h-0 flex-1 overflow-hidden bg-hull-deep">
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
                onError={onTileError}
                className="absolute select-none"
                style={{
                  left: tile.left,
                  top: tile.top,
                  width: tile.size,
                  height: tile.size,
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

          {/* The aircraft, fixed at the centre with the ground moving under it. */}
          <g transform={`translate(${view.cx}, ${view.cy})`} opacity={stale ? 0.45 : 1}>
            <circle r="44" fill={`url(#${uid}-halo)`} />
            <g transform={`scale(${MARKER_SCALE})`}>
              <Quadcopter heading={compass} spinning={!stale} />
            </g>
          </g>
          <text
            x={view.cx}
            y={view.cy - 50}
            textAnchor="middle"
            className="fill-mint font-mono"
            fontSize="12"
            style={{ paintOrder: 'stroke', stroke: '#001A2F', strokeWidth: 3.5 }}
          >
            {`E ${droneEast.toFixed(1)} · N ${droneNorth.toFixed(1)} m`}
          </text>

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

        {/* Attribution is a condition of using the tileset. */}
        {tilesOk && geoKnown ? (
          <span className="pointer-events-none absolute bottom-1 right-2 font-mono text-[9px] text-frost/35">
            {source.attribution}
          </span>
        ) : null}
      </div>

      {/* The coordinates, and where that is on the ground. */}
      <div className="flex h-9 shrink-0 items-center gap-4 border-t border-strut-soft px-3">
        <span className="flex min-w-0 shrink-0 items-center gap-1.5" title={GEO_LABEL[geo.source]}>
          <MapPin
            size={11}
            strokeWidth={2}
            className={geo.source === 'gps' ? 'text-mint' : geoKnown ? 'text-frost' : 'text-haze-deep'}
          />
          <span className="font-cond text-3xs tracking-wide text-haze-deep">
            {geo.source === 'gps' ? 'GPS' : 'ODOM'}
          </span>
          <span className={cn('tnum font-mono text-2xs', geoKnown ? 'text-frost' : 'text-haze-deep')}>
            {geoKnown ? `${geo.lat.toFixed(6)}, ${geo.lng.toFixed(6)}` : 'sem referência'}
          </span>
        </span>
        <GeoField
          icon={<Building2 size={11} strokeWidth={2} />}
          label="Cidade"
          value={place.city ?? (geoKnown ? operator.location?.city ?? null : null)}
        />

      </div>
    </div>
  );
};

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
 * A Bebop 2 seen from above: four arms off a central body, a rotor disc at each
 * tip, and a nose the operator can read at a glance. The generic dot and cone
 * this replaces told you where the aircraft was but not which way it was
 * pointing — on a map where the whole point is the heading relative to the
 * street under it, the marker has to carry its own orientation.
 *
 * The whole drawing rotates with the compass, so the nose is the heading. The
 * discs turn only while telemetry is live: a stationary rotor is how a stale
 * link reads without a caption.
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

      {/* Rotor discs. */}
      {arms.map(([x, y], index) => (
        <g key={`rotor-${x}-${y}`} transform={`translate(${x}, ${y})`}>
          <circle r="6.5" fill="#001A2F" fillOpacity="0.85" stroke="#01D5A3" strokeWidth="1.5" />
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
  <span className="flex min-w-0 items-center gap-1.5" title={value ?? 'não resolvido'}>
    {icon ? <span className="shrink-0 text-haze">{icon}</span> : null}
    <span className="shrink-0 font-cond text-3xs tracking-wide text-haze-deep">{label}</span>
    <span className={cn('truncate text-2xs', value ? 'text-frost' : 'text-haze-deep')}>
      {value ?? '—'}
    </span>
  </span>
);
