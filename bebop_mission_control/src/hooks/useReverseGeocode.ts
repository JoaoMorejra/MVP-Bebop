import { useEffect, useRef, useState } from 'react';

export interface PlaceName {
  city: string | null;
  road: string | null;
  suburb: string | null;
}

const EMPTY: PlaceName = { city: null, road: null, suburb: null };

/** Below this the aircraft has not moved enough to be on a different street. */
const REFETCH_DISTANCE_M = 40;
/** Nominatim's usage policy asks for at most one request per second. */
const MIN_INTERVAL_MS = 8000;

const METRES_PER_DEGREE = 111139;

function metresBetween(aLat: number, aLng: number, bLat: number, bLng: number): number {
  const dy = (aLat - bLat) * METRES_PER_DEGREE;
  const dx =
    (aLng - bLng) * METRES_PER_DEGREE * Math.cos((aLat * Math.PI) / 180);
  return Math.hypot(dx, dy);
}

/**
 * Street, neighbourhood and city for the aircraft's position.
 *
 * Resolved against OpenStreetMap's Nominatim, which means it only works when
 * the station has a route off the aircraft's own Wi-Fi — the common case is a
 * laptop on both networks. It fails quietly and reports nulls: a forensic tool
 * must never show a street name it is not sure of, so an unresolved lookup
 * reads as unresolved rather than as the last place that did resolve.
 *
 * Throttled by both distance and time. The aircraft flies within tens of metres
 * of its launch point, so the answer almost never changes during one mission.
 */
export function useReverseGeocode(
  latitude: number,
  longitude: number,
  enabled: boolean
): PlaceName {
  const [place, setPlace] = useState<PlaceName>(EMPTY);
  const lastFetch = useRef<{ lat: number; lng: number; at: number } | null>(null);

  useEffect(() => {
    if (!enabled) return;
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
    if (latitude === 0 && longitude === 0) return;

    const previous = lastFetch.current;
    const now = Date.now();
    if (previous) {
      const moved = metresBetween(latitude, longitude, previous.lat, previous.lng);
      if (moved < REFETCH_DISTANCE_M && now - previous.at < 5 * 60_000) return;
      if (now - previous.at < MIN_INTERVAL_MS) return;
    }
    lastFetch.current = { lat: latitude, lng: longitude, at: now };

    const controller = new AbortController();
    const url =
      'https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=17&addressdetails=1' +
      `&lat=${latitude.toFixed(6)}&lon=${longitude.toFixed(6)}`;

    void fetch(url, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: { address?: Record<string, string> } | null) => {
        if (!data?.address) return;
        const a = data.address;
        setPlace({
          city: a.city ?? a.town ?? a.village ?? a.municipality ?? null,
          road: a.road ?? a.pedestrian ?? a.footway ?? null,
          suburb: a.suburb ?? a.neighbourhood ?? a.city_district ?? a.quarter ?? null,
        });
      })
      .catch(() => {
        /* offline, blocked, or rate-limited: the footer shows this as unknown */
      });

    return () => controller.abort();
  }, [latitude, longitude, enabled]);

  return place;
}
