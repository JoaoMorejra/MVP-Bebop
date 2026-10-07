#!/usr/bin/env node
/**
 * Click-to-takeoff floor across consecutive launches, through the real station.
 *
 * Loads `bebop_mission_control/electron/main.cjs` with `electron` stood in for
 * and drives its IPC handlers the way the interface does: save the parameter
 * document, wait for the standby, "Iniciar Missão", "Finalizar Missão". The
 * missions are real `mission.py --no-fly` processes on an isolated ROS domain;
 * no driver is started and the copilot daemon is replaced by an inert process.
 *
 * Three cycles (docs/PROMPT_FIX_LANCAMENTO_CONTAGEM.md, section 5):
 *   1. normal: from a prepared standby;
 *   2. immediately after cycle 1 ends, before the standby is prepared again
 *      (the cold path);
 *   3. after the standby is prepared again (the warm path).
 *
 * For each, the time from the click (`launchAtMs`) to the mission's
 * "Issuing takeoff" line, which must never be shorter than
 * `kinematics.countdown_sec`.
 *
 * Usage:
 *   node scripts/bench_launch_countdown.cjs [--domain 97] [--countdown 10] [--json out.json]
 */

'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

const ROOT = path.resolve(__dirname, '..');
const MAIN = path.join(ROOT, 'bebop_mission_control', 'electron', 'main.cjs');
const SOURCE_CONFIG = path.join(ROOT, 'mvp_mission_bebop', 'mission_config.json');

const arg = (name, fallback) => {
  const index = process.argv.indexOf(name);
  return index >= 0 && index + 1 < process.argv.length ? process.argv[index + 1] : fallback;
};
const DOMAIN = String(arg('--domain', '97'));
const COUNTDOWN_SEC = Number(arg('--countdown', '10'));
const JSON_OUT = arg('--json', null);
const TAKEOFF_TIMEOUT_MS = 90_000;
const STANDBY_TIMEOUT_MS = 120_000;

if (!Number.isFinite(COUNTDOWN_SEC) || COUNTDOWN_SEC < 0) {
  throw new Error(`--countdown must be a non-negative number, got ${arg('--countdown')}`);
}

// The mission writes its parameters back to the store it was given: a copy, so
// the station's own mission_config.json is never touched by a bench run.
const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bmg-launch-bench-'));
const configPath = path.join(workDir, 'mission_config.json');
const doc = JSON.parse(fs.readFileSync(SOURCE_CONFIG, 'utf-8'));
doc.no_fly = true;
doc.kinematics = { ...(doc.kinematics || {}), countdown_sec: COUNTDOWN_SEC };
fs.writeFileSync(configPath, JSON.stringify(doc, null, 2));
process.env.BMG_MISSION_CONFIG = configPath;

/** Every line main.cjs mirrors to the terminal, with its arrival instant. */
const lines = [];
const waiters = [];
const ingest = (chunk) => {
  const at = Date.now();
  for (const text of String(chunk).split('\n')) {
    if (!text) continue;
    lines.push({ at, text });
    for (const waiter of waiters.slice()) {
      if (waiter.match(text)) {
        waiters.splice(waiters.indexOf(waiter), 1);
        waiter.resolve({ at, text });
      }
    }
  }
};
const realStdout = process.stdout.write.bind(process.stdout);
const realStderr = process.stderr.write.bind(process.stderr);
process.stdout.write = (chunk, ...rest) => {
  ingest(chunk);
  return process.env.BENCH_VERBOSE ? realStdout(chunk, ...rest) : true;
};
process.stderr.write = (chunk, ...rest) => {
  ingest(chunk);
  return process.env.BENCH_VERBOSE ? realStderr(chunk, ...rest) : true;
};
const report = (text) => realStdout(`${text}\n`);

function waitFor(match, timeoutMs, label) {
  return new Promise((resolve, reject) => {
    const waiter = { match, resolve };
    waiters.push(waiter);
    setTimeout(() => {
      const index = waiters.indexOf(waiter);
      if (index < 0) return;
      waiters.splice(index, 1);
      reject(new Error(`timed out after ${timeoutMs} ms waiting for ${label}`));
    }, timeoutMs);
  });
}

const children = [];
const realChild = require('node:child_process');
const original = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'electron') {
    return {
      app: { whenReady: () => new Promise(() => undefined), on: () => undefined, getPath: () => workDir, quit: () => undefined },
      BrowserWindow: function BrowserWindow() {},
      dialog: {},
      shell: {},
      ipcMain: { handle: (name, fn) => (handlers[name] = fn), on: () => undefined },
    };
  }
  if (request === 'child_process' && parent && parent.filename === MAIN) {
    return {
      ...realChild,
      spawn: (cmd, args, opts = {}) => {
        // nectar-activate exports ROS_DOMAIN_ID=14, the station's own domain.
        const env = { ...(opts.env || process.env), ROS_DOMAIN_ID: DOMAIN };
        const copilot = args.includes('mvp_mission_bebop.telemetry.announcer');
        const child = copilot
          ? realChild.spawn('cat', [], { ...opts, env })
          : realChild.spawn(cmd, args, { ...opts, env });
        children.push(child);
        return child;
      },
    };
  }
  return original.call(this, request, parent, isMain);
};
const handlers = {};
require(MAIN);

const STANDBY_READY = (text) => text.includes('Missão em espera pronta');
const TAKEOFF = (text) => text.includes('Issuing takeoff');
const EXITED = (text) => text.includes('[BMG] Missão finalizada com código');
const TICK = /\[MILESTONE mission\.countdown\] \{"remaining_sec": ?(\d+)\}/;

async function cycle(label) {
  const from = lines.length;
  const exited = waitFor(EXITED, TAKEOFF_TIMEOUT_MS + 30_000, `${label}: mission exit`);
  exited.catch(() => undefined);
  const takeoff = waitFor(TAKEOFF, TAKEOFF_TIMEOUT_MS, `${label}: takeoff`);
  const clickedAt = Date.now();
  const result = await handlers['bmg:start-mission']({}, {
    noFly: true,
    countdown: COUNTDOWN_SEC,
    launchAtMs: clickedAt,
    paramsJson: JSON.stringify(doc),
  });
  if (!result || !result.success) throw new Error(`${label}: launch refused: ${JSON.stringify(result)}`);
  const lift = await takeoff;
  const window = lines.slice(from).filter((line) => line.at <= lift.at);
  const ticks = window
    .map((line) => ({ at: line.at, match: TICK.exec(line.text) }))
    .filter((entry) => entry.match)
    .map((entry) => ({ at: entry.at, remaining: Number(entry.match[1]) }));
  const path = window.some((line) => line.text.includes('Lançamento pela missão em espera'))
    ? 'standby'
    : window.some((line) => line.text.includes('Lançamento frio'))
      ? 'cold'
      : 'unknown';
  const coldLine = window.find((line) => line.text.includes('Lançamento frio'));
  const missionCold = window.find((line) => line.text.includes('Cold launch'));

  await handlers['bmg:end-mission']({});
  await exited;
  return {
    label,
    path,
    countdown_sec: COUNTDOWN_SEC,
    click_to_first_tick_s: ticks.length ? (ticks[0].at - clickedAt) / 1000 : null,
    first_tick: ticks.length ? ticks[0].remaining : null,
    ticks: ticks.map((tick) => tick.remaining),
    click_to_takeoff_s: (lift.at - clickedAt) / 1000,
    floor_respected: (lift.at - clickedAt) / 1000 >= COUNTDOWN_SEC,
    station_log: coldLine ? coldLine.text.trim() : null,
    mission_log: missionCold ? missionCold.text.trim() : null,
  };
}

async function main() {
  report(`[bench] domain ${DOMAIN}, countdown ${COUNTDOWN_SEC} s, store ${configPath}`);
  const prepared = waitFor(STANDBY_READY, STANDBY_TIMEOUT_MS, 'standby ready');
  await handlers['bmg:save-parameters']({}, doc);
  await prepared;

  const results = [];
  results.push(await cycle('1 normal'));

  // The mission's exit re-prepares a standby at once; launching now, before
  // it is ready, is the cold path.
  results.push(await cycle('2 immediately after (cold)'));

  await waitFor(STANDBY_READY, STANDBY_TIMEOUT_MS, 'standby re-prepared');
  results.push(await cycle('3 after re-preparation (warm)'));

  report('');
  report('| Ciclo | Caminho | Clique -> 1o tick (s) | 1o tick | Clique -> decolagem (s) | >= countdown_sec |');
  report('|---|---|---|---|---|---|');
  for (const row of results) {
    report(
      `| ${row.label} | ${row.path} | ${row.click_to_first_tick_s === null ? '-' : row.click_to_first_tick_s.toFixed(2)} | ` +
        `${row.first_tick ?? '-'} | ${row.click_to_takeoff_s.toFixed(2)} | ${row.floor_respected ? 'sim' : 'NAO'} |`
    );
  }
  for (const row of results) {
    if (row.station_log) report(`[${row.label}] ${row.station_log}`);
    if (row.mission_log) report(`[${row.label}] ${row.mission_log}`);
    report(`[${row.label}] ticks: ${row.ticks.join(' ')}`);
  }
  if (JSON_OUT) fs.writeFileSync(JSON_OUT, JSON.stringify(results, null, 2));
  return results.every((row) => row.floor_respected) ? 0 : 1;
}

function shutdown(code) {
  for (const child of children) {
    try {
      child.kill('SIGTERM');
    } catch (_error) {
      // already gone
    }
  }
  setTimeout(() => {
    for (const child of children) {
      try {
        child.kill('SIGKILL');
      } catch (_error) {
        // already gone
      }
    }
    fs.rmSync(workDir, { recursive: true, force: true });
    process.exit(code);
  }, 3000);
}

main().then(shutdown, (error) => {
  report(`[bench] ${error.message}`);
  shutdown(2);
});
