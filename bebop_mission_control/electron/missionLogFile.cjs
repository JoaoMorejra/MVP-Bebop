/**
 * The stdout and stderr of each `mission.py`, kept on disk.
 *
 * Until now a mission's log lived only in memory (`recordLog`, 4000 lines) and
 * on the terminal that launched the station: once the window closed, the lines
 * that classify the firmware's answer to the pad edge (`[ALT_PROBE]`,
 * `[VERT_KF]`, `[PLATFORM ...]`, `[TIMING]`) were gone. Field identification
 * F-ID compares two flights by those lines, so each launch now appends them to
 * `<dir>/mission_<YYYYMMDD_HHMMSS>.log`, and only the 30 most recent files are
 * kept. Writes go through `fs.createWriteStream`, so the main loop never blocks
 * on the disk; a disk that refuses costs the file, never the flight.
 */
const fs = require('node:fs');
const path = require('node:path');

/** Files kept in the log directory, newest first. */
const KEEP_MISSION_LOGS = 30;

const LOG_NAME = /^mission_\d{8}_\d{6}(?:_\d+)?\.log$/;

/** `mission_<YYYYMMDD_HHMMSS>.log` for the local time of `date`. */
function missionLogName(date) {
  const pad = (value) => String(value).padStart(2, '0');
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_` +
    `${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  return `mission_${stamp}.log`;
}

/**
 * Delete all but the `keep` newest mission logs of `dir` (by name, which sorts by time).
 *
 * @returns {string[]} The names removed.
 */
function rotateMissionLogs(dir, keep = KEEP_MISSION_LOGS, fsImpl = fs) {
  let names;
  try {
    names = fsImpl.readdirSync(dir).filter((name) => LOG_NAME.test(name)).sort();
  } catch (_error) {
    return [];
  }
  const removed = names.slice(0, Math.max(0, names.length - keep));
  for (const name of removed) {
    try {
      fsImpl.unlinkSync(path.join(dir, name));
    } catch (_error) {
      // an undeletable file is retried at the next launch
    }
  }
  return removed;
}

/**
 * @param {object} options
 * @param {() => string} options.directory  Where the files go (`app.getPath('userData')/logs`).
 * @param {() => Date} [options.now]
 * @param {typeof fs} [options.fsImpl]
 * @param {(text: string) => void} [options.warn]
 */
function createMissionLogFile({ directory, now = () => new Date(), fsImpl = fs, warn = () => undefined }) {
  let stream = null;
  let file = null;

  const close = () => {
    if (stream) {
      try {
        stream.end();
      } catch (_error) {
        // already closed
      }
    }
    stream = null;
  };

  return {
    /** Open the file of a new launch, rotating the old ones; closes any file still open. */
    open() {
      close();
      try {
        const dir = directory();
        fsImpl.mkdirSync(dir, { recursive: true });
        const base = missionLogName(now()).slice(0, -'.log'.length);
        let name = `${base}.log`;
        for (let n = 1; fsImpl.existsSync(path.join(dir, name)); n += 1) name = `${base}_${n}.log`;
        file = path.join(dir, name);
        // Opened here, synchronously, so the file exists before the next launch
        // looks for a free name; the stream then writes through the descriptor.
        const fd = fsImpl.openSync(file, 'a');
        stream = fsImpl.createWriteStream(file, { fd });
        stream.on?.('error', (error) => {
          warn(`[BMG] Log de missão desativado: ${error.message}\n`);
          stream = null;
        });
        rotateMissionLogs(dir, KEEP_MISSION_LOGS, fsImpl);
      } catch (error) {
        warn(`[BMG] Log de missão não aberto: ${error.message}\n`);
        stream = null;
        file = null;
      }
      return file;
    },
    /** Append one chunk of the mission's output, tagged with its stream. */
    write(type, text) {
      if (!stream) return;
      const body = String(text ?? '');
      stream.write(type === 'stderr' ? body.replace(/^(?=.)/gm, '[stderr] ') : body);
    },
    close,
    path: () => file,
  };
}

module.exports = { KEEP_MISSION_LOGS, createMissionLogFile, missionLogName, rotateMissionLogs };
