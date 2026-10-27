/**
 * The fixed geographic anchor of the tactical map: the take-off base (0, 0).
 *
 * Mirrors `config/site-anchor.json`, which stays the source of truth for the
 * host (`electron/main.cjs`, `BMG_BASE_LAT/LNG` for the telemetry bridge);
 * `siteAnchor.test.ts` holds the two equal. Kept as a constant rather than a
 * JSON import so the renderer bundle does not reach outside the Vite root.
 */
export const MAP_ANCHOR = {
  latitude: -23.6488913,
  longitude: -46.7187124,
  name: 'Transamerica Expo Center',
  city: 'São Paulo',
} as const;
