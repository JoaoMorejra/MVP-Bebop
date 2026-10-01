/**
 * ROS 2 housekeeping done by the station (7.2, 7.3).
 *
 * - `restartRos2Daemon`: the `ros2` daemon caches the graph it discovered when
 *   it started. Reproduced live, a daemon older than the driver answered
 *   `ros2 node list` with nothing, and the Nectar SDK's `connect()` reads
 *   exactly that. Restarted whenever the station brings the driver up and
 *   whenever the Wi-Fi network changes.
 * - `cleanOrphanShm`: measured, 83 Fast DDS `fastrtps_*` segments in /dev/shm,
 *   61 still there with every process closed. `fastdds shm clean` runs at
 *   station start-up while no station ROS process is alive. The ros2 daemon
 *   does not count: the command only removes segments whose owner has died.
 */
const fs = require('node:fs');

/** Command lines of the station's ROS processes (driver, mission, bridges). */
const ROS_PROCESS_PATTERN =
  /(bebop_driver|mvp_mission_bebop[/.]mission|mission\.py|mjpeg_server\.py|telemetry_bridge\.py|command_bridge\.py|mvp_mission_bebop\.telemetry\.announcer)/;

/**
 * @param {(cmd: string, args: string[], opts?: object) => Promise<{ok: boolean}>} run
 * @returns {Promise<boolean>} Whether `ros2 daemon start` succeeded.
 */
async function restartRos2Daemon(run) {
  await run('ros2', ['daemon', 'stop'], { timeout: 10000 });
  const started = await run('ros2', ['daemon', 'start'], { timeout: 10000 });
  return Boolean(started && started.ok);
}

/** Fast DDS shared-memory segments currently in /dev/shm. */
function countFastddsSegments() {
  try {
    return fs.readdirSync('/dev/shm').filter((name) => name.startsWith('fastrtps_')).length;
  } catch (_error) {
    return 0;
  }
}

/** PIDs of live station ROS processes, from /proc. */
function stationRosProcesses() {
  const found = [];
  let entries = [];
  try {
    entries = fs.readdirSync('/proc');
  } catch (_error) {
    return found;
  }
  for (const entry of entries) {
    const pid = Number(entry);
    if (!Number.isInteger(pid) || pid === process.pid) continue;
    let command = '';
    try {
      command = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf-8').replace(/\0/g, ' ');
    } catch (_error) {
      continue;
    }
    if (ROS_PROCESS_PATTERN.test(command)) found.push(pid);
  }
  return found;
}

/**
 * Remove orphaned Fast DDS segments when no station ROS process is alive.
 *
 * @param {object} deps
 * @param {() => number[]} [deps.rosProcesses]
 * @param {() => number} [deps.countSegments]
 * @param {(cmd: string, args: string[], opts?: object) => Promise<{ok: boolean}>} deps.run
 */
async function cleanOrphanShm({ rosProcesses = stationRosProcesses, countSegments = countFastddsSegments, run }) {
  const alive = rosProcesses();
  if (alive.length) return { skipped: true, alive };
  const before = countSegments();
  await run('fastdds', ['shm', 'clean'], { timeout: 15000 });
  const after = countSegments();
  return { skipped: false, before, after, removed: Math.max(0, before - after) };
}

/**
 * A detector of Wi-Fi network changes: true when `ssid` differs from the last
 * non-blank one seen. The first network and blanks (a scan in between) are not
 * changes.
 */
function createSsidChangeDetector() {
  let last = null;
  return (ssid) => {
    const current = String(ssid ?? '').trim();
    if (!current) return false;
    const changed = last !== null && current !== last;
    last = current;
    return changed;
  };
}

module.exports = {
  ROS_PROCESS_PATTERN,
  cleanOrphanShm,
  countFastddsSegments,
  createSsidChangeDetector,
  restartRos2Daemon,
  stationRosProcesses,
};
