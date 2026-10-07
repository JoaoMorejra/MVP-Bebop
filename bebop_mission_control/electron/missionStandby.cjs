/**
 * The prepared mission the station keeps ready for the next launch.
 *
 * Measured: 40 s from the click to the first countdown tick, the mission's
 * start-up (SDK, driver link, camera, the detector on the MX110) running after
 * the click under the load of the driver, the video bridges and the speech
 * cache. `mission.py --standby` does all of it beforehand and waits on stdin;
 * the click hands it the parameter document and the click instant
 * (`mvp_mission_bebop/engine/launch.py`), and the takeoff is at that instant
 * plus the countdown.
 *
 * A standby is prepared for the fields it read while starting (the arming mode,
 * network, topics, detector, loop rate) and for whether the driver was up; a
 * launch or a change that differs in any of them recycles it. A standby
 * commands nothing before its go.
 */

/** Printed by `mission.py --standby` once prepared (`engine/launch.py`). */
const STANDBY_READY_LINE = '[STANDBY] ready';

/** Fields `mission.py` reads while preparing (`engine/launch.py:CRITICAL_PATHS`). */
const CRITICAL_PATHS = [
  'no_fly',
  'network.drone_ip',
  'network.namespace',
  'network.camera_raw_topic',
  'network.odometry_topic',
  'vision.model_path',
  'vision.inference_device',
  'vision.inference_imgsz',
  'kinematics.control_loop_hz',
  'dead_reckoning.enabled',
];

const getPath = (doc, path) =>
  path.split('.').reduce((node, key) => (node && typeof node === 'object' ? node[key] : undefined), doc);

const normalize = (path, value) => {
  if (value === undefined || value === null) return null;
  if (path === 'vision.inference_device') return String(value).trim().toUpperCase();
  if (path === 'vision.inference_imgsz') return Number(value);
  return value;
};

/**
 * What a standby is prepared for: the start-up fields of `doc` and the driver.
 *
 * @param {object} doc  Parameter document.
 * @param {boolean} driverRunning
 */
function standbyKey(doc, driverRunning) {
  return JSON.stringify([
    Boolean(driverRunning),
    ...CRITICAL_PATHS.map((path) => normalize(path, getPath(doc, path))),
  ]);
}

/**
 * @param {object} options
 * @param {(doc: object) => import('node:child_process').ChildProcess} options.spawnStandby
 *   Starts `mission.py --standby` for `doc`.
 * @param {(text: string) => void} options.log
 * @param {number} [options.retryMs]  Pause before re-preparing after a standby exited.
 */
function createStandbyManager({ spawnStandby, log, retryMs = 5000 }) {
  let current = null; // { child, key, doc, driverRunning, ready, buffer }
  let wanted = null; // { doc, driverRunning }
  let retryTimer = null;
  let disposed = false;

  const clearRetry = () => {
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
  };

  const release = (entry, reason) => {
    if (!entry) return;
    entry.released = true;
    log(`[BMG] Missão em espera reciclada (${reason}).\n`);
    try {
      entry.child.kill('SIGTERM');
    } catch (_error) {
      // already gone
    }
  };

  const start = (doc, driverRunning) => {
    clearRetry();
    let child;
    try {
      child = spawnStandby(doc);
    } catch (error) {
      log(`[BMG] Missão em espera não iniciou: ${error.message}\n`);
      return;
    }
    const entry = {
      child,
      key: standbyKey(doc, driverRunning),
      doc,
      driverRunning: Boolean(driverRunning),
      ready: false,
      buffer: '',
      released: false,
    };
    current = entry;
    const onData = (chunk) => {
      entry.buffer += String(chunk);
      let index;
      while ((index = entry.buffer.indexOf('\n')) >= 0) {
        const line = entry.buffer.slice(0, index);
        entry.buffer = entry.buffer.slice(index + 1);
        if (line.includes(STANDBY_READY_LINE) && !entry.ready) {
          entry.ready = true;
          log('[BMG] Missão em espera pronta para lançamento instantâneo.\n');
        }
      }
    };
    entry.onData = onData;
    child.stdout.setEncoding?.('utf-8');
    child.stdout.on('data', onData);
    child.on('close', (code) => {
      if (current === entry) current = null;
      if (entry.taken || entry.released || disposed) return;
      log(`[BMG] Missão em espera saiu (código ${code}); nova preparação em ${Math.round(retryMs / 1000)} s.\n`);
      clearRetry();
      retryTimer = setTimeout(() => {
        retryTimer = null;
        if (wanted && !current && !disposed) start(wanted.doc, wanted.driverRunning);
      }, retryMs);
    });
  };

  /** Keep a standby prepared for `doc` and the driver state; recycle one prepared for others. */
  const ensure = (doc, driverRunning) => {
    if (disposed || !doc || typeof doc !== 'object') return;
    wanted = { doc, driverRunning: Boolean(driverRunning) };
    const key = standbyKey(doc, driverRunning);
    if (current && current.key === key) return;
    if (current) {
      const stale = current;
      current = null;
      release(stale, 'parâmetros de partida ou driver mudaram');
    }
    start(doc, driverRunning);
  };

  /**
   * The prepared mission for this launch, given its go; null when there is
   * none ready for it (the caller spawns one, and a standby is re-prepared).
   */
  const take = (doc, driverRunning, launchAtMs) => {
    const key = standbyKey(doc, driverRunning);
    if (!current || !current.ready || current.key !== key) return null;
    const entry = current;
    current = null;
    entry.taken = true;
    wanted = null;
    entry.child.stdout.removeListener('data', entry.onData);
    entry.child.stdin.write(`${JSON.stringify({ op: 'go', params: doc, launch_at_ms: launchAtMs })}\n`);
    return entry.child;
  };

  /**
   * Why `take` gives nothing for this launch, in the operator's words; null
   * when it would hand over the prepared mission.
   */
  const unavailableReason = (doc, driverRunning) => {
    if (!current) return 'nenhuma missão em espera';
    if (!current.ready) return 'missão em espera ainda em preparo';
    if (current.key === standbyKey(doc, driverRunning)) return null;
    const differing = CRITICAL_PATHS.filter(
      (path) => JSON.stringify(normalize(path, getPath(doc, path))) !== JSON.stringify(normalize(path, getPath(current.doc, path)))
    );
    if (differing.length) return `missão em espera preparada para outro ${differing.join(', ')}`;
    return 'missão em espera preparada com o driver em outro estado';
  };

  /**
   * Stop the standby and prepare none until the next `ensure`: one mission
   * process at a time holds the camera and the MX110's memory.
   */
  const pause = () => {
    clearRetry();
    wanted = null;
    const entry = current;
    current = null;
    if (entry) release(entry, 'missão lançada sem espera');
  };

  const isStandby = (pid) => Boolean(current && current.child.pid === pid);

  const dispose = () => {
    disposed = true;
    clearRetry();
    const entry = current;
    current = null;
    if (entry) release(entry, 'estação encerrando');
  };

  return { ensure, take, unavailableReason, pause, isStandby, dispose };
}

module.exports = { CRITICAL_PATHS, STANDBY_READY_LINE, createStandbyManager, standbyKey };
