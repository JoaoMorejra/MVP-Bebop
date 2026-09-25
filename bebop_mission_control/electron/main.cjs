const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const http = require('http');
const fs = require('fs');
const os = require('os');
const { spawn, exec, execSync } = require('child_process');
const { createMilestoneParser, scheduleScriptMilestones } = require('./milestones.cjs');
const { createTerminalHost } = require('./terminal.cjs');
const { deferBenchSpawn } = require('./benchCountdown.cjs');

const DIST_DIR = path.join(__dirname, '..', 'dist');
const MISSION_DIR = '/home/joaomoreira/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop';
const NECTAR_SDK_DIR = '/home/joaomoreira/ros2_ws/src/nectar-sdk';
const STREAMER_DIR = path.join(__dirname, '..', 'streamer');
const PYTHON_VENV = '/home/joaomoreira/ros2_ws/.venv/bin/python3';
const NECTAR_ACTIVATOR = '/home/joaomoreira/ros2_ws/bin/nectar-activate';
const CONFIG_PATH = path.join(MISSION_DIR, 'mission_config.json');
const MISSION_PACKAGE_DIR = '/home/joaomoreira/ros2_ws/src/mvp_mission_bebop';
const COMMAND_SCRIPT = path.join(STREAMER_DIR, 'command_bridge.py');
const MJPEG_PORT = 9090;
const DRONE_IP = '192.168.42.1';
const BEBOP_SSID_RE = /^Bebop2?[-_]/i;
/** Where the diagnostics terminal's shell starts. */
const TERMINAL_HOME = '/home/joaomoreira/ros2_ws';

// Bring-up budget: how long the aircraft gets to answer each stage of
// `bmg:ensure-link` before the GCS calls it a failure and says which stage.
const LINK_PING_TIMEOUT_MS = 20000;
const LINK_DRIVER_TIMEOUT_MS = 25000;
const LINK_TOPICS_TIMEOUT_MS = 30000;

// -----------------------------------------------------------------------------
// Nectar SDK & ROS 2 environment
//
// Every subprocess this file spawns must see the same environment a developer
// gets from `source nectar-activate`. Resolving it once by actually sourcing the
// script is more faithful than reconstructing it, and the literal fallback below
// only has to hold if bash itself is unavailable.
// -----------------------------------------------------------------------------
let cachedNectarEnv = null;
function getNectarEnv() {
  if (cachedNectarEnv) return cachedNectarEnv;

  try {
    const rawEnv = execSync(
      `source "${NECTAR_ACTIVATOR}" && node -e "console.log(JSON.stringify(process.env))"`,
      { shell: '/bin/bash', encoding: 'utf-8', timeout: 8000 }
    );
    cachedNectarEnv = JSON.parse(rawEnv.trim());
    return cachedNectarEnv;
  } catch (err) {
    console.warn('[BMG] Dynamic environment resolution failed, using canonical fallback:', err.message);
    cachedNectarEnv = {
      ...process.env,
      VIRTUAL_ENV: '/home/joaomoreira/ros2_ws/.venv',
      ROS_DISTRO: 'jazzy',
      ROS_DOMAIN_ID: '14',
      ROS_AUTOMATIC_DISCOVERY_RANGE: 'LOCALHOST',
      ROS_STATIC_PEERS: '127.0.0.1',
      PYTHONUNBUFFERED: '1',
      LD_LIBRARY_PATH: `/home/joaomoreira/.local/lib:/home/joaomoreira/ros2_ws/.venv/lib:${process.env.LD_LIBRARY_PATH || ''}`,
      PATH: `/home/joaomoreira/ros2_ws/.venv/bin:/opt/ros/jazzy/bin:${process.env.PATH || ''}`,
      PYTHONPATH: [
        '/home/joaomoreira/ros2_ws/install/mvp_mission_bebop/lib/python3.12/site-packages',
        '/home/joaomoreira/ros2_ws/install/nectar/lib/python3.12/site-packages',
        '/home/joaomoreira/ros2_ws/install/nectar_interfaces/lib/python3.12/site-packages',
        '/home/joaomoreira/ros2_ws/install/ros2_bebop_driver/lib/python3.12/site-packages',
        '/opt/ros/jazzy/lib/python3.12/site-packages',
        process.env.PYTHONPATH || '',
      ].filter(Boolean).join(':'),
      AMENT_PREFIX_PATH: [
        '/home/joaomoreira/ros2_ws/install/mvp_mission_bebop',
        '/home/joaomoreira/ros2_ws/install/nectar',
        '/home/joaomoreira/ros2_ws/install/nectar_interfaces',
        '/home/joaomoreira/ros2_ws/install/ros2_bebop_driver',
        '/home/joaomoreira/ros2_ws/install/ros2_parrot_arsdk',
        '/opt/ros/jazzy',
        process.env.AMENT_PREFIX_PATH || '',
      ].filter(Boolean).join(':'),
    };
    return cachedNectarEnv;
  }
}

const cEnv = () => ({ ...process.env, LC_ALL: 'C', LANG: 'C' });

let mainWindow = null;
let server = null;
let driverProcess = null;
let missionProcess = null;
let missionStartedAt = null;
/** Cancels a bench routine still counting down to its spawn (`deferBenchSpawn`). */
let pendingBenchStage = null;
let mjpegProcess = null;
let telemetryProcess = null;
let speechProcess = null;
let commandProcess = null;
/** When the last `BMG_TELEM:` sample landed. Null means none has. */
let latestTelemetryAt = null;

let latestTelemetry = {
  connected: false,
  driver_running: false,
  battery_pct: 0,
  battery_known: false,
  battery_source: 'none',
  wifi_ssid: '',
  wifi_signal_dbm: -100,
  signal_source: 'none',
  speed: 0.0,
  altitude: 0.0,
  flight_time_sec: 0,
  heading: 0.0,
  // No invented place. Until the bridge reports a GPS fix or a real base, the
  // position is unknown and the map draws its local grid.
  latitude: 0,
  longitude: 0,
  base_known: false,
  drone_ip: DRONE_IP,
  flying_state: null,
  flying_state_label: 'unknown',
  gps_fix: false,
  node_present: false,
  topics: {},
  topics_ready: false,
};

// -----------------------------------------------------------------------------
// Log buffers
//
// The renderer can mount a screen long after a process started. Without a
// buffer, whatever was printed before that mount would be lost, which matters
// most for the launch banner and any early failure.
// -----------------------------------------------------------------------------
const LOG_LIMIT = 4000;
const logs = { mission: [], driver: [] };

const LOG_EVENTS = {
  mission: 'bmg:mission-log',
  driver: 'bmg:driver-log',
};

function recordLog(channel, entry) {
  const line = { ...entry, at: Date.now() };
  const buffer = logs[channel];
  if (!buffer) return;
  buffer.push(line);
  if (buffer.length > LOG_LIMIT) buffer.splice(0, buffer.length - LOG_LIMIT);
  send(LOG_EVENTS[channel], line);

  // Also mirror to the terminal that launched the app. `bin/bmg` runs in a
  // shell, and a driver that refuses to start is otherwise only visible by
  // opening Diagnostics and picking the right tab -- no use at all when the
  // window has not come up yet, or when the operator is reading a crash after
  // the fact.
  const stream = entry.type === 'stderr' ? process.stderr : process.stdout;
  stream.write(`[${channel}] ${String(entry.text ?? '').replace(/\n$/, '')}\n`);
}

/**
 * Pipe a child's stream into the log buffers.
 *
 * Draining matters beyond diagnostics: Node spawns with `stdio: 'pipe'`, and an
 * unread pipe blocks the writer once the kernel buffer fills. The MJPEG and
 * telemetry bridges log to stderr through rclpy, so leaving those streams
 * unconsumed froze them mid-flight with nothing on screen to explain it.
 */
function drainInto(stream, channel, type, prefix = '') {
  if (!stream) return;
  stream.setEncoding('utf-8');
  stream.on('data', (chunk) => {
    recordLog(channel, { type, text: prefix ? `${prefix}${chunk}` : chunk });
  });
  stream.on('error', (err) => {
    recordLog(channel, { type: 'stderr', text: `${prefix}[pipe] ${err.message}\n` });
  });
}

/**
 * Split a byte stream into whole lines.
 *
 * The telemetry payload now carries the ROS 2 topic table, which routinely
 * exceeds a single pipe chunk. Splitting each chunk independently dropped every
 * sample that straddled a boundary.
 */
function createLineReader(onLine) {
  let carry = '';
  return (chunk) => {
    carry += chunk;
    const lines = carry.split('\n');
    carry = lines.pop() ?? '';
    // A pathological producer must not grow this without bound.
    if (carry.length > 1 << 20) carry = '';
    for (const line of lines) onLine(line);
  };
}

function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

// -----------------------------------------------------------------------------
// Static server
// -----------------------------------------------------------------------------
const MIME_TYPES = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.mp4': 'video/mp4',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
};

function createStaticServer() {
  return new Promise((resolve) => {
    server = http.createServer((req, res) => {
      let reqPath = decodeURIComponent(req.url.split('?')[0]);

      // Evidence is served straight out of the mission working directory so the
      // renderer never needs a copy of a 14 MP capture.
      if (reqPath.startsWith('/evidence/')) {
        const name = path.basename(reqPath.slice('/evidence/'.length));
        const evidenceFile = path.join(MISSION_DIR, name);
        if (evidenceFile.startsWith(MISSION_DIR) && fs.existsSync(evidenceFile)) {
          const stat = fs.statSync(evidenceFile);
          const ext = path.extname(evidenceFile).toLowerCase();
          res.writeHead(200, {
            'Content-Type': MIME_TYPES[ext] || 'application/octet-stream',
            'Content-Length': stat.size,
            'Cache-Control': 'no-cache',
          });
          return fs.createReadStream(evidenceFile).pipe(res);
        }
        res.writeHead(404);
        return res.end();
      }

      if (reqPath === '/' || reqPath === '') reqPath = '/index.html';

      let filePath = path.join(DIST_DIR, reqPath);
      if (!filePath.startsWith(DIST_DIR) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
        filePath = path.join(DIST_DIR, 'index.html');
      }

      const stat = fs.statSync(filePath);
      const ext = path.extname(filePath).toLowerCase();
      const contentType = MIME_TYPES[ext] || 'application/octet-stream';

      const range = req.headers.range;
      if (range && ext === '.mp4') {
        const parts = range.replace(/bytes=/, '').split('-');
        const start = parseInt(parts[0], 10);
        const end = parts[1] ? parseInt(parts[1], 10) : stat.size - 1;
        res.writeHead(206, {
          'Content-Range': `bytes ${start}-${end}/${stat.size}`,
          'Accept-Ranges': 'bytes',
          'Content-Length': end - start + 1,
          'Content-Type': contentType,
        });
        fs.createReadStream(filePath, { start, end }).pipe(res);
      } else {
        res.writeHead(200, {
          'Content-Type': contentType,
          'Content-Length': stat.size,
          'Accept-Ranges': 'bytes',
        });
        fs.createReadStream(filePath).pipe(res);
      }
    });

    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
}

// -----------------------------------------------------------------------------
// Background services
// -----------------------------------------------------------------------------
let servicesStopping = false;
const serviceRestartTimers = new Map();

/**
 * Supervise one of the Python bridges.
 *
 * Both are spawned through `nectar-activate` rather than the venv interpreter
 * directly. The env snapshot alone is not enough: a mismatch in
 * `AMENT_PREFIX_PATH` or `ROS_DOMAIN_ID` leaves the bridge in its own DDS
 * partition, where it sees no topics and reports a perfectly healthy zero.
 *
 * A bridge that dies is restarted with a bounded backoff, because losing the
 * telemetry bridge mid-flight otherwise takes battery, altitude and link state
 * off the screen permanently.
 */
function superviseService(name, scriptArgs, onStdoutLine) {
  const env = {
    ...getNectarEnv(),
    LC_ALL: 'C',
    LANG: 'C',
    // The telemetry bridge projects odometry onto the operator's real,
    // cached position instead of a hard-coded city.
    BMG_BASE_FILE: locationCachePath(),
  };
  let attempts = 0;

  const launch = () => {
    if (servicesStopping) return null;

    let child;
    try {
      child = spawn(NECTAR_ACTIVATOR, ['python3', ...scriptArgs], {
        cwd: STREAMER_DIR,
        env,
      });
    } catch (err) {
      recordLog('driver', { type: 'stderr', text: `[${name}] falha ao iniciar: ${err.message}\n` });
      return null;
    }

    recordLog('driver', { type: 'stdout', text: `[${name}] iniciado (pid ${child.pid})\n` });

    if (onStdoutLine) {
      const read = createLineReader(onStdoutLine);
      child.stdout.setEncoding('utf-8');
      child.stdout.on('data', read);
    } else {
      drainInto(child.stdout, 'driver', 'stdout', `[${name}] `);
    }
    drainInto(child.stderr, 'driver', 'stderr', `[${name}] `);

    child.on('error', (err) => {
      recordLog('driver', { type: 'stderr', text: `[${name}] ${err.message}\n` });
    });

    child.on('close', (code, signal) => {
      recordLog('driver', {
        type: 'exit',
        text: `[${name}] encerrado (código ${code}${signal ? `, sinal ${signal}` : ''})\n`,
      });
      if (name === 'mjpeg') mjpegProcess = null;
      if (name === 'telemetry') telemetryProcess = null;
      if (name === 'comando') commandProcess = null;
      if (servicesStopping) return;

      attempts += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(attempts, 4));
      recordLog('driver', { type: 'stdout', text: `[${name}] reiniciando em ${delay / 1000}s\n` });
      const timer = setTimeout(() => {
        serviceRestartTimers.delete(name);
        const restarted = launch();
        if (name === 'mjpeg') mjpegProcess = restarted;
        if (name === 'telemetry') telemetryProcess = restarted;
        if (name === 'comando') commandProcess = restarted;
      }, delay);
      serviceRestartTimers.set(name, timer);
    });

    return child;
  };

  return launch();
}

function ingestTelemetryLine(line) {
  if (!line.startsWith('BMG_TELEM:')) {
    if (line.trim()) recordLog('driver', { type: 'stdout', text: `[telemetry] ${line}\n` });
    return;
  }
  try {
    const parsed = JSON.parse(line.slice('BMG_TELEM:'.length).trim());
    latestTelemetry = { ...latestTelemetry, ...parsed };
    latestTelemetryAt = Date.now();
    send('bmg:telemetry-update', latestTelemetry);
  } catch (err) {
    recordLog('driver', { type: 'stderr', text: `[telemetry] amostra ilegível: ${err.message}\n` });
  }
}

/**
 * Declare the aircraft gone when its telemetry stops arriving.
 *
 * `ingestTelemetryLine` merges each sample into the last one, which is right
 * for a field that simply was not in this frame and wrong for every field when
 * there are no frames at all. A drone powered off mid-session, or a bridge that
 * died, leaves nothing to merge — so the last good sample stayed on screen
 * indefinitely, and the station went on reporting 94 % charge and 1.2 m of
 * altitude for an aircraft sitting on a bench with its battery out.
 *
 * Silence is therefore information, and this is what reads it. Every field that
 * describes the airframe is cleared rather than frozen; the ones that describe
 * the station's own view of the world are left alone.
 */
const TELEMETRY_WATCHDOG_INTERVAL_MS = 1000;
/** Past this with no sample, the aircraft is not reporting. The bridge prints at 2 Hz. */
const TELEMETRY_DISCONNECT_AFTER_MS = 3000;

let telemetryWatchdogTimer = null;

function startTelemetryWatchdog() {
  if (telemetryWatchdogTimer) return;
  telemetryWatchdogTimer = setInterval(() => {
    const silentFor = latestTelemetryAt === null ? Infinity : Date.now() - latestTelemetryAt;
    if (silentFor <= TELEMETRY_DISCONNECT_AFTER_MS) return;
    // Nothing to do if it already reads as disconnected: this must not emit a
    // frame a second forever at an interface that has already been told.
    if (!latestTelemetry.connected && !latestTelemetry.driver_running) return;

    latestTelemetry = {
      ...latestTelemetry,
      connected: false,
      driver_running: false,
      node_present: false,
      topics_ready: false,
      battery_known: false,
      battery_pct: 0,
      battery_source: 'none',
      wifi_ssid: '',
      wifi_signal_dbm: -100,
      signal_source: 'none',
      speed: 0,
      altitude: 0,
      flight_time_sec: 0,
      flying_state: null,
      flying_state_label: 'disconnected',
      gps_fix: false,
      camera_tilt_deg: null,
    };
    recordLog('driver', {
      type: 'stderr',
      text: `[telemetry] sem amostras há ${Math.round(silentFor / 1000)}s: aeronave declarada desconectada.\n`,
    });
    send('bmg:telemetry-update', latestTelemetry);
  }, TELEMETRY_WATCHDOG_INTERVAL_MS);
}

function stopTelemetryWatchdog() {
  if (!telemetryWatchdogTimer) return;
  clearInterval(telemetryWatchdogTimer);
  telemetryWatchdogTimer = null;
}

/**
 * Reap bridges left behind by a previous run.
 *
 * Children are not killed when their parent dies on Linux, so a GCS that
 * crashed, was SIGKILLed, or was closed from the window manager leaves both
 * bridges orphaned. The stale MJPEG bridge keeps port 9090 bound, the freshly
 * spawned one cannot listen and exits, and the supervisor restarts it into the
 * same conflict forever -- with the cockpit reading the *old*, deaf bridge's
 * status the whole time.
 *
 * Matched on the absolute script paths this app owns, so nothing else on the
 * machine is in scope.
 */
function reapOrphanedServices() {
  const scripts = [
    path.join(STREAMER_DIR, 'mjpeg_server.py'),
    path.join(STREAMER_DIR, 'telemetry_bridge.py'),
    COMMAND_SCRIPT,
  ];

  for (const script of scripts) {
    try {
      const found = execSync(`pgrep -f ${JSON.stringify(script)} || true`, {
        encoding: 'utf-8',
        timeout: 3000,
      });
      for (const pid of found.split('\n').map((l) => l.trim()).filter(Boolean)) {
        if (Number(pid) === process.pid) continue;
        try {
          process.kill(Number(pid), 'SIGKILL');
          console.warn(`[BMG] Reaped orphaned bridge ${path.basename(script)} (pid ${pid})`);
        } catch (e) { /* already gone, or not ours */ }
      }
    } catch (err) {
      console.warn('[BMG] Could not scan for orphaned bridges:', err.message);
    }
  }
}

function startBackgroundServices() {
  servicesStopping = false;
  reapOrphanedServices();
  mjpegProcess = superviseService('mjpeg', [
    path.join(STREAMER_DIR, 'mjpeg_server.py'),
    String(MJPEG_PORT),
  ]);
  telemetryProcess = superviseService(
    'telemetry',
    [path.join(STREAMER_DIR, 'telemetry_bridge.py')],
    ingestTelemetryLine
  );
  // Started with the others rather than on first use. A participant needs a
  // couple of seconds to complete discovery before anything it publishes is
  // actually delivered, and the command that cannot afford to wait for that is
  // the emergency landing.
  commandProcess = superviseService(
    'comando',
    [COMMAND_SCRIPT],
    ingestCommandLine
  );
}

function stopBackgroundServices() {
  servicesStopping = true;
  for (const timer of serviceRestartTimers.values()) clearTimeout(timer);
  serviceRestartTimers.clear();
  for (const proc of [mjpegProcess, telemetryProcess, commandProcess]) {
    if (!proc) continue;
    try { proc.kill('SIGTERM'); } catch (e) { /* already exited */ }
    // A bridge wedged inside rclpy may not act on SIGTERM, and one that
    // outlives the shutdown holds port 9090 against the next launch.
    //
    // Escalation goes through the ChildProcess handle, never a raw pid: a
    // timer firing on a bare number three seconds later has no guarantee the
    // kernel still maps it to our process. It did not, once -- this killed a
    // bridge that had just been spawned to replace the one being stopped.
    // `proc.kill` on an already-exited child is a no-op.
    const escalate = setTimeout(() => {
      try { proc.kill('SIGKILL'); } catch (e) { /* already gone */ }
    }, 3000);
    escalate.unref?.();
    proc.once('close', () => clearTimeout(escalate));
  }
  mjpegProcess = null;
  telemetryProcess = null;
  commandProcess = null;
}

/**
 * Recycle both Python bridges with a fresh DDS participant.
 *
 * A participant enumerates the host's network interfaces once, when it is
 * created. Joining the aircraft's Wi-Fi afterwards leaves an already-running
 * bridge permanently deaf: the process is healthy, its HTTP endpoint answers,
 * and its rclpy node is simply absent from the ROS 2 graph, so it discovers
 * neither the driver nor a single frame. Measured on the bench: a bridge
 * started before the link sat at 0 frames indefinitely while one started
 * afterwards reached 24.7 fps immediately.
 *
 * Nothing upstream can see that state — `pgrep` finds the process and
 * `/status` reports `running: true` — so the only reliable remedy is to
 * rebuild the participants at the moments the network can have changed.
 */
async function restartBackgroundServices(reason) {
  recordLog('driver', { type: 'stdout', text: `[bridges] reiniciando (${reason})\n` });

  const dying = [mjpegProcess, telemetryProcess, commandProcess].filter(Boolean);
  stopBackgroundServices();

  // Give SIGTERM a moment to land so the new MJPEG bridge does not race the
  // old one for port 9090, then escalate anything still holding on.
  await Promise.all(
    dying.map(
      (proc) =>
        new Promise((resolve) => {
          if (proc.exitCode !== null || proc.signalCode !== null) return resolve();
          const timer = setTimeout(() => {
            try { proc.kill('SIGKILL'); } catch (e) { /* already exited */ }
            resolve();
          }, 2500);
          proc.once('close', () => { clearTimeout(timer); resolve(); });
        })
    )
  );

  startBackgroundServices();
}

// -----------------------------------------------------------------------------
// Deaf-bridge watchdog
//
// The bring-up path recycles the bridges itself, so this covers the cases it
// cannot see: the operator joining the aircraft's network outside the GCS, a
// DHCP renewal, or the radio dropping and returning.
// -----------------------------------------------------------------------------

/** Past this, a telemetry sample no longer describes the present. */
const TELEMETRY_STALE_MS = 5000;

/** How long a bridge may look deaf before it is rebuilt. */
const BRIDGE_DEAF_GRACE_MS = 20000;
/** Floor between two automatic recycles, so a real outage is not a restart loop. */
const BRIDGE_RECYCLE_COOLDOWN_MS = 60000;

let bridgeDeafSince = null;
let lastBridgeRecycleAt = 0;
let lastStreamFrames = -1;
let streamStalledSince = null;
let bridgeWatchdogTimer = null;

async function inspectBridgeLiveness() {
  if (servicesStopping || ensureLinkInFlight) return;
  if (!telemetryProcess && !mjpegProcess) return;

  const driverAlive = await isDriverAlive();
  const now = Date.now();
  let reason = null;

  // 1. The driver is running but the telemetry bridge cannot see its node.
  //    A healthy bridge discovers it within a couple of seconds.
  if (driverAlive && telemetryProcess && !latestTelemetry.node_present) {
    bridgeDeafSince = bridgeDeafSince ?? now;
    if (now - bridgeDeafSince > BRIDGE_DEAF_GRACE_MS) {
      reason = 'a ponte de telemetria não enxerga o nó do driver';
    }
  } else {
    bridgeDeafSince = null;
  }

  // 2. The driver is publishing video but the MJPEG bridge's frame counter is
  //    not moving. Compared against the telemetry bridge's own graph view, so
  //    this only fires when one bridge can see what the other cannot.
  const imageTopic = latestTelemetry.topics?.['/bebop/camera/image_raw'];
  if (!reason && mjpegProcess && imageTopic?.present) {
    const stream = await fetchStreamStatus();
    const advancing = stream.frames !== lastStreamFrames;
    lastStreamFrames = stream.frames;

    if (stream.running && !advancing && !stream.live) {
      streamStalledSince = streamStalledSince ?? now;
      if (now - streamStalledSince > BRIDGE_DEAF_GRACE_MS) {
        reason = 'a ponte MJPEG não recebe quadros de um tópico que está publicando';
      }
    } else {
      streamStalledSince = null;
    }
  } else {
    streamStalledSince = null;
  }

  if (!reason) return;
  if (now - lastBridgeRecycleAt < BRIDGE_RECYCLE_COOLDOWN_MS) return;

  lastBridgeRecycleAt = now;
  bridgeDeafSince = null;
  streamStalledSince = null;
  await restartBackgroundServices(reason);
}

function startBridgeWatchdog() {
  if (bridgeWatchdogTimer) return;
  bridgeWatchdogTimer = setInterval(() => {
    inspectBridgeLiveness().catch((err) =>
      console.warn('[BMG] bridge watchdog:', err.message)
    );
  }, 5000);
}

function stopBridgeWatchdog() {
  if (!bridgeWatchdogTimer) return;
  clearInterval(bridgeWatchdogTimer);
  bridgeWatchdogTimer = null;
}

// -----------------------------------------------------------------------------
// Window
// -----------------------------------------------------------------------------
function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 940,
    minWidth: 1180,
    minHeight: 720,
    backgroundColor: '#12171B',
    title: 'BMG — Bebop Mission Control',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  /**
   * Geolocation is denied by default in Electron, and the station asks for it.
   *
   * The map falls back to the operator's own position whenever the aircraft has
   * no GPS fix, which is every flight indoors. Without this handler the request
   * is refused before Chromium ever tries, and the refusal looks exactly like a
   * laptop that cannot position itself.
   *
   * Only these two are granted. Everything else the page could ask for is
   * refused, because nothing in this interface has a reason to ask for it.
   */
  mainWindow.webContents.session.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'geolocation' || permission === 'media');
  });

  const query = process.env.BMG_INITIAL_QUERY ? `?${process.env.BMG_INITIAL_QUERY}` : '';
  mainWindow.loadURL(`http://127.0.0.1:${port}/${query}`);

  mainWindow.webContents.on('did-finish-load', () => {
    if (!process.env.BMG_SCREENSHOT_PATH) return;
    const delay = Number(process.env.BMG_SCREENSHOT_DELAY_MS || 3000);
    setTimeout(async () => {
      try {
        if (mainWindow && !mainWindow.isDestroyed()) {
          const image = await mainWindow.webContents.capturePage();
          fs.writeFileSync(process.env.BMG_SCREENSHOT_PATH, image.toPNG());
          console.log('[BMG] Screenshot saved to', process.env.BMG_SCREENSHOT_PATH);
        }
      } catch (e) {
        console.error('[BMG] Screenshot failed:', e);
      } finally {
        app.quit();
      }
    }, delay);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  watchMissionDirectory();
}

/**
 * Announce captures as the mission writes them.
 *
 * `MissionContext._write_atomic` renames a dot-prefixed temporary into place, so
 * a file matching these prefixes is already complete. The short delay covers the
 * window where the raw PNG lands a moment before its annotated companion.
 */
function watchMissionDirectory() {
  try {
    if (!fs.existsSync(MISSION_DIR)) return;
    fs.watch(MISSION_DIR, (_eventType, filename) => {
      if (!filename) return;
      if (!filename.startsWith('accident_raw_') && !filename.startsWith('accident_inspected_')) return;

      const stamp = filename.replace(/\.(png|jpg)$/, '').replace(/^accident_(raw|inspected)_/, '');
      const rawFilename = `accident_raw_${stamp}.png`;
      const inspectedFilename = `accident_inspected_${stamp}.jpg`;
      const rawPath = path.join(MISSION_DIR, rawFilename);
      const inspectedPath = path.join(MISSION_DIR, inspectedFilename);

      setTimeout(() => {
        const rawReady = fs.existsSync(rawPath) && fs.statSync(rawPath).size > 1000;
        const inspectedReady = fs.existsSync(inspectedPath) && fs.statSync(inspectedPath).size > 1000;
        if (!rawReady && !inspectedReady) return;

        send('bmg:raw-evidence-ready', {
          filename: rawReady ? rawFilename : inspectedFilename,
          url: rawReady ? `/evidence/${rawFilename}` : `/evidence/${inspectedFilename}`,
          // The lossless original, and null when only the annotated copy made
          // it to disk. `url` falls back to the annotated file so something is
          // always displayable; this field does not, so a panel that must show
          // the unmarked photograph can tell whether it has one.
          rawUrl: rawReady ? `/evidence/${rawFilename}` : null,
          annotatedUrl: inspectedReady ? `/evidence/${inspectedFilename}` : `/evidence/${rawFilename}`,
          timestamp: Date.now(),
        });
      }, 400);
    });
  } catch (err) {
    console.warn('[BMG] Could not watch the mission directory:', err.message);
  }
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------
function execP(command, options = {}) {
  return new Promise((resolve) => {
    exec(command, { encoding: 'utf-8', ...options }, (err, stdout, stderr) => {
      resolve({ ok: !err, stdout: stdout || '', stderr: stderr || '', error: err });
    });
  });
}

function getActiveSystemWifi() {
  const env = cEnv();
  let activeSsid = '';
  let hasBebopRoute = false;

  try {
    const rawId = execSync('iwgetid -r', { env, encoding: 'utf-8', timeout: 1000 }).trim();
    if (rawId) activeSsid = rawId;
  } catch (e) { /* no wireless extension, fall through to nmcli */ }

  if (!activeSsid) {
    try {
      const activeConns = execSync('nmcli -t -f TYPE,NAME connection show --active', {
        env, encoding: 'utf-8', timeout: 1500,
      });
      for (const line of activeConns.split('\n')) {
        if (line.startsWith('802-11-wireless:') || line.startsWith('wifi:')) {
          activeSsid = line.split(':', 2)[1]?.trim() || '';
          if (activeSsid) break;
        }
      }
    } catch (e) { /* NetworkManager unavailable */ }
  }

  try {
    const routeOut = execSync('ip route', { env, encoding: 'utf-8', timeout: 1000 });
    if (routeOut.includes('192.168.42.')) hasBebopRoute = true;
  } catch (e) { /* iproute2 unavailable */ }

  return { activeSsid, hasBebopRoute };
}

const STREAM_STATUS_DOWN = {
  running: false,
  live: false,
  frames: 0,
  fps: 0,
  ageSec: null,
  source: '',
  width: 0,
  height: 0,
};

/** The MJPEG bridge's own frame counter, asked over loopback HTTP. */
function fetchStreamStatus() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port: MJPEG_PORT, path: '/status', timeout: 1200 },
      (res) => {
        let body = '';
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          try {
            const parsed = JSON.parse(body);
            resolve({
              running: true,
              live: Boolean(parsed.live),
              frames: parsed.frames ?? 0,
              fps: parsed.fps ?? 0,
              ageSec: parsed.age_sec ?? null,
              source: parsed.source ?? '',
              width: parsed.width ?? 0,
              height: parsed.height ?? 0,
            });
          } catch {
            resolve(STREAM_STATUS_DOWN);
          }
        });
      }
    );
    req.on('error', () => resolve(STREAM_STATUS_DOWN));
    req.on('timeout', () => { req.destroy(); resolve(STREAM_STATUS_DOWN); });
  });
}

// -----------------------------------------------------------------------------
// IPC: link
// -----------------------------------------------------------------------------
ipcMain.handle('bmg:scan-wifi', async (_event, options = {}) => {
  const env = cEnv();
  const { activeSsid: detectedSsid, hasBebopRoute } = getActiveSystemWifi();
  const force = Boolean(options && options.forceRescan);
  const rescanFlag = force ? '--rescan yes' : '--rescan no';

  const { ok, stdout } = await execP(
    `nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY dev wifi list ${rescanFlag}`,
    { env, timeout: force ? 9000 : 3000 }
  );

  let activeSsid = detectedSsid;
  const networks = [];

  if (ok && stdout) {
    for (const line of stdout.trim().split('\n')) {
      const parts = splitNmcliFields(line);
      if (parts.length < 3) continue;

      const activeFlag = ['yes', 'sim', 'true', '*', '1'].includes(parts[0].trim().toLowerCase());
      const ssid = parts[1].trim();
      // Hidden networks have no name to join by.
      if (!ssid) continue;
      const signal = parseInt(parts[2].trim(), 10) || 0;
      const security = (parts[3] || '').trim();
      const secure = security !== '' && security !== '--';
      const isCurrent = activeFlag || (activeSsid && ssid === activeSsid);
      if (isCurrent) activeSsid = ssid;

      const existing = networks.find((n) => n.ssid === ssid);
      if (!existing) {
        networks.push({
          ssid,
          signal,
          active: Boolean(isCurrent),
          isBebop: BEBOP_SSID_RE.test(ssid),
          secure,
        });
      } else {
        // One SSID, several access points: the strongest is the one joined.
        existing.signal = Math.max(existing.signal, signal);
        if (isCurrent) existing.active = true;
      }
    }
  }

  const connectedToBebop = BEBOP_SSID_RE.test(activeSsid) || hasBebopRoute;
  if (connectedToBebop && !activeSsid && hasBebopRoute) activeSsid = 'Parrot Bebop 2';

  if (connectedToBebop && activeSsid) {
    const entry = networks.find((n) => n.ssid === activeSsid);
    if (entry) entry.active = true;
    else networks.unshift({ ssid: activeSsid, signal: 85, active: true, isBebop: true, secure: false });
  }

  // The aircraft first, then the network the station is on, then by strength.
  networks.sort((a, b) =>
    Number(b.isBebop) - Number(a.isBebop) ||
    Number(b.active) - Number(a.active) ||
    b.signal - a.signal
  );

  if (connectedToBebop) {
    latestTelemetry.connected = true;
    latestTelemetry.wifi_ssid = activeSsid;
  }

  return { networks, currentSsid: activeSsid, connectedToBebop };
});

/**
 * Split one `nmcli -t` line. Terse mode escapes a literal colon inside a field
 * as `\:`, so a plain `split(':')` cut any SSID containing one in two.
 */
function splitNmcliFields(line) {
  const fields = [];
  let current = '';
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '\\' && i + 1 < line.length) {
      current += line[i + 1];
      i += 1;
    } else if (ch === ':') {
      fields.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields;
}

/**
 * Run a command as an argv vector.
 *
 * `exec` would put an operator-supplied SSID through a shell, where a name
 * containing a backtick or `$(...)` is executed rather than joined.
 */
function spawnP(command, args, options = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { ...options });
    } catch (err) {
      return resolve({ ok: false, stdout: '', stderr: err.message, code: null });
    }

    let stdout = '';
    let stderr = '';
    child.stdout?.setEncoding('utf-8');
    child.stderr?.setEncoding('utf-8');
    child.stdout?.on('data', (d) => { stdout += d; });
    child.stderr?.on('data', (d) => { stderr += d; });

    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    const timer = options.timeout
      ? setTimeout(() => {
          try { child.kill('SIGKILL'); } catch (e) { /* already exited */ }
          finish({ ok: false, stdout, stderr: stderr || 'timeout', code: null });
        }, options.timeout)
      : null;

    child.on('error', (err) => {
      if (timer) clearTimeout(timer);
      finish({ ok: false, stdout, stderr: err.message, code: null });
    });
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      finish({ ok: code === 0, stdout, stderr, code });
    });
  });
}

/** Is the aircraft answering at its fixed address? */
async function pingDrone(attempts = 2) {
  const env = cEnv();
  for (let i = 0; i < attempts; i += 1) {
    const probe = await spawnP('ping', ['-c', '1', '-W', '1', DRONE_IP], { env, timeout: 3000 });
    if (probe.ok) return true;
  }
  return false;
}

ipcMain.handle('bmg:connect-wifi', async (_event, ssid) => {
  const env = cEnv();
  const target = String(ssid ?? '').trim();
  if (!target) return { success: false, error: 'SSID vazio' };

  const joined = await spawnP('nmcli', ['device', 'wifi', 'connect', target], { env, timeout: 40000 });
  if (!joined.ok) {
    return { success: false, error: (joined.stderr || 'nmcli recusou a conexão').trim() };
  }

  latestTelemetry.wifi_ssid = target;
  // NetworkManager returns before DHCP has settled on the drone's side.
  await new Promise((r) => setTimeout(r, 1500));

  const reachable = await pingDrone(3);
  // Only an answered ping is evidence of a link. Asserting `connected` here
  // regardless is what let the readiness panel go green over a dead radio.
  latestTelemetry.connected = reachable;
  send('bmg:telemetry-update', latestTelemetry);

  return {
    success: true,
    pingOk: reachable,
    message: reachable
      ? `A aeronave respondeu em ${DRONE_IP}`
      : 'Rede conectada. A aeronave ainda não respondeu ao ping.',
  };
});

// -----------------------------------------------------------------------------
// IPC: ROS 2 driver
// -----------------------------------------------------------------------------
/**
 * Bring the ROS 2 driver up.
 *
 * Idempotent on purpose: the caller may be the operator, the automatic link
 * orchestration, or a retry after a probe came back empty, and none of them
 * should be able to leave two `bebop_driver` processes fighting over the
 * aircraft's ARSDK session.
 */
/**
 * Make sure the kernel knows how to reach the aircraft before the driver tries.
 *
 * The Bebop's video is RTP over UDP straight from 192.168.42.1, and the driver
 * only ever receives it if packets from that address arrive on the interface
 * joined to its network. Joining the AP normally installs that route through
 * DHCP, but it does not when the station also holds a default route on Ethernet
 * or a tether — the usual setup here, because the map needs internet — and the
 * failure is silent: the driver starts, subscribes, and publishes nothing.
 *
 * The route is added if it is missing and if we are allowed to. Without root we
 * are not, so the fallback is to say so with the exact command, which is worth
 * far more than a `|| true` that hides the one fact explaining a black screen.
 */
async function ensureDroneRoute() {
  const env = cEnv();
  const target = latestTelemetry.drone_ip || DRONE_IP;

  const existing = await execP(`ip route get ${target}`, { env, timeout: 3000 });
  if (existing.ok && existing.stdout.trim()) {
    recordLog('driver', {
      type: 'stdout',
      text: `[BMG] Rota para ${target}: ${existing.stdout.trim().split('\n')[0]}\n`,
    });
    return true;
  }

  const iface = await execP('iwgetid -r 2>/dev/null && iwgetid | cut -d" " -f1', { env, timeout: 3000 });
  const device = iface.stdout.trim().split('\n').pop() || 'wlan0';

  const added = await execP(`ip route add ${target}/32 dev ${device}`, { env, timeout: 4000 });
  if (added.ok) {
    recordLog('driver', { type: 'stdout', text: `[BMG] Rota ${target} adicionada em ${device}.\n` });
    return true;
  }

  recordLog('driver', {
    type: 'stderr',
    text:
      `[BMG] Sem rota para ${target} e não foi possível criá-la (requer privilégios).\n` +
      `[BMG] O vídeo do Bebop chega por RTP/UDP desse endereço; sem rota não há imagem.\n` +
      `[BMG] Execute:  sudo ip route add ${target}/32 dev ${device}\n`,
  });
  return false;
}

async function startDriverProcess() {
  if (driverProcess) return { success: true, message: 'O driver já está em execução.' };

  const alreadyUp = await isDriverAlive();
  if (alreadyUp) {
    return { success: true, message: 'O driver já estava em execução (processo externo).' };
  }

  await ensureDroneRoute();

  try {
    driverProcess = spawn('make', ['driver-bebop', `IP=${latestTelemetry.drone_ip || DRONE_IP}`], {
      cwd: NECTAR_SDK_DIR,
      env: getNectarEnv(),
    });

    recordLog('driver', { type: 'stdout', text: `[BMG] make driver-bebop IP=${latestTelemetry.drone_ip || DRONE_IP} em ${NECTAR_SDK_DIR}\n` });
    drainInto(driverProcess.stdout, 'driver', 'stdout');
    drainInto(driverProcess.stderr, 'driver', 'stderr');
    driverProcess.on('error', (err) => {
      recordLog('driver', { type: 'stderr', text: `[BMG] ${err.message}\n` });
    });
    driverProcess.on('close', (code) => {
      recordLog('driver', { type: 'exit', text: `[BMG] Driver encerrado com código ${code}\n` });
      driverProcess = null;
    });

    return { success: true, pid: driverProcess.pid };
  } catch (error) {
    driverProcess = null;
    return { success: false, error: error.message };
  }
}

/**
 * Command lines that mean the Bebop driver is up.
 *
 * Kept specific on purpose: this application runs out of
 * /home/joaomoreira/ros2_ws/bebop_mission_control, so a loose "ros2.*bebop" matches
 * Electron's own command line.
 */
const DRIVER_PATTERN = 'bebop_driver_node|ros2_bebop_driver|bebop_node_launch';

/**
 * Is the Bebop driver up?
 *
 * Probed with an argv vector, never through a shell, and every shell in the
 * result is discarded.
 *
 * `exec('pgrep -f "<pattern>"')` runs the command under `/bin/sh -c`, and that
 * shell carries the pattern in its own command line, so `pgrep -f` matched it
 * and the probe answered "running" unconditionally. `startDriverProcess` reads
 * this to stay idempotent, so it concluded the driver was already up and never
 * started one -- the aircraft connected, the GCS reported a healthy driver, and
 * no `bebop_driver` process ever existed. It is the reason the driver "did not
 * start automatically" on connecting to the aircraft's network.
 */
async function isDriverAlive() {
  const probe = await spawnP('pgrep', ['-af', DRIVER_PATTERN], { timeout: 2500 });
  if (!probe.ok) return false;

  const matcher = new RegExp(DRIVER_PATTERN);
  return probe.stdout
    .split('\n')
    .map((line) => line.replace(/^\d+\s+/, '').trim())
    .filter(Boolean)
    .some((command) => {
      // A shell or another pgrep carrying the pattern as an argument is not a
      // driver, however well it matches.
      if (/^(\/\S+\/)?(ba|da|z|k)?sh\b/.test(command)) return false;
      if (/\bpgrep\b/.test(command)) return false;
      return matcher.test(command);
    });
}

async function stopDriverProcess() {
  await execP('make driver-stop', { cwd: NECTAR_SDK_DIR, env: getNectarEnv(), timeout: 15000 });
  if (driverProcess) {
    try { driverProcess.kill('SIGKILL'); } catch (e) { /* already gone */ }
    driverProcess = null;
  }
  return { success: true };
}

ipcMain.handle('bmg:start-driver', async () => startDriverProcess());
ipcMain.handle('bmg:stop-driver', async () => stopDriverProcess());

// -----------------------------------------------------------------------------
// Deterministic link bring-up
//
// Joining the aircraft's network and starting a process are not the same thing
// as being ready to fly. This walks the whole chain in one place and reports
// which stage it stopped at, replacing the reactive effect in the renderer that
// fired once per session, could not retry, and had no idea whether the driver
// was actually exchanging data with the airframe.
// -----------------------------------------------------------------------------

const REQUIRED_TOPICS = [
  '/bebop/odom',
  '/bebop/camera/image_raw',
  '/bebop/camera/camera_info',
  '/bebop/states/battery',
  '/bebop/cmd_vel',
  '/bebop/takeoff',
  '/bebop/land',
  '/bebop/move_camera',
];

function linkProgress(stage, status, message, extra = {}) {
  const event = { stage, status, message, at: Date.now(), ...extra };
  send('bmg:link-progress', event);
  recordLog('driver', { type: status === 'error' ? 'stderr' : 'stdout', text: `[link] ${stage}: ${message}\n` });
  return event;
}

/**
 * Snapshot of what the aircraft is actually delivering right now.
 *
 * Topic facts come from the telemetry bridge, which holds the subscriptions and
 * can tell an advertised topic from a delivering one. Video is the exception:
 * the MJPEG bridge owns those subscriptions, so its frame counter is the proof.
 */
async function readLinkReadiness() {
  const stream = await fetchStreamStatus();

  // A bridge that died leaves `latestTelemetry` frozen on its last good sample,
  // which would keep reporting a healthy aircraft indefinitely. Past this
  // window the graph facts are unknown, not true.
  const telemetryAge =
    latestTelemetryAt === null ? Infinity : Date.now() - latestTelemetryAt;
  const telemetryFresh = telemetryAge < TELEMETRY_STALE_MS;

  const topics = telemetryFresh ? latestTelemetry.topics || {} : {};

  const details = REQUIRED_TOPICS.map((name) => {
    const info = topics[name] || {};
    const isVideo = name === '/bebop/camera/image_raw';
    const receiving = isVideo
      ? Boolean(info.present) && stream.live
      : Boolean(info.receiving);
    return {
      topic: name,
      direction: info.direction ?? (name === '/bebop/cmd_vel' ? 'in' : 'out'),
      present: Boolean(info.present),
      receiving,
      publishers: info.publishers ?? 0,
      subscribers: info.subscribers ?? 0,
      ageSec: info.age_sec ?? null,
    };
  });

  const missing = details.filter((d) => !d.receiving).map((d) => d.topic);

  return {
    at: Date.now(),
    telemetryFresh,
    connected: telemetryFresh && Boolean(latestTelemetry.connected),
    nodePresent: telemetryFresh && Boolean(latestTelemetry.node_present),
    driverRunning: telemetryFresh && Boolean(latestTelemetry.driver_running),
    batteryKnown: telemetryFresh && Boolean(latestTelemetry.battery_known),
    stream,
    topics: details,
    missing,
    ready: Boolean(latestTelemetry.connected) && Boolean(latestTelemetry.node_present) && missing.length === 0,
  };
}

async function waitFor(predicate, timeoutMs, intervalMs = 700) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await predicate();
    if (value) return value;
    if (Date.now() >= deadline) return null;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

let ensureLinkInFlight = null;

ipcMain.handle('bmg:ensure-link', async (_event, options = {}) => {
  // A second request joins the attempt already running rather than starting a
  // competing `make driver-bebop` against the same aircraft.
  if (ensureLinkInFlight) return ensureLinkInFlight;

  const attempt = (async () => {
    try {
      // 1. Network
      if (options.ssid) {
        linkProgress('wifi', 'running', `Entrando em ${options.ssid}`);
        const env = cEnv();
        const joined = await spawnP('nmcli', ['device', 'wifi', 'connect', String(options.ssid)], {
          env,
          timeout: 40000,
        });
        if (!joined.ok) {
          const message = (joined.stderr || 'nmcli recusou a conexão').trim();
          return {
            success: false,
            stage: 'wifi',
            message,
            readiness: await readLinkReadiness(),
            event: linkProgress('wifi', 'error', message),
          };
        }
        latestTelemetry.wifi_ssid = String(options.ssid);
      }

      // 2. Aircraft reachable
      //
      // Live odometry is stronger evidence than ICMP: the driver is holding an
      // ARSDK session with the airframe and samples are arriving. Some Bebop
      // firmware drops pings under video load, and waiting out the ping budget
      // there would stall a bring-up over a link that is demonstrably up.
      linkProgress('ping', 'running', `Procurando a aeronave em ${DRONE_IP}`);
      const reachable = await waitFor(
        async () => latestTelemetry.driver_running || (await pingDrone(1)),
        LINK_PING_TIMEOUT_MS,
        1000
      );
      latestTelemetry.connected = Boolean(reachable);
      send('bmg:telemetry-update', latestTelemetry);
      if (!reachable) {
        const message = `A aeronave não respondeu em ${DRONE_IP}`;
        return {
          success: false,
          stage: 'ping',
          message,
          readiness: await readLinkReadiness(),
          event: linkProgress('ping', 'error', message),
        };
      }
      linkProgress('ping', 'ok', 'Aeronave respondeu');

      // 3. Rebuild the DDS participants now that the interface list is final.
      //    Both bridges are normally started at app launch, before the operator
      //    has joined the aircraft's network; a participant created then never
      //    discovers the driver, and reports itself perfectly healthy while
      //    doing so.
      linkProgress('bridges', 'running', 'Reiniciando as pontes no enlace atual');
      await restartBackgroundServices('enlace com a aeronave estabelecido');
      lastBridgeRecycleAt = Date.now();
      linkProgress('bridges', 'ok', 'Pontes reiniciadas');

      // 4. Driver process
      linkProgress('driver', 'running', 'Subindo o driver ROS 2');
      const started = await startDriverProcess();
      if (!started.success) {
        return {
          success: false,
          stage: 'driver',
          message: started.error || 'Falha ao iniciar o driver',
          readiness: await readLinkReadiness(),
          event: linkProgress('driver', 'error', started.error || 'Falha ao iniciar o driver'),
        };
      }

      const nodeUp = await waitFor(
        async () => (await isDriverAlive()) && latestTelemetry.node_present,
        LINK_DRIVER_TIMEOUT_MS
      );
      if (!nodeUp) {
        const message = 'O nó /bebop/bebop_driver não apareceu no grafo ROS 2';
        return {
          success: false,
          stage: 'driver',
          message,
          readiness: await readLinkReadiness(),
          event: linkProgress('driver', 'error', message),
        };
      }
      linkProgress('driver', 'ok', 'Driver no ar');

      // 5. Bidirectional topic contract
      linkProgress('topics', 'running', 'Validando tópicos da aeronave');
      const readiness = await waitFor(async () => {
        const snapshot = await readLinkReadiness();
        return snapshot.ready ? snapshot : null;
      }, LINK_TOPICS_TIMEOUT_MS);

      if (!readiness) {
        const snapshot = await readLinkReadiness();
        const message = `Tópicos sem tráfego: ${snapshot.missing.join(', ') || 'desconhecido'}`;
        return {
          success: false,
          stage: 'topics',
          message,
          readiness: snapshot,
          event: linkProgress('topics', 'error', message, { missing: snapshot.missing }),
        };
      }

      return {
        success: true,
        stage: 'ready',
        message: 'Aeronave pronta: todos os tópicos essenciais em tráfego',
        readiness,
        event: linkProgress('topics', 'ok', 'Todos os tópicos essenciais em tráfego'),
      };
    } catch (error) {
      return {
        success: false,
        stage: 'error',
        message: error.message,
        readiness: await readLinkReadiness(),
        event: linkProgress('error', 'error', error.message),
      };
    } finally {
      ensureLinkInFlight = null;
    }
  })();

  ensureLinkInFlight = attempt;
  return attempt;
});

ipcMain.handle('bmg:get-link-readiness', async () => readLinkReadiness());

ipcMain.handle('bmg:check-driver-status', async () => {
  const running = await isDriverAlive();
  return { running: running || Boolean(latestTelemetry.driver_running) };
});

// -----------------------------------------------------------------------------
// IPC: mission parameters
// -----------------------------------------------------------------------------
ipcMain.handle('bmg:get-parameters', async () => {
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      return { success: true, params: JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8')) };
    } catch (e) {
      console.warn('[BMG] mission_config.json is unreadable, asking Python for defaults:', e.message);
    }
  }
  return readDefaultParameters();
});

function readDefaultParameters() {
  return new Promise((resolve) => {
    exec(
      `${PYTHON_VENV} -c "import json; from mvp_mission_bebop.parameters import MissionParameters; print(json.dumps(MissionParameters().to_dict()))"`,
      { cwd: MISSION_DIR, env: getNectarEnv(), timeout: 15000 },
      (err, stdout, stderr) => {
        if (err || !stdout) {
          return resolve({ success: false, error: (stderr || err?.message || '').trim() });
        }
        try {
          resolve({ success: true, params: JSON.parse(stdout.trim()) });
        } catch (parseErr) {
          resolve({ success: false, error: parseErr.message });
        }
      }
    );
  });
}

ipcMain.handle('bmg:get-default-parameters', async () => readDefaultParameters());

ipcMain.handle('bmg:save-parameters', async (_event, params) => {
  if (!params || typeof params !== 'object') {
    return { success: false, error: 'Payload de parâmetros vazio' };
  }
  try {
    // Written through a temporary file so a mission reading the config never
    // sees a half-written document.
    const temporary = `${CONFIG_PATH}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(params, null, 2), 'utf-8');
    fs.renameSync(temporary, CONFIG_PATH);
    return { success: true };
  } catch (err) {
    console.error('[BMG] Failed to write mission_config.json:', err);
    return { success: false, error: err.message };
  }
});

// -----------------------------------------------------------------------------
// IPC: environment, telemetry, streamer
// -----------------------------------------------------------------------------
ipcMain.handle('bmg:get-env-info', async () => {
  const env = getNectarEnv();
  return {
    activated: Boolean(env.VIRTUAL_ENV),
    venvPath: env.VIRTUAL_ENV || '/home/joaomoreira/ros2_ws/.venv',
    rosDistro: env.ROS_DISTRO || 'jazzy',
    rosDomainId: env.ROS_DOMAIN_ID || '14',
    pythonPath: path.join(env.VIRTUAL_ENV || '/home/joaomoreira/ros2_ws/.venv', 'bin', 'python3'),
  };
});

ipcMain.handle('bmg:get-telemetry', async () => latestTelemetry);

ipcMain.handle('bmg:get-stream-status', async () => fetchStreamStatus());

ipcMain.handle('bmg:get-log-history', async () => ({ mission: logs.mission, driver: logs.driver }));

ipcMain.handle('bmg:get-diagnostics', async () => {
  const env = getNectarEnv();
  const errors = [];

  const [psResult, stream] = await Promise.all([
    execP('ps -eo pid=,args= | grep -E "mission\\.py|bebop_driver_node|ros2_bebop_driver|mjpeg_server|telemetry_bridge" | grep -v grep', { timeout: 3000 }),
    fetchStreamStatus(),
  ]);

  const parseLines = (text) => text.split('\n').map((l) => l.trim()).filter(Boolean);

  // The graph comes from the telemetry bridge, which holds a live rclpy node
  // and therefore sees it accurately. `ros2 node list` goes through a daemon
  // that caches whatever graph existed when it first started; with discovery
  // confined to loopback it reported zero nodes while the aircraft was
  // streaming. The CLI is kept only as a fallback for when the bridge is down,
  // with `--no-daemon` so at least it discovers afresh.
  let nodes = latestTelemetry.graph?.nodes ?? [];
  let topics = latestTelemetry.graph?.topics ?? [];

  if (!nodes.length && !topics.length) {
    const [nodesResult, topicsResult] = await Promise.all([
      execP('ros2 node list --no-daemon', { env, timeout: 12000 }),
      execP('ros2 topic list --no-daemon', { env, timeout: 12000 }),
    ]);
    if (!nodesResult.ok) errors.push('ros2 node list não respondeu');
    if (!topicsResult.ok) errors.push('ros2 topic list não respondeu');
    nodes = parseLines(nodesResult.stdout);
    topics = parseLines(topicsResult.stdout);
    if (!telemetryProcess) errors.push('ponte de telemetria fora do ar: grafo obtido pelo CLI');
  }

  const processes = parseLines(psResult.stdout)
    .map((line) => {
      const match = /^(\d+)\s+(.*)$/.exec(line);
      return match ? { pid: Number(match[1]), command: match[2] } : null;
    })
    .filter(Boolean);

  const driverProbe = await isDriverAlive();

  return {
    at: Date.now(),
    env: {
      activated: Boolean(env.VIRTUAL_ENV),
      venvPath: env.VIRTUAL_ENV || '/home/joaomoreira/ros2_ws/.venv',
      rosDistro: env.ROS_DISTRO || 'jazzy',
      rosDomainId: env.ROS_DOMAIN_ID || '14',
      pythonPath: path.join(env.VIRTUAL_ENV || '/home/joaomoreira/ros2_ws/.venv', 'bin', 'python3'),
    },
    missionRunning: Boolean(missionProcess),
    driverRunning: driverProbe || Boolean(driverProcess),
    streamer: stream,
    telemetryBridge: { running: Boolean(telemetryProcess) },
    nodes,
    topics,
    processes,
    readiness: await readLinkReadiness(),
    missionDir: MISSION_DIR,
    configPath: CONFIG_PATH,
    errors,
  };
});

// -----------------------------------------------------------------------------
// The voice copilot
//
// `announcer.py --serve` is a resident process rather than one invocation per
// sentence. Starting Python, importing `google.genai` and opening a Live
// session costs seconds; the post-landing report wants a finding read every two,
// so the session has to be warm before the line arrives.
//
// The daemon speaks one request at a time and answers `done` when the audio has
// finished playing, which is what the renderer waits on before revealing the
// card that matches the sentence. Nothing here times the cadence -- the ear
// leads and the screen follows.
// -----------------------------------------------------------------------------

const SPEECH_EVENT_PREFIX = 'BMG_SPEECH:';
let speechSeq = 0;

function ingestSpeechLine(line) {
  const trimmed = line.trim();
  if (!trimmed) return;
  if (!trimmed.startsWith(SPEECH_EVENT_PREFIX)) {
    recordLog('mission', { type: 'stdout', text: `[copiloto] ${trimmed}\n` });
    return;
  }
  try {
    const event = JSON.parse(trimmed.slice(SPEECH_EVENT_PREFIX.length));
    if (event.event === 'done') {
      send('bmg:announce-done', { id: event.id ?? null, ok: Boolean(event.ok) });
    } else if (event.event === 'ready') {
      // The daemon starts whether or not it can actually synthesise. Saying so
      // here is the difference between "the copilot is quiet" being a mystery
      // and it being a line in Diagnóstico naming the missing piece.
      pushVoiceLevel();
      const ok = event.synthesis && event.playback;
      recordLog('mission', {
        type: ok ? 'stdout' : 'stderr',
        text: ok
          ? `[copiloto] pronto (voz ${event.voice}, modelo ${event.model})\n`
          : `[copiloto] sem voz: ${event.detail}. A apresentação segue em silêncio, na cadência própria.\n`,
      });
    }
  } catch (err) {
    recordLog('mission', { type: 'stderr', text: `[copiloto] evento ilegível: ${trimmed}\n` });
  }
}

/**
 * The speech daemon, started on first use and kept for the session.
 *
 * Returns null when it cannot be started at all, so a caller reports that the
 * copilot is unavailable rather than queueing lines into nothing.
 */
function ensureSpeechProcess() {
  if (speechProcess) return speechProcess;

  const env = { ...getNectarEnv(), PYTHONUNBUFFERED: '1' };
  let child;
  try {
    child = spawn(
      NECTAR_ACTIVATOR,
      ['python3', '-m', 'mvp_mission_bebop.telemetry.announcer', '--serve'],
      { cwd: MISSION_PACKAGE_DIR, env }
    );
  } catch (err) {
    recordLog('mission', { type: 'stderr', text: `[copiloto] falha ao iniciar: ${err.message}\n` });
    return null;
  }

  speechProcess = child;
  child.stdout.setEncoding('utf-8');
  child.stdout.on('data', createLineReader(ingestSpeechLine));
  drainInto(child.stderr, 'mission', 'stderr', '[copiloto] ');

  child.on('error', (err) => {
    recordLog('mission', { type: 'stderr', text: `[copiloto] ${err.message}\n` });
  });

  child.on('close', (code, signal) => {
    speechProcess = null;
    recordLog('mission', {
      type: 'exit',
      text: `[copiloto] encerrado (código ${code}${signal ? `, sinal ${signal}` : ''})\n`,
    });
    // A request in flight when the daemon died would otherwise leave the
    // renderer waiting forever for a `done` that is not coming.
    send('bmg:announce-done', { id: null, ok: false });
  });

  return child;
}

function stopSpeechProcess() {
  const child = speechProcess;
  if (!child) return;
  speechProcess = null;
  try {
    child.stdin.write(JSON.stringify({ op: 'quit' }) + '\n');
  } catch (e) { /* the pipe is already gone */ }
  try { child.kill('SIGTERM'); } catch (e) { /* already exited */ }
  const escalate = setTimeout(() => {
    try { child.kill('SIGKILL'); } catch (e) { /* already gone */ }
  }, 2500);
  escalate.unref?.();
  child.once('close', () => clearTimeout(escalate));
}

ipcMain.handle('bmg:announce', async (_event, payload = {}) => {
  const text = typeof payload === 'string' ? payload : String(payload.text ?? '').trim();
  if (!text) return { success: false, error: 'texto vazio' };

  const child = ensureSpeechProcess();
  if (!child) return { success: false, error: 'copiloto indisponível' };

  speechSeq += 1;
  const id = speechSeq;
  const request = {
    id,
    text,
    priority: typeof payload === 'string' ? 'NORMAL' : payload.priority ?? 'NORMAL',
    verbatim: typeof payload === 'string' ? true : payload.verbatim !== false,
    timeout: typeof payload === 'string' ? 30 : payload.timeout ?? 30,
  };

  try {
    child.stdin.write(JSON.stringify(request) + '\n');
  } catch (err) {
    return { success: false, error: err.message };
  }
  return { success: true, id };
});

/** Drop whatever is queued and silence the line being spoken. */
ipcMain.handle('bmg:cancel-speech', async () => {
  if (!speechProcess) return { success: true, cancelled: false };
  try {
    speechProcess.stdin.write(JSON.stringify({ op: 'cancel' }) + '\n');
    return { success: true, cancelled: true };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// -----------------------------------------------------------------------------
// The camera gimbal
//
// Same shape and the same reason: a resident rclpy participant, because a cold
// `ros2 topic pub` takes seconds to reach the wire and an operator dragging the
// tilt control would watch the camera replay the drag several seconds late.
// -----------------------------------------------------------------------------

const COMMAND_EVENT_PREFIX = 'BMG_CMD:';
/** The last tilt actually published, so the interface reports the camera rather than the handle. */
let lastCameraTilt = null;

function ingestCommandLine(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith(COMMAND_EVENT_PREFIX)) return;
  try {
    const event = JSON.parse(trimmed.slice(COMMAND_EVENT_PREFIX.length));
    if (event.event === 'tilt') {
      lastCameraTilt = event.tilt;
      send('bmg:camera-tilt-changed', { tilt: event.tilt, pan: event.pan ?? 0 });
    } else if (event.event === 'land') {
      recordLog('mission', {
        type: 'stderr',
        text: `[comando] pouso de emergência publicado em ${event.repeats}x /bebop/land\n`,
      });
    }
  } catch (e) { /* a malformed event is not worth failing a drag over */ }
}

/**
 * Write one request to the resident bridge.
 *
 * Returns false when the bridge is not up, which the caller reports rather than
 * papers over: an abort that could not be published is something the operator
 * has to know about, not something to retry silently.
 */
function sendCommand(request) {
  const child = commandProcess;
  if (!child) return false;
  try {
    child.stdin.write(JSON.stringify(request) + '\n');
    return true;
  } catch (err) {
    recordLog('driver', { type: 'stderr', text: `[comando] ${err.message}\n` });
    return false;
  }
}

ipcMain.handle('bmg:camera-tilt', async (_event, payload = {}) => {
  const degrees = typeof payload === 'number' ? payload : Number(payload.tilt);
  if (!Number.isFinite(degrees)) return { success: false, error: 'ângulo inválido' };
  const pan = typeof payload === 'object' && Number.isFinite(Number(payload.pan))
    ? Number(payload.pan)
    : 0;

  if (!sendCommand({ tilt: degrees, pan })) {
    return { success: false, error: 'ponte de comando indisponível' };
  }
  return { success: true, tilt: degrees, pan };
});

ipcMain.handle('bmg:get-camera-tilt', async () => ({ tilt: lastCameraTilt }));

/**
 * Ask the running mission to continue at a different stage.
 *
 * Published on the aircraft's namespace as an Int32; `mission.py` subscribes,
 * sets the jump event on its context, and the step that is running unwinds
 * through its own abort path so the runner can pick up at the requested stage.
 *
 * The mission is the authority on whether the jump is allowed — it refuses a
 * return to stage 1 at an airframe that is already flying — so this end only
 * carries the request.
 */
ipcMain.handle('bmg:goto-stage', async (_event, payload = {}) => {
  const stage = typeof payload === 'number' ? payload : Number(payload.stage);
  if (!Number.isInteger(stage) || stage < 1 || stage > 5) {
    return { success: false, error: `Etapa inválida: ${payload?.stage ?? payload}` };
  }
  if (!missionProcess) {
    return { success: false, error: 'Nenhuma missão em andamento para receber o salto' };
  }
  if (!sendCommand({ op: 'stage', stage })) {
    return { success: false, error: 'ponte de comando indisponível' };
  }
  recordLog('mission', {
    type: 'stdout',
    text: `[BMG] Salto para a etapa ${stage} solicitado pela estação.\n`,
  });
  return { success: true, stage };
});

// -----------------------------------------------------------------------------
// Operator location cache
//
// The Bebop's own Wi-Fi has no route to the internet, so the moment the station
// joins it every geolocation service becomes unreachable. The position is
// therefore resolved while there is still a route -- at start-up, and whenever
// it is asked for -- and written to disk. From then on the cached coordinate is
// the base the map centres on and the telemetry bridge projects odometry onto,
// instead of a hard-coded city nobody is standing in.
// -----------------------------------------------------------------------------
/** A network (IP) fix is replaced by a newer one only after this long. */
const LOCATION_IP_REFRESH_MS = 6 * 60 * 60 * 1000;
const LOCATION_REFRESH_INTERVAL_MS = 10 * 60 * 1000;
let locationRefreshTimer = null;

function locationCachePath() {
  return path.join(app.getPath('userData'), 'operator-location.json');
}

function readLocationCache() {
  try {
    const data = JSON.parse(fs.readFileSync(locationCachePath(), 'utf-8'));
    const latitude = Number(data.latitude);
    const longitude = Number(data.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
    if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
    return { ...data, latitude, longitude };
  } catch {
    return null;
  }
}

/** Atomic, so the telemetry bridge re-reading it never sees half a file. */
function writeLocationCache(entry) {
  try {
    const target = locationCachePath();
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(entry, null, 2));
    fs.renameSync(temporary, target);
    return true;
  } catch (err) {
    console.warn('[BMG] Could not persist the operator location:', err.message);
    return false;
  }
}

/**
 * Whether a fresh fix should overwrite the cache.
 *
 * A device fix (Chromium's own positioning, tens of metres) always wins. A
 * network fix (IP, kilometres) only replaces another network fix, or a device
 * fix that has aged past the refresh window -- a coarse answer must not erase
 * a precise one taken minutes ago at the same site.
 */
function shouldReplaceCache(cached, incomingSource) {
  if (!cached) return true;
  if (incomingSource === 'device') return true;
  if (cached.source !== 'device') return true;
  return Date.now() - Number(cached.at || 0) > LOCATION_IP_REFRESH_MS;
}

function requestJson(url) {
  return new Promise((resolve) => {
    const client = url.startsWith('https:') ? require('https') : http;
    const req = client.get(url, { timeout: 4000 }, (res) => {
      if (res.statusCode !== 200) {
        res.resume();
        return resolve(null);
      }
      let body = '';
      res.setEncoding('utf-8');
      res.on('data', (chunk) => {
        body += chunk;
        if (body.length > 64_000) req.destroy();
      });
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve(null));
  });
}

/** Live IP geolocation; writes the cache on success. Null when there is no route. */
async function resolveNetworkLocation() {
  let data = await requestJson('https://ipwho.is/');
  if (!data || data.success === false || !Number.isFinite(Number(data?.latitude))) {
    data = await requestJson('https://ipapi.co/json/');
  }
  const latitude = Number(data?.latitude);
  const longitude = Number(data?.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;

  const entry = {
    latitude,
    longitude,
    /** City-level resolution. Stated, never guessed at finer. */
    accuracyM: 5000,
    city: typeof data.city === 'string' ? data.city : null,
    region: typeof data.region === 'string' ? data.region : null,
    source: 'ip',
    at: Date.now(),
  };
  if (shouldReplaceCache(readLocationCache(), 'ip')) writeLocationCache(entry);
  return entry;
}

function startLocationRefresh() {
  // Resolved at start-up, while the station is most likely still on a network
  // with internet, and refreshed so a station carried to a new site catches up.
  void resolveNetworkLocation().then((entry) => {
    recordLog('driver', {
      type: 'stdout',
      text: entry
        ? `[geo] posição do operador resolvida (${entry.latitude.toFixed(4)}, ${entry.longitude.toFixed(4)}${entry.city ? `, ${entry.city}` : ''}) e gravada em cache\n`
        : readLocationCache()
        ? '[geo] sem internet: usando a posição do operador em cache\n'
        : '[geo] sem internet e sem posição em cache: mapa em grade tática local\n',
    });
  });
  locationRefreshTimer = setInterval(() => {
    void resolveNetworkLocation();
  }, LOCATION_REFRESH_INTERVAL_MS);
  locationRefreshTimer.unref?.();
}

/**
 * Where the station is, without Chromium's geolocation.
 *
 * `navigator.geolocation` in Electron resolves through Chromium's network
 * location provider, which needs a Google API key compiled into the build. The
 * stock binary does not have one, so the browser API can report a permission
 * the user granted and then never produce a fix. This is the honest fallback:
 * the host's own public address, resolved to a city-level coordinate, and
 * labelled as coarse all the way to the screen.
 *
 * The live network answer when there is a route; otherwise the cached one,
 * marked as such, so the map keeps the operator's real city even on the
 * aircraft's own Wi-Fi.
 */
ipcMain.handle('bmg:get-host-location', async () => {
  const live = await resolveNetworkLocation();
  if (live) {
    return {
      success: true,
      latitude: live.latitude,
      longitude: live.longitude,
      accuracyM: live.accuracyM,
      city: live.city,
      region: live.region,
      cached: false,
      cachedAt: live.at,
    };
  }

  const cached = readLocationCache();
  if (cached) {
    return {
      success: true,
      latitude: cached.latitude,
      longitude: cached.longitude,
      accuracyM: Number(cached.accuracyM) || 5000,
      city: cached.city ?? null,
      region: cached.region ?? null,
      cached: true,
      cachedAt: Number(cached.at) || null,
      cachedSource: cached.source === 'device' ? 'device' : 'ip',
    };
  }

  return { success: false, error: 'sem rota para o serviço de geolocalização e sem posição em cache' };
});

/** A device-level fix from the renderer, kept for the next time there is no internet. */
ipcMain.handle('bmg:save-operator-location', async (_event, payload = {}) => {
  const latitude = Number(payload.latitude);
  const longitude = Number(payload.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
    return { success: false, error: 'coordenadas inválidas' };
  }
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    return { success: false, error: 'coordenadas fora do intervalo' };
  }
  const previous = readLocationCache();
  const entry = {
    latitude,
    longitude,
    accuracyM: Number(payload.accuracyM) || 100,
    city: previous?.city ?? null,
    region: previous?.region ?? null,
    source: 'device',
    at: Date.now(),
  };
  return { success: writeLocationCache(entry) };
});

// -----------------------------------------------------------------------------
// IPC: diagnostics terminal
//
// A real interactive bash on a pseudo-terminal, in the mission's own
// environment (the rcfile sources nectar-activate), so `ros2 topic echo
// /bebop/odom` typed here sees exactly the graph the aircraft is on and Tab
// completes the way it does in any ROS terminal. Bytes flow raw both ways; see
// electron/terminal.cjs. The emergency `land` shortcut is caught in the
// renderer before a byte reaches the PTY.
// -----------------------------------------------------------------------------
const terminalHost = createTerminalHost({
  loadPty: () => require('node-pty'),
  env: getNectarEnv,
  cwd: TERMINAL_HOME,
  activator: NECTAR_ACTIVATOR,
  send,
});

ipcMain.handle('bmg:terminal-spawn', async (_event, options = {}) => terminalHost.spawn(options));
ipcMain.handle('bmg:terminal-write', async (_event, payload = {}) =>
  terminalHost.write(payload.id, payload.data)
);
ipcMain.handle('bmg:terminal-resize', async (_event, payload = {}) =>
  terminalHost.resize(payload.id, payload.cols, payload.rows)
);
ipcMain.handle('bmg:terminal-kill', async (_event, id) => terminalHost.kill(id));

/**
 * The copilot's output level.
 *
 * Held here as well as pushed down, because the daemon is started lazily and on
 * demand: a volume set on the pre-flight screen has to survive until the first
 * sentence brings the process up, and has to be reapplied if that process is
 * ever replaced.
 */
let voiceVolume = 1;
let voiceMuted = false;

function pushVoiceLevel() {
  if (!speechProcess) return false;
  try {
    speechProcess.stdin.write(
      JSON.stringify({ op: 'level', volume: voiceVolume, muted: voiceMuted }) + '\n'
    );
    return true;
  } catch (err) {
    return false;
  }
}

ipcMain.handle('bmg:set-voice-level', async (_event, payload = {}) => {
  if (typeof payload.volume === 'number' && Number.isFinite(payload.volume)) {
    voiceVolume = Math.max(0, Math.min(1, payload.volume));
  }
  if (typeof payload.muted === 'boolean') voiceMuted = payload.muted;
  // Not an error when the copilot is not up yet: the level is applied to it on
  // the handshake, so the setting is never silently lost.
  pushVoiceLevel();
  return { success: true, volume: voiceVolume, muted: voiceMuted };
});

ipcMain.handle('bmg:get-voice-level', async () => ({ volume: voiceVolume, muted: voiceMuted }));

// -----------------------------------------------------------------------------
// IPC: mission lifecycle
// -----------------------------------------------------------------------------
/**
 * Spawn `mission.py` with the operator's parameters.
 *
 * Shared by the launch command and by bench mode, which is the same mission
 * process with `--no-fly` and a stage subset, so a rehearsal cannot diverge
 * from the flight it is rehearsing.
 */
async function startMissionProcess(options = {}) {
  if (missionProcess) return { success: false, message: 'Uma missão já está em andamento.' };
  if (pendingBenchStage) return { success: false, message: 'Uma rotina de bancada está em contagem regressiva.' };

  // Single voice: the station's copilot is the only speaker in the system. The
  // flag tells mission.py to build no audio player of its own and to hand its
  // failures over as `[ALERT ...]` lines (`announcer.station_narrates`). Set
  // here and only here: the speech daemon is this process's own voice and
  // must keep its player.
  const env = { ...getNectarEnv(), BMG_GCS_SESSION: '1' };
  const scriptPath = path.join(MISSION_DIR, 'mission.py');
  const args = [scriptPath];

  // `--params-json` carries the whole document and is applied first; the flags
  // below then override individual fields, matching the order in
  // `mission.py:main`.
  if (options.paramsJson) {
    args.push('--params-json', typeof options.paramsJson === 'string'
      ? options.paramsJson
      : JSON.stringify(options.paramsJson));
  }

  const push = (flag, value) => {
    if (value !== undefined && value !== null && value !== '') args.push(flag, String(value));
  };

  push('--countdown', options.countdown ?? 0);
  push('--height', options.height ?? options.targetAltitude);
  push('--velocity', options.velocity ?? options.speed ?? options.forwardSpeed);
  push('--rtl-velocity', options.rtlVelocity);
  push('--search-timeout', options.searchTimeout ?? options.search_timeout_sec);
  push('--hover-duration', options.hoverDuration ?? options.hoverTime ?? options.hover_duration_sec);
  push('--confidence', options.confidence ?? options.confidence_threshold);
  push('--arrival-radius', options.arrivalRadius ?? options.arrival_radius_m);
  push('--model-path', options.modelPath);
  push('--ip', options.ip);
  push('--detection-topic', options.detectionTopic);

  // Bench mode runs one routine rather than the sequence. The selection goes to
  // `mission.py --stages`, which takes it literally and in order.
  if (Array.isArray(options.stages) && options.stages.length) {
    push('--stages', options.stages.join(','));
  } else if (typeof options.stages === 'string' && options.stages.trim()) {
    push('--stages', options.stages.trim());
  }

  // Arming is always stated explicitly. `mission.py` deliberately keeps
  // `no_fly` out of what it writes back to `mission_config.json`, so the file
  // can carry a stale `true` from an earlier bench run; relying on
  // `--params-json` alone to clear it meant one malformed payload silently
  // grounded a real flight.
  args.push(options.noFly ? '--no-fly' : '--fly');

  // Bring the copilot up alongside the mission rather than on its first line.
  // Starting Python, importing `google.genai` and opening a Live session costs
  // seconds; paid here they overlap the countdown, paid on demand they land on
  // the takeoff call and the aircraft is already climbing before it is spoken.
  ensureSpeechProcess();

  try {
    missionProcess = spawn(NECTAR_ACTIVATOR, ['python3', ...args], { cwd: MISSION_DIR, env });
    missionStartedAt = Date.now();

    // Flight-script milestones travel on their own channel rather than on
    // `bmg:step-change`, which stays the five-stage contract it has always
    // been. A bench routine is one stage out of its script, so only a full
    // launch raises the two milestones this process owns.
    const forwardMilestone = (message) => send('bmg:milestone', message);
    const milestones = createMilestoneParser(forwardMilestone);
    const fullScript = !args.includes('--stages');
    const cancelScriptMilestones = fullScript
      ? scheduleScriptMilestones(Number(options.countdown ?? 0), forwardMilestone)
      : () => {};

    recordLog('mission', {
      type: 'stdout',
      text:
        `[BMG] Ambiente: nectar-activate (${env.VIRTUAL_ENV || '/home/joaomoreira/ros2_ws/.venv'})\n` +
        `[BMG] ROS 2 ${env.ROS_DISTRO || 'jazzy'} · domínio ${env.ROS_DOMAIN_ID || '14'}\n` +
        `[BMG] Armamento: ${options.noFly ? 'BANCADA (--no-fly, motores inertes)' : 'VOO REAL (--fly)'}\n` +
        `[BMG] Comando: python3 ${args.map((a) => (a.length > 120 ? `${a.slice(0, 117)}...` : a)).join(' ')}\n`,
    });

    missionProcess.stdout.on('data', (data) => {
      const text = data.toString();
      recordLog('mission', { type: 'stdout', text });
      milestones.push(text);

      // The stage machine. `mission.py` logs `--- [STEP N: ...] ---` from each
      // step's `execute`, on stdout, which is what makes this reliable.
      const match = /\[STEP ([1-5]):/.exec(text);
      if (match) {
        const stepNumber = Number(match[1]);
        send('bmg:step-change', { stepNumber, stepName: STEP_NAMES[stepNumber] });
      }
    });

    missionProcess.stderr.on('data', (data) => {
      recordLog('mission', { type: 'stderr', text: data.toString() });
    });

    missionProcess.on('close', (code, signal) => {
      milestones.flush();
      cancelScriptMilestones();
      missionProcess = null;
      missionStartedAt = null;
      recordLog('mission', { type: 'exit', text: `[BMG] Missão finalizada com código ${code}\n` });
      send('bmg:mission-exit', { code, signal });
    });

    return { success: true, pid: missionProcess.pid, argv: args };
  } catch (error) {
    missionProcess = null;
    missionStartedAt = null;
    return { success: false, error: error.message };
  }
}

ipcMain.handle('bmg:start-mission', async (_event, options = {}) => startMissionProcess(options));

/**
 * Run one stage on the bench.
 *
 * Always `--no-fly`: the point of the panel is to exercise a routine with the
 * motors inert, and an operator picking a stage from a bench control has not
 * asked for a flight.
 */
ipcMain.handle('bmg:start-bench-stage', async (_event, options = {}) => {
  const stage = Number(options.stage);
  if (!Number.isInteger(stage) || stage < 1 || stage > 5) {
    return { success: false, error: `Etapa inválida: ${options.stage}` };
  }
  cancelPendingBenchStage();

  const spawnOptions = { ...options, stages: [stage], noFly: true, countdown: 0 };
  const countdown = Math.max(0, Number(options.countdown) || 0);
  if (countdown <= 0) return startMissionProcess(spawnOptions);

  // A later stage has no countdown hold of its own, so the station counts and
  // spawns at zero; see electron/benchCountdown.cjs.
  ensureSpeechProcess();
  pendingBenchStage = deferBenchSpawn(countdown, {
    emit: (message) => send('bmg:milestone', message),
    spawn: async () => {
      pendingBenchStage = null;
      const result = await startMissionProcess(spawnOptions);
      if (!result.success) {
        recordLog('mission', {
          type: 'stderr',
          text: `[BMG] A rotina de bancada não subiu: ${result.error ?? result.message ?? 'erro desconhecido'}\n`,
        });
        send('bmg:mission-exit', { code: -1, signal: null });
      }
    },
  });
  recordLog('mission', {
    type: 'stdout',
    text: `[BMG] Rotina de bancada da etapa ${stage} em contagem regressiva (${countdown} s).\n`,
  });
  return { success: true, deferred: true, stage };
});

/** Drop a bench routine still counting down, with its countdown milestones. */
function cancelPendingBenchStage() {
  if (!pendingBenchStage) return false;
  const cancel = pendingBenchStage;
  pendingBenchStage = null;
  return cancel();
}

const STEP_NAMES = {
  1: 'Decolagem',
  2: 'Varredura',
  3: 'Acidente detectado',
  4: 'Inspeção',
  5: 'Retornando base',
};

ipcMain.handle('bmg:get-mission-status', async () => ({
  running: Boolean(missionProcess),
  pid: missionProcess ? missionProcess.pid : null,
  startedAt: missionStartedAt,
}));

/**
 * Abort: three independent paths to the ground.
 *
 * The fast path is the SIGINT below. The mission process already holds an
 * initialised ROS 2 node, so its own landing burst reaches the aircraft in
 * milliseconds. The two publishes here are the backup for when there is no
 * mission process to signal, or when it is wedged.
 *
 * `-w 0` is load-bearing. Without it `--once` waits for a matching
 * subscription and blocks indefinitely when the driver is down. The timeout is
 * generous because a cold `ros2 topic pub` spends about seven seconds starting
 * Python and completing discovery before it publishes at all -- a shorter one
 * killed the command just before it sent anything.
 */
const LAND_PUB = 'ros2 topic pub --once -w 0 /bebop/land std_msgs/msg/Empty "{}"';
const STOP_PUB = 'ros2 topic pub --once -w 0 /bebop/cmd_vel geometry_msgs/msg/Twist "{}"';
const PUB_TIMEOUT_MS = 20000;

/**
 * Bring the mission process down and wait until it is actually gone.
 *
 * The previous shape of this — SIGINT, sleep a fixed interval, then clear the
 * handle regardless — had two failure modes, both of which end with two
 * processes commanding one aircraft. A mission that took longer than the sleep
 * to die became unreferenced while still running, so the guard in
 * `startMissionProcess` let a second one spawn alongside it and both published
 * to /bebop/cmd_vel. And a mission wedged past SIGINT could never be escalated,
 * because by the time anything thought to try, the handle was null.
 *
 * So: signal, wait for the close event, escalate to SIGKILL, and only clear the
 * handle once the process has reported that it is gone.
 */
function stopMissionProcess(grace = 1200) {
  if (cancelPendingBenchStage()) {
    recordLog('mission', { type: 'stdout', text: '[BMG] Rotina de bancada cancelada antes de iniciar.\n' });
  }
  const child = missionProcess;
  if (!child) return Promise.resolve(false);

  return new Promise((resolve) => {
    let escalation = null;

    const settle = () => {
      if (escalation !== null) clearTimeout(escalation);
      // Only this handle is cleared. A restart that already replaced it must
      // not be dropped by the exit of the process it replaced.
      if (missionProcess === child) {
        missionProcess = null;
        missionStartedAt = null;
      }
      resolve(true);
    };

    child.once('close', settle);

    try {
      child.kill('SIGINT');
    } catch (e) {
      // Already gone; the close event has fired or is about to.
      return settle();
    }

    escalation = setTimeout(() => {
      recordLog('mission', {
        type: 'stderr',
        text: `[BMG] Missão não respondeu ao SIGINT em ${grace} ms: enviando SIGKILL.\n`,
      });
      try { child.kill('SIGKILL'); } catch (e) { /* already gone */ }
      // The close event still arrives and settles this; this is only the
      // backstop for a child whose handle never reports at all.
      setTimeout(settle, 1500).unref?.();
    }, grace);
  });
}

/** ARSDK flying states in which the airframe is off the ground. */
const AIRBORNE_FLYING_STATES = new Set([1, 2, 3, 4, 6, 8]);

/**
 * End a mission that is finishing on its own.
 *
 * Distinct from abort: the emergency land burst is only sent if the aircraft is
 * still reporting an airborne flying state. Firing it unconditionally on a
 * completed mission, as the renderer used to, commanded a landing at an
 * airframe that had already cut its motors.
 */
ipcMain.handle('bmg:end-mission', async () => {
  const env = getNectarEnv();
  const missionKilled = await stopMissionProcess(1200);

  const airborne = AIRBORNE_FLYING_STATES.has(latestTelemetry.flying_state);
  if (airborne) {
    recordLog('mission', { type: 'stderr', text: '[BMG] Aeronave ainda no ar: enviando pouso.\n' });
    sendCommand({ op: 'land' });
    exec(STOP_PUB, { env, timeout: PUB_TIMEOUT_MS }, () => {});
    exec(LAND_PUB, { env, timeout: PUB_TIMEOUT_MS }, () => {});
  } else {
    recordLog('mission', { type: 'stdout', text: '[BMG] Missão encerrada. Aeronave em solo.\n' });
  }

  // Everything the last flight left running on the host. The copilot is
  // silenced rather than shut down -- a half-read forensic report talking over
  // the next pre-flight is the exact thing "Finalizar missão" is for -- and the
  // gimbal bridge goes with it so the next mission's first tilt command comes
  // from a participant that saw the current network.
  //
  // Called twice in a row this changes nothing the second time: every step
  // below is a no-op against state that is already clear.
  if (speechProcess) {
    try {
      speechProcess.stdin.write(JSON.stringify({ op: 'cancel' }) + '\n');
    } catch (e) { /* the pipe is already gone */ }
  }
  // The command bridge stays up. It is supervised with the other bridges and
  // holding it open is what keeps the next abort instant; only the reading it
  // was carrying belongs to the flight that just ended.
  lastCameraTilt = null;

  send('bmg:mission-reset', { at: Date.now(), missionKilled, landCommanded: airborne });

  return { success: true, missionKilled, landCommanded: airborne, reset: true };
});

ipcMain.handle('bmg:abort-mission', async () => {
  const env = getNectarEnv();

  // The landing goes out first and through the resident bridge, which holds a
  // live participant and publishes in microseconds. `ros2 topic pub` still runs
  // behind it as the backstop for a bridge that is down, but it is no longer on
  // the critical path: a cold CLI spends about seven seconds starting Python
  // and completing discovery before it puts anything on the wire, and an
  // emergency landing that arrives seven seconds after it was demanded is not
  // an emergency landing.
  const bridged = sendCommand({ op: 'land' });
  recordLog('mission', {
    type: 'stderr',
    text: bridged
      ? '[BMG] Aborto: pouso publicado imediatamente pela ponte de comando.\n'
      : '[BMG] Aborto: ponte de comando indisponível, usando ros2 topic pub.\n',
  });

  exec(STOP_PUB, { env, timeout: PUB_TIMEOUT_MS }, () => {});
  exec(LAND_PUB, { env, timeout: PUB_TIMEOUT_MS }, (err) => {
    if (err) console.warn('[BMG] Direct land publish failed:', err.message);
  });

  const hadMission = Boolean(missionProcess);
  const missionKilled = await stopMissionProcess(900);

  if (!hadMission) {
    // No mission process to have said it on the way down, so the station says
    // it. Routed through the resident copilot rather than a throwaway
    // interpreter: a second announcer would contend with it for the one audio
    // device, and pay the cold-start cost to do it.
    const copilot = ensureSpeechProcess();
    if (copilot) {
      try {
        speechSeq += 1;
        copilot.stdin.write(
          JSON.stringify({
            id: speechSeq,
            text: 'Missão abortada. Pouso imediato comandado.',
            priority: 'URGENT',
            verbatim: true,
          }) + '\n'
        );
      } catch (voiceErr) {
        console.warn('[BMG] Voice announcer unavailable:', voiceErr.message);
      }
    }
  }

  exec(LAND_PUB, { env, timeout: PUB_TIMEOUT_MS }, () => {});

  recordLog('mission', { type: 'stderr', text: '[BMG] Aborto comandado. Pouso enviado em /bebop/land.\n' });
  return { success: true, missionKilled };
});

// -----------------------------------------------------------------------------
// IPC: forensic evidence
// -----------------------------------------------------------------------------
const STAMP_RE = /^(\d{8}_\d{6})$/;

function stampToMs(stamp) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(stamp);
  if (!m) return 0;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
}

/**
 * Enumerate captures by joining the three files the mission writes for one
 * event: the lossless raw PNG, the annotated JPEG, and the metadata sidecar,
 * all sharing a single `%Y%m%d_%H%M%S` stamp.
 */
ipcMain.handle('bmg:list-evidence', async () => {
  try {
    if (!fs.existsSync(MISSION_DIR)) {
      return { success: false, items: [], error: `Diretório da missão não encontrado: ${MISSION_DIR}` };
    }

    const byStamp = new Map();
    const ensure = (stamp) => {
      if (!byStamp.has(stamp)) {
        byStamp.set(stamp, {
          id: stamp,
          stamp,
          capturedAtMs: stampToMs(stamp),
          rawUrl: null,
          annotatedUrl: null,
          metadata: null,
        });
      }
      return byStamp.get(stamp);
    };

    for (const filename of fs.readdirSync(MISSION_DIR)) {
      let match;
      if ((match = /^accident_raw_(\d{8}_\d{6})\.png$/.exec(filename))) {
        ensure(match[1]).rawUrl = `/evidence/${filename}`;
      } else if ((match = /^accident_inspected_(\d{8}_\d{6})\.jpg$/.exec(filename))) {
        ensure(match[1]).annotatedUrl = `/evidence/${filename}`;
      } else if ((match = /^accident_metadata_(\d{8}_\d{6})\.json$/.exec(filename))) {
        try {
          ensure(match[1]).metadata = JSON.parse(
            fs.readFileSync(path.join(MISSION_DIR, filename), 'utf-8')
          );
        } catch (e) {
          console.warn('[BMG] Unreadable sidecar', filename, e.message);
        }
      } else if ((match = /^accident_capture_(\d{8}_\d{6})\.jpg$/.exec(filename))) {
        // Single-fidelity captures from earlier builds. Listed so nothing on
        // disk is invisible to the operator.
        const entry = ensure(match[1]);
        if (!entry.annotatedUrl) entry.annotatedUrl = `/evidence/${filename}`;
      }
    }

    const items = Array.from(byStamp.values())
      .filter((i) => i.rawUrl || i.annotatedUrl)
      .sort((a, b) => b.capturedAtMs - a.capturedAtMs);

    return { success: true, items };
  } catch (err) {
    return { success: false, items: [], error: err.message };
  }
});

/**
 * Write a self-contained dossier.
 *
 * The renderer supplies the document with image placeholders; the bytes are
 * inlined here so the exported file carries the evidence rather than pointing
 * at a path that will not survive being emailed.
 */
ipcMain.handle('bmg:export-dossier', async (_event, payload = {}) => {
  const { html, stamp } = payload;
  if (typeof html !== 'string' || !STAMP_RE.test(String(stamp || ''))) {
    return { success: false, error: 'Pedido de exportação inválido' };
  }

  const toDataUri = (filename, mime) => {
    const full = path.join(MISSION_DIR, filename);
    if (!fs.existsSync(full)) return '';
    try {
      return `data:${mime};base64,${fs.readFileSync(full).toString('base64')}`;
    } catch (e) {
      console.warn('[BMG] Could not inline', filename, e.message);
      return '';
    }
  };

  const rawUri = toDataUri(`accident_raw_${stamp}.png`, 'image/png');
  const annotatedUri =
    toDataUri(`accident_inspected_${stamp}.jpg`, 'image/jpeg') ||
    toDataUri(`accident_capture_${stamp}.jpg`, 'image/jpeg');

  const document = html
    .replace('{{RAW_IMAGE}}', rawUri || annotatedUri)
    .replace('{{ANNOTATED_IMAGE}}', annotatedUri || rawUri);

  const suggested = path.join(
    app.getPath('documents') || os.homedir(),
    `dossie_pericial_${stamp}.html`
  );

  const result = await dialog.showSaveDialog(mainWindow, {
    title: 'Exportar dossiê pericial',
    defaultPath: suggested,
    filters: [{ name: 'Documento HTML', extensions: ['html'] }],
  });

  if (result.canceled || !result.filePath) return { success: false, error: 'cancelled' };

  try {
    fs.writeFileSync(result.filePath, document, 'utf-8');
    return { success: true, path: result.filePath };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('bmg:reveal-path', async (_event, name) => {
  const target = name && STAMP_RE.test(String(name))
    ? path.join(MISSION_DIR, `accident_raw_${name}.png`)
    : MISSION_DIR;
  try {
    if (fs.existsSync(target)) shell.showItemInFolder(target);
    else await shell.openPath(MISSION_DIR);
    return { success: true };
  } catch {
    return { success: false };
  }
});

// -----------------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------------
let cleanedUp = false;

/**
 * Tear every child down before the app exits.
 *
 * `make driver-stop` has to run synchronously here. Fired asynchronously on
 * `before-quit`, Electron regularly exited first and left `ros2 launch` and the
 * `bebop_driver` node holding the aircraft's ARSDK session, so the next launch
 * of the GCS could not connect at all.
 */
function cleanupAllProcesses() {
  if (cleanedUp) return;
  cleanedUp = true;

  if (missionProcess) {
    try { missionProcess.kill('SIGINT'); } catch (e) { /* already exited */ }
    missionProcess = null;
    missionStartedAt = null;
  }

  stopBridgeWatchdog();
  stopTelemetryWatchdog();
  stopSpeechProcess();
  terminalHost.killAll();
  if (locationRefreshTimer) clearInterval(locationRefreshTimer);

  // Capture the bridge pids before the handles are cleared: the SIGKILL
  // escalation inside `stopBackgroundServices` runs on an unref'd timer, which
  // never fires because the app exits first. A bridge that ignores SIGTERM then
  // outlives the GCS and holds port 9090 against the next launch.
  const bridgePids = [mjpegProcess, telemetryProcess]
    .filter(Boolean)
    .map((proc) => proc.pid)
    .filter(Boolean);

  stopBackgroundServices();

  if (driverProcess) {
    try { driverProcess.kill('SIGTERM'); } catch (e) { /* already exited */ }
    driverProcess = null;
  }

  try {
    execSync('make driver-stop', {
      cwd: NECTAR_SDK_DIR,
      env: getNectarEnv(),
      timeout: 12000,
      stdio: 'ignore',
    });
  } catch (err) {
    console.warn('[BMG] make driver-stop failed on shutdown:', err.message);
  }

  // `make driver-stop` above is synchronous and takes seconds, which is all the
  // grace a bridge needs to act on its SIGTERM. Anything still alive now is
  // wedged, and is taken down before this process goes.
  for (const pid of bridgePids) {
    try {
      process.kill(pid, 0);
      process.kill(pid, 'SIGKILL');
      console.warn(`[BMG] Bridge ${pid} ignored SIGTERM; killed on shutdown.`);
    } catch (e) { /* already gone, which is the expected case */ }
  }

  // Then sweep by script path. Handle bookkeeping cannot be trusted here: a
  // bridge recycled mid-session leaves the previous handle behind, and the pid
  // captured above is not necessarily the process still holding port 9090.
  // Matching on the paths this app owns is the only check that cannot drift.
  reapOrphanedServices();

  if (server) {
    try { server.close(); } catch (e) { /* already closed */ }
    server = null;
  }
}

app.whenReady().then(async () => {
  const port = await createStaticServer();
  startBackgroundServices();
  startBridgeWatchdog();
  startTelemetryWatchdog();
  startLocationRefresh();
  createWindow(port);

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(port);
  });
});

app.on('window-all-closed', () => {
  cleanupAllProcesses();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', cleanupAllProcesses);
