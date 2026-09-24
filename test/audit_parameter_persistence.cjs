// Runtime audit of mission parameter persistence between the GCS and mission.py.
//
// Loads the real electron/main.cjs with `electron` and `child_process.spawn`
// mocked, drives the real bmg:get-parameters / bmg:save-parameters /
// bmg:start-mission handlers, and replays the exact argv the launch produced
// through mission.py until it reports its merged parameters.
//
//   1. edit -> save -> reopen: the saved values are what the next session and
//      the Python loader read; fields the UI does not expose round-trip intact.
//   2. edit -> launch without saving: the working document, not the stale
//      store, is what reaches the controller (--height and --params-json), and
//      mission.py persists the flown values but never the arming state.
//
// Operates on the real mission_config.json (main.cjs hardcodes its path), so
// the file is backed up first and restored on every exit path. Run manually,
// with no BMG instance and no mission running:
//
//   node test/audit_parameter_persistence.cjs
const Module = require('module');
const path = require('path');
const fs = require('fs');
const { EventEmitter } = require('events');
const realChild = require('child_process');

const REPO = path.resolve(__dirname, '..');
const MAIN = path.join(REPO, 'bebop_mission_control/electron/main.cjs');
const CONFIG = path.join(REPO, 'mvp_mission_bebop/mission_config.json');
const BACKUP = path.join(require('os').tmpdir(), `bmg-params-audit-${process.pid}.json`);
const ACTIVATOR = process.env.NECTAR_ACTIVATE || path.resolve(REPO, '../../bin/nectar-activate');

let handlers = {};
const spawns = [];

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = Object.assign(new EventEmitter(), { setEncoding: () => {} });
  child.stderr = Object.assign(new EventEmitter(), { setEncoding: () => {} });
  child.stdin = Object.assign(new EventEmitter(), { write: () => true, end: () => {}, setEncoding: () => {} });
  child.kill = () => true;
  child.pid = 424242;
  return child;
}

const electronMock = {
  app: { whenReady: () => new Promise(() => {}), on: () => {}, getPath: () => '/tmp', quit: () => {} },
  BrowserWindow: function () {},
  dialog: {},
  shell: {},
  ipcMain: { handle: (name, fn) => { handlers[name] = fn; }, on: () => {} },
};
const childMock = {
  ...realChild,
  spawn: (cmd, args, opts) => { spawns.push({ cmd, args, opts }); return fakeChild(); },
};

const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === 'electron') return electronMock;
  if (request === 'child_process' && parent && parent.filename === MAIN) return childMock;
  return originalLoad.apply(this, arguments);
};

/** A fresh require of main.cjs stands in for quitting and reopening the app. */
function bootApp() {
  delete require.cache[require.resolve(MAIN)];
  handlers = {};
  require(MAIN);
  return handlers;
}

const getPath = (doc, p) => p.split('.').reduce((node, key) => (node == null ? undefined : node[key]), doc);
const num = (doc, p, fallback) => { const v = Number(getPath(doc, p)); return Number.isFinite(v) ? v : fallback; };

/** Verbatim copy of the options object App.tsx:launch() builds. */
function launchOptions(doc) {
  return {
    countdown: num(doc, 'kinematics.countdown_sec', 10),
    noFly: Boolean(getPath(doc, 'no_fly')),
    height: num(doc, 'kinematics.target_altitude_m', 1),
    velocity: num(doc, 'kinematics.forward_cruise_velocity', 0.2),
    rtlVelocity: num(doc, 'rtl.max_speed', 0.1),
    searchTimeout: num(doc, 'timeouts.search_timeout_sec', 30),
    hoverDuration: num(doc, 'kinematics.hover_duration_sec', 7),
    confidence: num(doc, 'vision.confidence_threshold', 0.5),
    arrivalRadius: num(doc, 'rtl.arrival_radius_m', 0.2),
    modelPath: String(getPath(doc, 'vision.model_path') ?? 'yolov8n.pt'),
    ip: String(getPath(doc, 'network.drone_ip') ?? '192.168.42.1'),
    detectionTopic: String(getPath(doc, 'network.detection_stream_topic') ?? '/bebop/camera/detections'),
    paramsJson: JSON.stringify(doc ?? {}),
  };
}

/** Run the captured mission argv until mission.py reports its merged parameters. */
function replayMission(spawnCall) {
  return new Promise((resolve, reject) => {
    const child = realChild.spawn(spawnCall.cmd, spawnCall.args, { cwd: spawnCall.opts.cwd, env: spawnCall.opts.env });
    let out = '';
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('no Active parameters line:\n' + out.slice(-3000))); }, 120000);
    const onData = (data) => {
      out += data.toString();
      const line = out.split('\n').find((l) => l.includes('Active parameters:'));
      if (line) {
        clearTimeout(timer);
        child.stdout.off('data', onData);
        // nectar-activate execs, so this signal reaches mission.py itself. The
        // audit waits for the exit so no bench mission outlives it.
        const reaper = setTimeout(() => child.kill('SIGKILL'), 3000);
        child.once('exit', () => { clearTimeout(reaper); resolve(line); });
        child.kill('SIGINT');
      }
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', (d) => { out += d.toString(); });
  });
}

function pythonView() {
  const code = 'import json,sys; from mvp_mission_bebop.parameters import MissionParameters as P; ' +
    'p=P.load_from_file(sys.argv[1]); print(json.dumps({"alt":p.kinematics.target_altitude_m,"hover":p.kinematics.hover_duration_sec,"conf":p.vision.confidence_threshold,"no_fly":p.no_fly}))';
  return JSON.parse(realChild.execFileSync(ACTIVATOR, ['python3', '-c', code, CONFIG], { cwd: REPO }).toString().trim().split('\n').pop());
}

const results = [];
const check = (name, ok, detail) => { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ${detail}`); };

(async () => {
  if (!fs.existsSync(CONFIG)) throw new Error(`no store at ${CONFIG}`);
  fs.copyFileSync(CONFIG, BACKUP);
  try {
    // ---- Test 1: edit -> save -> reopen -------------------------------------
    let ipc = bootApp();
    const loaded = await ipc['bmg:get-parameters']();
    check('get-parameters reads the store', loaded.success === true, `alt=${getPath(loaded.params, 'kinematics.target_altitude_m')}`);

    const doc = JSON.parse(JSON.stringify(loaded.params));
    const originalNoFly = doc.no_fly;
    doc.kinematics.target_altitude_m = 1.37;
    doc.kinematics.hover_duration_sec = 5.5;
    doc.vision.confidence_threshold = 0.61;
    const saved = await ipc['bmg:save-parameters'](null, doc);
    check('save-parameters succeeds', saved.success === true, JSON.stringify(saved));

    ipc = bootApp();
    const reopened = (await ipc['bmg:get-parameters']()).params;
    check('reopen shows the saved altitude', getPath(reopened, 'kinematics.target_altitude_m') === 1.37, `${getPath(reopened, 'kinematics.target_altitude_m')}`);
    check('reopen shows the saved hover', getPath(reopened, 'kinematics.hover_duration_sec') === 5.5, `${getPath(reopened, 'kinematics.hover_duration_sec')}`);
    check('reopen keeps untouched fields', JSON.stringify(getPath(reopened, 'lateral_pid')) === JSON.stringify(getPath(loaded.params, 'lateral_pid')), 'lateral_pid round-trips');
    const py = pythonView();
    check('Python loader sees the saved values', py.alt === 1.37 && py.hover === 5.5 && py.conf === 0.61, JSON.stringify(py));

    // ---- Test 2: edit, launch without saving --------------------------------
    // Worst case: the renderer's pre-launch save is skipped, so the store still
    // holds 1.37 while the working document holds 1.73.
    const working = JSON.parse(JSON.stringify(reopened));
    working.kinematics.target_altitude_m = 1.73;
    working.kinematics.hover_duration_sec = 4.25;
    working.no_fly = true;
    spawns.length = 0;
    const started = await ipc['bmg:start-mission'](null, launchOptions(working));
    check('start-mission accepted', started.success === true, JSON.stringify({ success: started.success, error: started.error }));
    const missionSpawn = spawns.find((s) => s.args.some((a) => String(a).endsWith('mission.py')));
    const argv = missionSpawn.args;
    const flag = (f) => argv[argv.indexOf(f) + 1];
    check('argv carries the working altitude', flag('--height') === '1.73', `--height ${flag('--height')}`);
    check('argv --params-json carries the working doc', JSON.parse(flag('--params-json')).kinematics.target_altitude_m === 1.73, 'params-json alt=1.73');
    check('argv arms from the working doc', argv.includes('--no-fly') && !argv.includes('--fly'), argv.filter((a) => a === '--no-fly' || a === '--fly').join(' '));
    const onDiskBefore = pythonView();
    check('store still holds the committed value before launch', onDiskBefore.alt === 1.37, JSON.stringify(onDiskBefore));

    const active = await replayMission(missionSpawn);
    const alt = /altitude=([0-9.]+)m/.exec(active)[1];
    const hover = /hover=([0-9.]+)s/.exec(active)[1];
    check('controller receives the working altitude', alt === '1.73', `mission.py: altitude=${alt}m`);
    check('controller receives the working hover', hover === '4.2' || hover === '4.3' || hover === '4.25', `mission.py: hover=${hover}s`);
    const onDiskAfter = pythonView();
    check('mission.py persists the flown values', onDiskAfter.alt === 1.73 && onDiskAfter.hover === 4.25, JSON.stringify(onDiskAfter));
    check('mission.py does not persist the arming state', onDiskAfter.no_fly === onDiskBefore.no_fly, `no_fly on disk ${onDiskBefore.no_fly} -> ${onDiskAfter.no_fly} (original ${originalNoFly})`);

    ipc = bootApp();
    const afterRelaunch = (await ipc['bmg:get-parameters']()).params;
    check('reopen after the flight shows the flown altitude', getPath(afterRelaunch, 'kinematics.target_altitude_m') === 1.73, `${getPath(afterRelaunch, 'kinematics.target_altitude_m')}`);
  } finally {
    fs.copyFileSync(BACKUP, CONFIG);
    fs.unlinkSync(BACKUP);
    console.log('operator config restored');
  }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(2);
});
