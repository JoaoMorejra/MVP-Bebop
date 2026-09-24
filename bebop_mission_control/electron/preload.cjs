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
  missionDir: '/home/joaomoreira/ros2_ws/src/mvp_mission_bebop/mvp_mission_bebop',

  // Wi-Fi link to the aircraft
  scanWifi: (options) => ipcRenderer.invoke('bmg:scan-wifi', options),
  connectWifi: (ssid) => ipcRenderer.invoke('bmg:connect-wifi', ssid),

  // ROS 2 driver lifecycle
  startDriver: () => ipcRenderer.invoke('bmg:start-driver'),
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
  setVoiceLevel: (level = {}) => ipcRenderer.invoke('bmg:set-voice-level', level),
  getVoiceLevel: () => ipcRenderer.invoke('bmg:get-voice-level'),

  // Camera gimbal, in real time
  setCameraTilt: (degrees, pan = 0) =>
    ipcRenderer.invoke('bmg:camera-tilt', { tilt: degrees, pan }),
  getCameraTilt: () => ipcRenderer.invoke('bmg:get-camera-tilt'),
  getHostLocation: () => ipcRenderer.invoke('bmg:get-host-location'),
  saveOperatorLocation: (location) => ipcRenderer.invoke('bmg:save-operator-location', location),
  gotoStage: (stage) => ipcRenderer.invoke('bmg:goto-stage', { stage }),

  // Parameters
  getParameters: () => ipcRenderer.invoke('bmg:get-parameters'),
  getDefaultParameters: () => ipcRenderer.invoke('bmg:get-default-parameters'),
  saveParameters: (params) => ipcRenderer.invoke('bmg:save-parameters', params),

  // System state
  getEnvInfo: () => ipcRenderer.invoke('bmg:get-env-info'),
  getTelemetry: () => ipcRenderer.invoke('bmg:get-telemetry'),
  getStreamStatus: () => ipcRenderer.invoke('bmg:get-stream-status'),
  getDiagnostics: () => ipcRenderer.invoke('bmg:get-diagnostics'),
  getLogHistory: () => ipcRenderer.invoke('bmg:get-log-history'),

  // Diagnostics terminal: a shell in the mission's own environment. Output
  // streams on `bmg:terminal-output`; the promise resolves when it exits.
  terminalExec: (command, id) => ipcRenderer.invoke('bmg:terminal-exec', { command, id }),
  terminalKill: (id) => ipcRenderer.invoke('bmg:terminal-kill', id),
  getTerminalInfo: () => ipcRenderer.invoke('bmg:terminal-info'),

  // Forensic evidence
  listEvidence: () => ipcRenderer.invoke('bmg:list-evidence'),
  exportDossier: (payload) => ipcRenderer.invoke('bmg:export-dossier', payload),
  revealPath: (name) => ipcRenderer.invoke('bmg:reveal-path', name),

  // Events
  onTelemetryUpdate: subscribe('bmg:telemetry-update'),
  onStepChange: subscribe('bmg:step-change'),
  // Flight-script milestones, from mission.py stdout and from the launch itself.
  onMilestone: subscribe('bmg:milestone'),
  onRawEvidenceReady: subscribe('bmg:raw-evidence-ready'),
  onMissionLog: subscribe('bmg:mission-log'),
  onDriverLog: subscribe('bmg:driver-log'),
  onMissionExit: subscribe('bmg:mission-exit'),
  onLinkProgress: subscribe('bmg:link-progress'),
  onAnnounceDone: subscribe('bmg:announce-done'),
  onCameraTiltChanged: subscribe('bmg:camera-tilt-changed'),
  onMissionReset: subscribe('bmg:mission-reset'),
  onTerminalOutput: subscribe('bmg:terminal-output'),
});
