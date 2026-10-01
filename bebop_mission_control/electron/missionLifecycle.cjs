/**
 * Mission process lifecycle primitives shared by abort, end-mission and quit.
 *
 * Kept out of `main.cjs` so the parts that decide whether a landing reaches the
 * aircraft can be exercised without Electron:
 *
 * - `signalOnce`: one SIGINT per mission process, ever. A second SIGINT used to
 *   reach `signals.py` as "the operator insisted" and run `os._exit(130)` in the
 *   middle of the landing burst the first one had started.
 * - `singleFlight`: concurrent IPC calls share the call in flight instead of
 *   each running the abort or end sequence again.
 * - `publishStopThenLand`: the CLI backstop, stop then land, never concurrent,
 *   land always last.
 * - `createBridgeGate`: the resident command bridge is only written to once it
 *   has announced `ready`; stdin written before that sat in a pipe until the
 *   node came up and left 1.3 s late.
 */

/**
 * CLI backstop for the landing. Measured live, `--once` lost one land in three
 * with both `-w 0` and `-w 1`: a cold participant publishes and exits before
 * the driver's subscription has matched it. Ten publications at 20 Hz after a
 * matched subscription keep the process alive long enough to be delivered.
 * `--max-wait-time-secs` bounds the wait for a subscriber, so a driver that is
 * down does not hold the land behind a stop that can never match.
 */
const LAND_PUB =
  'ros2 topic pub -w 1 --max-wait-time-secs 5 -t 10 -r 20 /bebop/land std_msgs/msg/Empty "{}"';
const STOP_PUB =
  'ros2 topic pub -w 1 --max-wait-time-secs 5 -t 10 -r 20 /bebop/cmd_vel geometry_msgs/msg/Twist "{}"';

/** Wait after SIGKILL for a close event before settling regardless. */
const KILL_BACKSTOP_MS = 1500;

const signalled = new WeakMap();

/**
 * Interrupt a mission process once and resolve when it is gone.
 *
 * Repeated calls for the same child return the first call's promise and send
 * nothing: a mission that received SIGINT is already running its landing
 * burst. After `grace` ms without a close event the child is sent SIGKILL,
 * once; the promise then settles on close or after `KILL_BACKSTOP_MS`.
 *
 * @param {import('node:events').EventEmitter & {kill(signal: string): boolean}} child
 * @param {object} options
 * @param {number} options.grace  Milliseconds between SIGINT and SIGKILL.
 * @param {(text: string) => void} [options.log]  Called when escalating.
 * @param {() => void} [options.onSettle]  Called once, when the child is gone.
 * @returns {Promise<boolean>} Always resolves true.
 */
function signalOnce(child, { grace, log, onSettle }) {
  const existing = signalled.get(child);
  if (existing) return existing;

  const promise = new Promise((resolve) => {
    let escalation = null;
    let backstop = null;
    let settled = false;

    const settle = () => {
      if (settled) return;
      settled = true;
      if (escalation !== null) clearTimeout(escalation);
      if (backstop !== null) clearTimeout(backstop);
      if (onSettle) onSettle();
      resolve(true);
    };

    child.once('close', settle);

    try {
      child.kill('SIGINT');
    } catch (_error) {
      settle();
      return;
    }

    escalation = setTimeout(() => {
      escalation = null;
      if (log) log(`[BMG] Missão não respondeu ao SIGINT em ${grace} ms: enviando SIGKILL.\n`);
      try { child.kill('SIGKILL'); } catch (_error) { /* already gone */ }
      backstop = setTimeout(settle, KILL_BACKSTOP_MS);
    }, grace);
  });

  signalled.set(child, promise);
  return promise;
}

/**
 * Wrap an async function so overlapping calls share one execution.
 *
 * @template T
 * @param {(...args: unknown[]) => Promise<T> | T} fn
 * @returns {(...args: unknown[]) => Promise<T>}
 */
function singleFlight(fn) {
  let pending = null;
  return (...args) => {
    if (pending) return pending;
    pending = Promise.resolve()
      .then(() => fn(...args))
      .finally(() => {
        pending = null;
      });
    return pending;
  };
}

/**
 * Publish zero velocity, then land, through the ROS 2 CLI.
 *
 * @param {(command: string, options: object, done: (err: Error | null) => void) => void} execFn
 * @param {NodeJS.ProcessEnv} env
 * @param {number} timeoutMs  Per-command kill timeout.
 * @returns {Promise<{stop: Error | null, land: Error | null}>}
 */
function publishStopThenLand(execFn, env, timeoutMs) {
  const run = (command) =>
    new Promise((resolve) => {
      execFn(command, { env, timeout: timeoutMs }, (err) => resolve(err || null));
    });
  return run(STOP_PUB).then((stop) => run(LAND_PUB).then((land) => ({ stop, land })));
}

/**
 * Track which command-bridge processes have announced `ready`.
 *
 * Keyed on the process handle, so a bridge restarted by the supervisor starts
 * unready regardless of what its predecessor reported.
 */
function createBridgeGate() {
  const ready = new WeakSet();
  return {
    markReady(child) {
      if (child) ready.add(child);
    },
    isReady(child) {
      return Boolean(child) && ready.has(child);
    },
  };
}

/**
 * Resolve once `predicate` holds or `timeoutMs` has passed.
 *
 * @param {() => boolean} predicate
 * @param {number} timeoutMs
 * @param {number} pollMs
 * @returns {Promise<boolean>} Whether the predicate held.
 */
function waitFor(predicate, timeoutMs, pollMs) {
  return new Promise((resolve) => {
    if (predicate()) {
      resolve(true);
      return;
    }
    const started = Date.now();
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer);
        resolve(true);
      } else if (Date.now() - started >= timeoutMs) {
        clearInterval(timer);
        resolve(false);
      }
    }, pollMs);
  });
}

/**
 * The app's teardown, in the order that keeps the aircraft landing.
 *
 * 1. Drain the mission: it holds a live participant and lands on its own
 *    SIGINT, so it is signalled and awaited before anything it depends on
 *    goes away.
 * 2. If the aircraft still reports an airborne flying state, command the
 *    landing through the resident bridge and wait for ground, bounded by
 *    `groundWaitMs`. The bridge and the driver are what carry that landing.
 * 3. Only then stop the bridges and the driver.
 *
 * Runs once for the life of the app; later calls return the first run.
 *
 * @param {object} hooks
 * @param {() => Promise<unknown>} hooks.stopMission
 * @param {() => boolean} hooks.isAirborne
 * @param {() => void} hooks.commandLand
 * @param {() => void} hooks.stopServices
 * @param {number} [hooks.groundWaitMs=10000]
 * @param {number} [hooks.pollMs=200]
 * @returns {() => Promise<{landCommanded: boolean, grounded: boolean}>}
 */
function createShutdownSequence({
  stopMission,
  isAirborne,
  commandLand,
  stopServices,
  groundWaitMs = 10000,
  pollMs = 200,
}) {
  let run = null;
  return () => {
    if (run) return run;
    run = (async () => {
      await stopMission();
      let landCommanded = false;
      let grounded = true;
      if (isAirborne()) {
        landCommanded = true;
        commandLand();
        grounded = await waitFor(() => !isAirborne(), groundWaitMs, pollMs);
      }
      stopServices();
      return { landCommanded, grounded };
    })();
    return run;
  };
}

module.exports = {
  KILL_BACKSTOP_MS,
  LAND_PUB,
  STOP_PUB,
  createBridgeGate,
  createShutdownSequence,
  publishStopThenLand,
  signalOnce,
  singleFlight,
  waitFor,
};
