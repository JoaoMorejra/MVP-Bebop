/**
 * Download of the native 14 MP photos once a mission that touched down exits.
 *
 * The capture asks the aircraft for its own photo and records the
 * acknowledgement in the evidence sidecar (`steps/inspection.py:_capture`); the
 * file stays on the Bebop until `streamer/media_fetch.py` pulls it over FTP.
 * The fetch waits for the mission process to exit so every sidecar is final,
 * and only follows a `mission.touchdown`: the transfer shares the Wi-Fi link
 * with the video and has no business running while the aircraft is airborne.
 */

/** argv, after the activator, of the fetch script for `outputDir`. */
const MEDIA_FETCH_ARGS = (script, outputDir) => ['python3', script, '--output-dir', outputDir];

/**
 * @param {object} options
 * @param {() => Promise<{code: number | null, stdout: string}>} options.run
 *   Runs the fetch to completion.
 * @param {(report: {fetched: object[], error?: string}) => void} options.onResult
 *   Called with every report the fetch printed, including failures it reported.
 * @param {(text: string) => void} options.log
 */
function createNativePhotoFetch({ run, onResult, log }) {
  let touchdownSeen = false;
  let inFlight = null;

  const observe = (message) => {
    if (message && message.kind === 'milestone' && message.key === 'mission.touchdown') {
      touchdownSeen = true;
    }
  };

  const fetchOnce = async () => {
    let result;
    try {
      result = await run();
    } catch (error) {
      log(`[BMG] Busca das fotos nativas não iniciou: ${error.message}\n`);
      return;
    }
    let report = null;
    try {
      const lines = String(result.stdout || '').trim().split('\n');
      const parsed = JSON.parse(lines[lines.length - 1]);
      if (parsed && Array.isArray(parsed.fetched)) report = parsed;
    } catch (_error) {
      report = null;
    }
    if (!report) {
      log(`[BMG] Busca das fotos nativas sem relatório legível (código ${result.code}).\n`);
      return;
    }
    onResult(report);
    log(
      report.error
        ? `[BMG] Fotos nativas não baixadas: ${report.error}\n`
        : `[BMG] Fotos nativas baixadas: ${report.fetched.length}.\n`
    );
  };

  const missionExited = () => {
    if (inFlight) return inFlight;
    if (!touchdownSeen) return Promise.resolve();
    touchdownSeen = false;
    inFlight = fetchOnce().finally(() => {
      inFlight = null;
    });
    return inFlight;
  };

  return { observe, missionExited };
}

module.exports = { MEDIA_FETCH_ARGS, createNativePhotoFetch };
