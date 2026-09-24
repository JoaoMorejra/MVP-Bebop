import { useEffect, useRef, useState } from 'react';
import { useBridge } from './useBridge';

export interface OperatorLocation {
  latitude: number;
  longitude: number;
  /** Radius of the reported 68 % confidence circle, in metres. */
  accuracyM: number;
  at: number;
  /**
   * `device` is the laptop's own positioning; `host` is the coarse network
   * fallback; `cache` is the last of either, kept on disk for when the station
   * is on the aircraft's Wi-Fi and has no route to ask again.
   */
  source: 'device' | 'host' | 'cache';
  city?: string | null;
}

export type OperatorLocationStatus = 'idle' | 'locating' | 'ready' | 'denied' | 'unavailable';

const LOCAL_CACHE_KEY = 'bmg.operator-location.v1';
/** A device fix is persisted to the host again only after moving or ageing this much. */
const PERSIST_MIN_MOVE_M = 50;
const PERSIST_MIN_AGE_MS = 10 * 60 * 1000;

function readLocalCache(): OperatorLocation | null {
  try {
    const raw = window.localStorage.getItem(LOCAL_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as OperatorLocation;
    if (!Number.isFinite(parsed.latitude) || !Number.isFinite(parsed.longitude)) return null;
    return { ...parsed, source: 'cache' };
  } catch {
    return null;
  }
}

function writeLocalCache(location: OperatorLocation) {
  try {
    window.localStorage.setItem(LOCAL_CACHE_KEY, JSON.stringify(location));
  } catch {
    /* private mode or quota: the host's disk cache is the real store */
  }
}

function metresBetween(aLat: number, aLng: number, bLat: number, bLng: number) {
  const dLat = (aLat - bLat) * 111139;
  const dLng = (aLng - bLng) * 111139 * Math.cos((aLat * Math.PI) / 180);
  return Math.hypot(dLat, dLng);
}

/**
 * Where the station itself is, from the laptop's own positioning.
 *
 * The aircraft's latitude and longitude are synthesised from odometry whenever
 * it has no GPS fix, which is every flight indoors and most flights in a yard.
 * The laptop knows better: it positions from the Wi-Fi networks around it, or
 * failing that from its public address, and either is enough to put the map
 * over the right street and name the right city.
 *
 * Both need the internet, and the aircraft's Wi-Fi has none. So every answer is
 * cached — in this renderer and, through the host, on disk — and the cached one
 * is offered the moment the hook starts, before any live attempt has had time
 * to fail. A live fix replaces it as soon as one arrives.
 *
 * Denial is a final answer and stops the watch; every other failure falls back
 * to the host, whose own fallback is the disk cache.
 */
export function useOperatorLocation(enabled: boolean) {
  const bridge = useBridge();
  const [location, setLocation] = useState<OperatorLocation | null>(() =>
    typeof window === 'undefined' ? null : readLocalCache()
  );
  const [status, setStatus] = useState<OperatorLocationStatus>(() =>
    typeof window !== 'undefined' && readLocalCache() ? 'ready' : 'idle'
  );
  /** Set once the browser produces a real fix, so the fallback stands down. */
  const deviceFixed = useRef(false);
  const lastPersisted = useRef<{ lat: number; lng: number; at: number } | null>(null);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let watchId: number | null = null;

    /**
     * The fallback, used when the browser cannot produce a fix.
     *
     * Electron's geolocation resolves through Chromium's network location
     * provider, which needs a Google API key compiled into the build — the
     * stock binary has none, so the permission can be granted and no position
     * ever arrive. The host answers with its public address's city, or with
     * the cached position when there is no route at all.
     */
    const askHost = async () => {
      if (!bridge) {
        if (!cancelled) setStatus((previous) => (previous === 'ready' ? previous : 'unavailable'));
        return;
      }
      const result = await bridge.getHostLocation().catch(() => null);
      if (cancelled) return;
      if (!result?.success || result.latitude === undefined || result.longitude === undefined) {
        setStatus((previous) => (previous === 'ready' ? previous : 'unavailable'));
        return;
      }
      const next: OperatorLocation = {
        latitude: result.latitude,
        longitude: result.longitude,
        accuracyM: result.accuracyM ?? 5000,
        at: result.cachedAt ?? Date.now(),
        source: result.cached ? 'cache' : 'host',
        city: result.city ?? null,
      };
      // A device fix, if one arrived while this was in flight, is the better
      // answer and must not be overwritten by the coarse one.
      setLocation((previous) => (previous?.source === 'device' ? previous : next));
      if (!result.cached) writeLocalCache(next);
      setStatus((previous) => (previous === 'denied' ? previous : 'ready'));
    };

    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      setStatus((prev) => (prev === 'ready' ? prev : 'locating'));
      void askHost();
      return () => {
        cancelled = true;
      };
    }

    setStatus((prev) => (prev === 'ready' ? prev : 'locating'));

    // The browser gets a bounded window to answer. Electron's provider can sit
    // on a request indefinitely rather than erroring, and a map waiting forever
    // on a fix that is not coming is the same as a map with no fix.
    const fallbackTimer = window.setTimeout(() => {
      if (!cancelled && !deviceFixed.current) void askHost();
    }, 6000);

    watchId = navigator.geolocation.watchPosition(
      (position) => {
        if (cancelled) return;
        deviceFixed.current = true;
        const next: OperatorLocation = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracyM: position.coords.accuracy,
          at: position.timestamp,
          source: 'device',
        };
        setLocation(next);
        setStatus('ready');
        writeLocalCache(next);

        // Kept on the host too, where the telemetry bridge reads it as the
        // base. Throttled: a watch fires far more often than the station moves.
        const previous = lastPersisted.current;
        const moved = previous
          ? metresBetween(next.latitude, next.longitude, previous.lat, previous.lng)
          : Infinity;
        const aged = previous ? Date.now() - previous.at : Infinity;
        if (bridge && (moved > PERSIST_MIN_MOVE_M || aged > PERSIST_MIN_AGE_MS)) {
          lastPersisted.current = { lat: next.latitude, lng: next.longitude, at: Date.now() };
          void bridge
            .saveOperatorLocation({
              latitude: next.latitude,
              longitude: next.longitude,
              accuracyM: next.accuracyM,
            })
            .catch(() => undefined);
        }
      },
      (error) => {
        if (cancelled) return;
        if (error.code === error.PERMISSION_DENIED) {
          setStatus((previous) => (previous === 'ready' ? previous : 'denied'));
        }
        void askHost();
      },
      {
        // Coarse is the right trade here: the station does not move during a
        // flight, and asking for a high-accuracy fix indoors spends the radio
        // on a number no better than the one Wi-Fi positioning already gave.
        enableHighAccuracy: false,
        timeout: 15000,
        maximumAge: 60000,
      }
    );

    return () => {
      cancelled = true;
      window.clearTimeout(fallbackTimer);
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    };
  }, [enabled, bridge]);

  return { location, status };
}
