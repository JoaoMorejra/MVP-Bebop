const fs = require('fs');

/**
 * The demonstration site, when one is configured (`config/site-anchor.json`).
 *
 * The station's automatic positions are all coarse: Chromium's geolocation
 * has no provider in a stock Electron build, and the host's fallback is the
 * city of its public address, kilometres off. Without an aircraft GPS fix the
 * map needs a known point, and at a planned venue that point is known in
 * advance. When enabled it is the anchor ahead of every automatic source; only
 * the aircraft's own GPS outranks it.
 */
function readSiteAnchorFile(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf-8'));
    if (data.enabled !== true) return null;
    const latitude = Number(data.latitude);
    const longitude = Number(data.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
    if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
    return {
      latitude,
      longitude,
      accuracyM: Number(data.accuracyM) > 0 ? Number(data.accuracyM) : 25,
      name: typeof data.name === 'string' && data.name.trim() ? data.name.trim() : 'local configurado',
    };
  } catch {
    return null;
  }
}

module.exports = { readSiteAnchorFile };
