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

const { EXIT_LAUNCH_REFUSED } = require('./launchRefusal.cjs');

/** Printed by `mission.py --standby` once prepared (`engine/launch.py`). */
const STANDBY_READY_LINE = '[STANDBY] ready';

/**
 * Fields `mission.py` reads while preparing (`engine/launch.py:CRITICAL_PATHS`).
 * The last three are the profile, the return plan and the launch surface: a
 * standby prepared for another one never receives the go.
 */
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
  'mission_mode',
  'rtl.enabled',
  'platform.takeoff_from_ground',
];

const getPath = (doc, path) =>
  path.split('.').reduce((node, key) => (node && typeof node === 'object' ? node[key] : undefined), doc);

/**
 * What a document that leaves a field out flies with: the station sends the
 * mode explicitly, a document read from an older store may not.
 */
const ABSENT_DEFAULTS = { mission_mode: 'advanced', 'rtl.enabled': true, 'platform.takeoff_from_ground': false };

const normalize = (path, value) => {
  if ((value === undefined || value === null) && path in ABSENT_DEFAULTS) return ABSENT_DEFAULTS[path];
  if (value === undefined || value === null) return null;
  if (path === 'vision.inference_device') return String(value).trim().toUpperCase();
  if (path === 'vision.inference_imgsz') return Number(value);
  if (path === 'mission_mode') return String(value).trim().toLowerCase() || ABSENT_DEFAULTS.mission_mode;
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
 * @param {number} [options.releaseGraceMs]  How long a prepared standby asked to
 *   stop (SIGTERM) is given before it is forced.
 *
 * A standby that exits with `EXIT_LAUNCH_REFUSED` refused the stored document
 * itself; preparing it again every `retryMs` would only be refused again. The
 * manager holds it as refused and prepares nothing until `ensure` receives a
 * different document, which is what the save of the parameter sheet does
 * (`refreshStandby` after `bmg:save-parameters`).
 */
function createStandbyManager({ spawnStandby, log, retryMs = 5000, releaseGraceMs = 2500 }) {
  let current = null; // { child, key, doc, driverRunning, ready, buffer }
  // Released standbys until their process is gone, by pid. Dropping them from
  // the bookkeeping at the SIGTERM made each one, for the seconds it took to
  // exit, an untracked `mission.py` that the launch's orphan check refused on:
  // "Missão órfã ainda em execução" on the very click that recycled it.
  const draining = new Map();
  let wanted = null; // { doc, driverRunning }
  let retryTimer = null;
  let disposed = false;
  let refusedDoc = null; // JSON of the document the last standby refused

  const clearRetry = () => {
    if (retryTimer) clearTimeout(retryTimer);
    retryTimer = null;
  };

  const release = (entry, reason) => {
    if (!entry) return;
    entry.released = true;
    log(`[BMG] Missão em espera reciclada (${reason}).\n`);
    if (entry.child.exitCode != null || entry.child.signalCode != null) return;
    draining.set(entry.child.pid, entry);
    // A standby still preparing has not installed its signal handlers: SIGTERM
    // then lands in rclpy, which invalidates the context and leaves the process
    // running into errors. It has commanded nothing and never will, so it is
    // finished outright; a prepared one is asked first and forced after the grace.
    const signalName = entry.ready ? 'SIGTERM' : 'SIGKILL';
    try {
      entry.child.kill(signalName);
    } catch (_error) {
      // already gone
    }
    if (entry.ready) {
      entry.forceTimer = setTimeout(() => {
        try {
          entry.child.kill('SIGKILL');
        } catch (_error) {
          // already gone
        }
      }, releaseGraceMs);
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
      startedAt: Date.now(),
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
      if (draining.get(child.pid) === entry) draining.delete(child.pid);
      if (entry.forceTimer) clearTimeout(entry.forceTimer);
      if (entry.taken || entry.released || disposed) return;
      if (code === EXIT_LAUNCH_REFUSED) {
        refusedDoc = JSON.stringify(entry.doc);
        clearRetry();
        log('[BMG] Missão em espera recusou o documento salvo; nenhuma nova preparação até os parâmetros mudarem.\n');
        return;
      }
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
    if (refusedDoc !== null) {
      if (JSON.stringify(doc) === refusedDoc) return;
      refusedDoc = null;
    }
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
    if (!current && refusedDoc !== null) return 'missão em espera recusou o documento salvo';
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

  const isStandby = (pid) => Boolean((current && current.child.pid === pid) || draining.has(pid));

  /**
   * Re-prepare the standby from scratch for the same document and driver state.
   *
   * A Fast DDS participant created before the host joined the aircraft's
   * network does not discover the driver over the new interface, so a standby
   * prepared earlier would receive its go blind and exit stale. The link
   * bring-up rebuilds the bridges for that reason and recycles this once the
   * link is proven, sparing a standby already prepared after `startedBefore`.
   */
  const recycle = (reason, startedBefore = Infinity) => {
    if (disposed || !current || current.startedAt >= startedBefore) return;
    const stale = current;
    current = null;
    release(stale, reason);
    start(stale.doc, stale.driverRunning);
  };

  /** Whether the last standby refused its document and none is prepared until it changes. */
  const isRefused = () => refusedDoc !== null;

  const dispose = () => {
    disposed = true;
    clearRetry();
    const entry = current;
    current = null;
    if (entry) release(entry, 'estação encerrando');
  };

  return { ensure, take, unavailableReason, pause, recycle, isStandby, isRefused, dispose };
}

module.exports = { CRITICAL_PATHS, STANDBY_READY_LINE, createStandbyManager, standbyKey };
