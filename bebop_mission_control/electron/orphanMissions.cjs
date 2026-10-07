/**
 * Mission processes this station's own bookkeeping has lost track of.
 *
 * Children are not killed when their parent dies on Linux: a GCS that
 * crashed, was SIGKILLed, or was closed from outside Electron's own quit path
 * leaves whatever `mission.py` it had running behind, re-parented to init.
 * `missionStandby.dispose()` and the mission's own signal handlers only run on
 * a clean quit, so this is the recovery for every other way the station can
 * have gone down.
 *
 * Two classes of orphan need two different answers:
 *
 * - A flight script (no `--standby` on its command line) may be mid-air. It is
 *   only ever asked to land (`SIGINT`, which its handler turns into a landing
 *   burst), never forced: a `SIGKILL` would leave the aircraft holding its
 *   last setpoint with nobody on the ground commanding it.
 * - A standby (`--standby`) is waiting on stdin for a `go`
 *   (`mission.py:_await_go`), which runs before the mission installs its
 *   flight signal handlers (`runner.install_signal_handlers`) or reaches any
 *   step that could command the aircraft. It cannot be mid-air. Once asked to
 *   stop and given a grace period to unwind its ROS node and worker threads,
 *   forcing it is exactly as safe as forcing any other leftover subprocess --
 *   so one that outlives the grace period is finished outright instead of
 *   left to block every later launch until an operator kills it by hand.
 */

const STANDBY_FLAG = '--standby';

/**
 * Orphan candidates from one `pgrep -af <pattern>` call, classified.
 *
 * @param {string} pgrepOutput  Raw stdout: one `pid cmdline` line per match.
 * @param {object} options
 * @param {number | null} options.ownPid  This process's own pid, excluded.
 * @param {number | null} options.missionPid  The mission this station holds a
 *   handle to right now, excluded: it is not an orphan.
 * @param {(pid: number) => boolean} options.isTracked  Whether `pid` is this
 *   station's own prepared standby (or its activator parent) -- also not an
 *   orphan.
 * @returns {Array<{pid: number, standby: boolean}>}
 */
function parseOrphanCandidates(pgrepOutput, { ownPid, missionPid, isTracked }) {
  return pgrepOutput
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const sep = line.indexOf(' ');
      const pid = Number(sep === -1 ? line : line.slice(0, sep));
      const cmdline = sep === -1 ? '' : line.slice(sep + 1);
      return { pid, cmdline };
    })
    .filter(({ pid }) => Number.isInteger(pid) && pid > 0 && pid !== ownPid && pid !== missionPid)
    .filter(({ pid }) => !isTracked(pid))
    .map(({ pid, cmdline }) => ({ pid, standby: cmdline.includes(STANDBY_FLAG) }));
}

/**
 * Track orphans across sweeps, asking once and forcing a standby that does
 * not answer in time.
 *
 * @param {object} options
 * @param {() => Array<{pid: number, standby: boolean}>} options.listCandidates
 * @param {(pid: number, sig: string) => void} options.signal  `process.kill`.
 * @param {(text: string) => void} options.log
 * @param {number} [options.graceMs]  How long a standby is given to exit on
 *   its own `SIGINT` before it is forced. Measured: a standby's own ROS node
 *   and worker threads took several seconds to unwind after the signal.
 * @param {() => number} [options.now]
 */
function createOrphanReaper({ listCandidates, signal, log, graceMs = 4000, now = Date.now }) {
  /** pid -> the instant this reaper first signalled it. */
  const signalledAt = new Map();

  /**
   * One pass: signal anything new, force a standby that missed its grace
   * period, and forget anything that is no longer there.
   *
   * @returns {{blocking: number[]}} Pids still alive this reaper could not
   *   safely clear -- a flight script, or a standby still inside its grace
   *   period. A launch should refuse only on these.
   */
  function sweep() {
    const candidates = listCandidates();
    const present = new Set(candidates.map((c) => c.pid));
    for (const pid of signalledAt.keys()) {
      if (!present.has(pid)) signalledAt.delete(pid);
    }

    const blocking = [];
    for (const { pid, standby } of candidates) {
      const askedAt = signalledAt.get(pid);
      if (askedAt === undefined) {
        signalledAt.set(pid, now());
        try {
          signal(pid, 'SIGINT');
          log(`[BMG] Missão órfã (pid ${pid}) interrompida: pouso comandado.\n`);
        } catch (_error) {
          // already gone
        }
        blocking.push(pid);
        continue;
      }

      if (standby && now() - askedAt >= graceMs) {
        try {
          signal(pid, 'SIGKILL');
          log(
            `[BMG] Espera órfã (pid ${pid}) não encerrou em ${Math.round(graceMs / 1000)} s ` +
              'após o pedido de parada; finalizada (uma espera nunca decola).\n'
          );
        } catch (_error) {
          // already gone
        }
        signalledAt.delete(pid);
        continue;
      }

      blocking.push(pid);
    }
    return { blocking };
  }

  return { sweep };
}

module.exports = { STANDBY_FLAG, parseOrphanCandidates, createOrphanReaper };
