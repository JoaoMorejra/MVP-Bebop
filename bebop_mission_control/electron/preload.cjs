const path = require('path');
const { contextBridge, ipcRenderer } = require('electron');

/**
 * The only surface the renderer has on the host. Everything is an explicit
 * method — no generic `invoke` passthrough — so the set of things the interface
 * can ask the system to do is enumerable here.
 */

/** Wraps an ipcRenderer subscription so callers get an unsubscribe function. */
const subscribe = (channel) => (callback) => {
  const handler = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};

contextBridge.exposeInMainWorld('bmgAPI', {
  isElectron: true,
  missionDir: path.resolve(__dirname, '..', '..', 'mvp_mission_bebop'),

  // Wi-Fi link to the aircraft and notebook network management
  scanWifi: (options) => ipcRenderer.invoke('bmg:scan-wifi', options),
  connectWifi: (ssid, password) =>
    ipcRenderer.invoke(
      'bmg:connect-wifi',
      typeof ssid === 'object' && ssid !== null ? ssid : { ssid, password }
    ),
  disconnectWifi: () => ipcRenderer.invoke('bmg:disconnect-wifi'),
  getWifiPower: () => ipcRenderer.invoke('bmg:get-wifi-power'),
  setWifiPower: (enabled) => ipcRenderer.invoke('bmg:set-wifi-power', { enabled }),

  // ROS 2 driver lifecycle
  startDriver: (options = {}) => ipcRenderer.invoke('bmg:start-driver', options),
  stopDriver: () => ipcRenderer.invoke('bmg:stop-driver'),
  checkDriverStatus: () => ipcRenderer.invoke('bmg:check-driver-status'),

  // Deterministic bring-up: join the network, start the driver and prove the
  // aircraft's topics are exchanging data, reporting which stage failed.
  ensureLink: (options = {}) => ipcRenderer.invoke('bmg:ensure-link', options),
  getLinkReadiness: () => ipcRenderer.invoke('bmg:get-link-readiness'),

  // Autonomous mission
  startMission: (options = {}) => ipcRenderer.invoke('bmg:start-mission', options),
  startBenchStage: (options = {}) =>
    ipcRenderer.invoke(
      'bmg:start-bench-stage',
      typeof options === 'number' ? { stage: options } : options
    ),
  abortMission: () => ipcRenderer.invoke('bmg:abort-mission'),
  endMission: () => ipcRenderer.invoke('bmg:end-mission'),
  getMissionStatus: () => ipcRenderer.invoke('bmg:get-mission-status'),

  // Voice copilot. `announce` resolves as soon as the line is queued; the
  // sentence having actually been heard arrives later on `bmg:announce-done`,
  // which is what the forensic report paces itself against.
  announce: (text, priority = 'NORMAL') =>
    ipcRenderer.invoke(
      'bmg:announce',
      typeof text === 'string' ? { text, priority } : { priority, ...text }
    ),
  cancelSpeech: () => ipcRenderer.invoke('bmg:cancel-speech'),
  prepareSpeech: (text) => ipcRenderer.invoke('bmg:prepare-speech', text),
  cacheSpeech: (texts) => ipcRenderer.invoke('bmg:cache-speech', texts),
  setVoiceLevel: (level = {}) => ipcRenderer.invoke('bmg:set-voice-level', level),
  getVoiceLevel: () => ipcRenderer.invoke('bmg:get-voice-level'),

  // Camera gimbal, in real time
  setCameraTilt: (degrees, pan = 0) =>
    ipcRenderer.invoke('bmg:camera-tilt', { tilt: degrees, pan }),
  getCameraTilt: () => ipcRenderer.invoke('bmg:get-camera-tilt'),
  getHostLocation: () => ipcRenderer.invoke('bmg:get-host-location'),
  saveOperatorLocation: (location) => ipcRenderer.invoke('bmg:save-operator-location', location),
  gotoStage: (stage) => ipcRenderer.invoke('bmg:goto-stage', { stage }),
  // The battery failsafe switch, forwarded to a running mission.
  setBatteryFailsafe: (enabled, thresholdPct) =>
    ipcRenderer.invoke('bmg:set-battery-failsafe', { enabled, thresholdPct }),
  // Keyboard nudge intent, fire-and-forget (main.cjs throttles it).
  sendNudge: (intent) => ipcRenderer.send('bmg:nudge', intent),

  // Parameters
  getParameters: () => ipcRenderer.invoke('bmg:get-parameters'),
  getParameterDefaults: () => ipcRenderer.invoke('bmg:get-parameter-defaults'),
  saveParameters: (params) => ipcRenderer.invoke('bmg:save-parameters', params),

  // System state
  getEnvInfo: () => ipcRenderer.invoke('bmg:get-env-info'),
  getTelemetry: () => ipcRenderer.invoke('bmg:get-telemetry'),
  getStreamStatus: () => ipcRenderer.invoke('bmg:get-stream-status'),
  getDiagnostics: () => ipcRenderer.invoke('bmg:get-diagnostics'),
  getLogHistory: () => ipcRenderer.invoke('bmg:get-log-history'),

  // Events
  onTelemetryUpdate: subscribe('bmg:telemetry-update'),
  onStepChange: subscribe('bmg:step-change'),
  // Flight-script milestones, from mission.py stdout and from the launch itself.
  onMilestone: subscribe('bmg:milestone'),
  onRawEvidenceReady: subscribe('bmg:raw-evidence-ready'),
  onMissionLog: subscribe('bmg:mission-log'),
  onDriverLog: subscribe('bmg:driver-log'),
  onMissionExit: subscribe('bmg:mission-exit'),
  onLaunchRefused: subscribe('bmg:launch-refused'),
  // D8: the cockpit publishes whether its abort is available; the main
  // process then delivers Ctrl/Cmd+C and Escape as `bmg:abort-shortcut`.
  setCockpitAbortArmed: (armed) => ipcRenderer.send('bmg:cockpit-abort-armed', { armed: armed === true }),
  onAbortShortcut: subscribe('bmg:abort-shortcut'),
  // The cockpit publishes whether the nudge may act; the main process then
  // takes W A S D Q R before the page (the video iframe swallows them) and
  // delivers them as `bmg:nudge-key` ({type, code}).
  setCockpitNudgeArmed: (armed) => ipcRenderer.send('bmg:cockpit-nudge-armed', { armed: armed === true }),
  onNudgeKey: subscribe('bmg:nudge-key'),
  onLinkProgress: subscribe('bmg:link-progress'),
  onLandProgress: subscribe('bmg:land-progress'),
  onAnnounceDone: subscribe('bmg:announce-done'),
  onAnnounceStart: subscribe('bmg:announce-start'),
  onCameraTiltChanged: subscribe('bmg:camera-tilt-changed'),
  onMissionReset: subscribe('bmg:mission-reset'),
});
