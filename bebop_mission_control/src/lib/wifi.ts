import type { WifiNetwork } from '../types/bmg';

/**
 * The Bebop's own access point: `Bebop-`, `Bebop2-`, `Bebop_` or `Bebop2_`
 * followed by the airframe suffix. The same pattern `electron/main.cjs` tags
 * networks with (`BEBOP_SSID_RE`); a test pins the two together. Not
 * `BebopDrone-*`, which is what the driver documentation suggests and not
 * what the aircraft broadcasts.
 */
export const BEBOP_SSID_RE = /^Bebop2?[-_]/i;

export function isBebopSsid(ssid: string): boolean {
  return BEBOP_SSID_RE.test(ssid);
}

/**
 * The networks the drone connection sheet may offer.
 *
 * Decided on the name, not on `isBebop`, so the sheet holds the pattern even
 * against a mis-tagged scan. The one exception is the active link main.cjs
 * synthesizes when the station has a route to the aircraft but nmcli cannot
 * name the network: that entry is the drone connection itself, not a nearby
 * network, and hiding it would tell the operator no Bebop is in range while
 * connected to one.
 */
export function bebopNetworks(networks: readonly WifiNetwork[]): WifiNetwork[] {
  return networks.filter((network) => isBebopSsid(network.ssid) || (network.active && network.isBebop));
}
