/**
 * The renderer half of the Electron bridge contract.
 *
 * Every member here is backed by a handler in `electron/main.cjs` and exposed
 * through `electron/preload.cjs`. Nothing in the UI may invent data that does
 * not originate from one of these calls or events.
 */

export interface WifiNetwork {
  ssid: string;
  signal: number;
  active: boolean;
  isBebop: boolean;
  /** The network asks for a password; joining it needs a saved profile. */
  secure?: boolean;
}

export interface WifiScanResult {
  networks: WifiNetwork[];
  currentSsid: string;
  connectedToBebop: boolean;
}

/** One topic of the driver contract, as the telemetry bridge observes it. */
export interface TopicHealth {
  /** `out` — the driver publishes it; `in` — the driver subscribes to it. */
  direction: 'in' | 'out';
  publishers: number;
  subscribers: number;
  /** The topic exists in the ROS 2 graph with an endpoint on the driver side. */
  present: boolean;
  /** Data is actually flowing, not merely advertised. */
  receiving: boolean;
  /** Seconds since the last sample, or null when none has ever arrived. */
  age_sec: number | null;
}

/** Shape emitted by `streamer/telemetry_bridge.py` on `BMG_TELEM:` lines. */
export interface BmgTelemetry {
  connected: boolean;
  driver_running: boolean;
  battery_pct: number;
  wifi_ssid: string;
  wifi_signal_dbm: number;
  speed: number;
  altitude: number;
  flight_time_sec: number;
  /** Compass heading in degrees: 0 north, clockwise, [0, 360). */
  heading: number;
  latitude: number;
  longitude: number;

  /** Epoch milliseconds the sample was rendered by the bridge. */
  at?: number;
  drone_ip?: string;
  /**
   * Whether `battery_pct` is a measurement at all. The bridge reports 0 for
   * "never read", so a panel must not show it as a charge level.
   */
  battery_known?: boolean;
  battery_source?: 'aircraft' | 'console' | 'none';
  battery_age_sec?: number | null;
  /** `aircraft` is the drone's own RSSI; `host` is the laptop's radio. */
  signal_source?: 'aircraft' | 'host' | 'none';
  position_source?: 'gps' | 'odometry';
  gps_fix?: boolean;
  /**
   * Raw `/bebop/odom` position, metres, in the driver's frame: x north, y west.
   * Null while odometry is stale. Draw from `east_m`/`north_m` instead.
   */
  odom_x_m?: number | null;
  odom_y_m?: number | null;
  /** The same position as metres east and north of the odometry origin. */
  east_m?: number | null;
  north_m?: number | null;
  /**
   * The real reference the flight is placed against: the aircraft's first GPS
   * fix, or the operator's cached position. False means there is none, and
   * latitude/longitude are zero rather than an invented place.
   */
  base_known?: boolean;
  base_latitude?: number | null;
  base_longitude?: number | null;
  base_source?: 'gps' | 'operator-cache' | 'configured' | 'none';
  /** Last angle commanded on `/bebop/move_camera`, or null before anything has. */
  camera_tilt_deg?: number | null;
  camera_tilt_age_sec?: number | null;
  sonar_altitude?: number | null;
  /** ARSDK FlyingStateChanged, or null while the aircraft has not reported. */
  flying_state?: number | null;
  flying_state_label?: string;
  odom_age_sec?: number | null;
  node_present?: boolean;
  topics?: Record<string, TopicHealth>;
  topics_ready?: boolean;
  /** Full ROS 2 graph as the bridge's own rclpy node observes it. */
  graph?: { nodes: string[]; topics: string[] };
}

/** Health of the MJPEG bridge, as reported by its own `/status`. */
export interface StreamStatus {
  running: boolean;
  /** A frame arrived recently. Distinct from `running`, which is the process. */
  live: boolean;
  frames: number;
  fps: number;
  ageSec: number | null;
  /** Topic the current frames came from. */
  source: string;
  width: number;
  height: number;
}

export interface TopicReadiness {
  topic: string;
  direction: 'in' | 'out';
  present: boolean;
  receiving: boolean;
  publishers: number;
  subscribers: number;
  ageSec: number | null;
}

/** The flight-readiness contract evaluated in `electron/main.cjs`. */
export interface LinkReadiness {
  at: number;
  /** False when no telemetry sample arrived recently; every flag below is then unknown, not false. */
  telemetryFresh: boolean;
  connected: boolean;
  nodePresent: boolean;
  driverRunning: boolean;
  batteryKnown: boolean;
  stream: StreamStatus;
  topics: TopicReadiness[];
  /** Essential topics that are not exchanging data. */
  missing: string[];
  ready: boolean;
}

export type LinkStage =
  | 'wifi'
  | 'ping'
  /** Rebuilding the Python bridges' DDS participants on the current interfaces. */
  | 'bridges'
  | 'driver'
  | 'topics'
  | 'ready'
  | 'error';

export interface LinkProgressEvent {
  stage: LinkStage;
  status: 'running' | 'ok' | 'error';
  message: string;
  at: number;
  missing?: string[];
}

export interface EnsureLinkResult {
  success: boolean;
  stage: LinkStage;
  message: string;
  readiness: LinkReadiness;
  event: LinkProgressEvent;
}

export interface RawEvidence {
  filename: string;
  /** Displayable frame: the original when it exists, the annotated copy otherwise. */
  url: string;
  /** The lossless original, or null when only the annotated copy reached disk. */
  rawUrl?: string | null;
  annotatedUrl?: string;
  timestamp: number;
}

/** One capture as enumerated from the mission working directory. */
export interface EvidenceItem {
  id: string;
  /** `YYYYMMDD_HHMMSS` as written by `MissionContext.record_photographic_evidence`. */
  stamp: string;
  capturedAtMs: number;
  rawUrl: string | null;
  annotatedUrl: string | null;
  metadata: EvidenceMetadata | null;
}

/** Sidecar written by `MissionContext._write_metadata`. */
export interface EvidenceMetadata {
  captured_at_utc?: string;
  timestamp?: string;
  raw_image?: string | null;
  annotated_image?: string | null;
  frame?: { width: number; height: number };
  gimbal_tilt_deg?: number;
  detections?: EvidenceDetection[];
  mission?: {
    no_fly?: boolean;
    elapsed_sec?: number;
    target_classes?: string[];
    confidence_threshold?: number;
  };
  odometry?: {
    x_m?: number;
    y_m?: number;
    relative_altitude_m?: number;
    raw_altitude_m?: number;
    yaw_rad?: number;
    speed_mps?: number;
    launch_origin?: { x_m?: number; y_m?: number };
    ground_reference_m?: number;
  };
}

export interface EvidenceDetection {
  class_name: string;
  class_id: number;
  confidence: number;
  bbox_xyxy: [number, number, number, number] | number[];
  center_px: [number, number] | number[];
  area_px: number;
}

export interface MissionStepEvent {
  stepNumber: number;
  stepName: string;
}

/**
 * One milestone of the flight script, on `bmg:milestone`.
 *
 * `key` is a pool key of the synchronisation table (`lib/copilotPhrases.ts`);
 * it is typed as a string because it crosses a process boundary, and the
 * renderer checks it before use. `payload` is the milestone's JSON detail.
 */
export interface MilestoneEvent {
  /** Narration, or an alert (failure, abort, failsafe) spoken ahead of it. */
  kind: 'milestone' | 'alert';
  key: string;
  payload: Record<string, unknown>;
  at: number;
  source: 'mission' | 'station';
}

export interface MissionExitEvent {
  code: number | null;
  signal: string | null;
}

export interface LogLine {
  type: 'stdout' | 'stderr' | 'exit' | string;
  text: string;
  at?: number;
}

/** Arguments forwarded to `mission.py` by `bmg:start-mission`. */
export interface MissionLaunchOptions {
  countdown?: number;
  noFly?: boolean;
  height?: number;
  velocity?: number;
  rtlVelocity?: number;
  searchTimeout?: number;
  hoverDuration?: number;
  confidence?: number;
  modelPath?: string;
  ip?: string;
  detectionTopic?: string;
  arrivalRadius?: number;
  /** Full `MissionParameters` document, serialised. Applied before the flags. */
  paramsJson?: string;
}

/** How loudly the copilot may interrupt itself. `URGENT` pre-empts the queue. */
export type AnnouncePriority = 'URGENT' | 'HIGH' | 'NORMAL';

export interface AnnounceRequest {
  text: string;
  priority?: AnnouncePriority;
  /** False lets `announcer.py` map the text onto its milestone phrases. Default true. */
  verbatim?: boolean;
  /** Seconds the daemon waits for synthesis before giving up on this line. */
  timeout?: number;
}

/** Acknowledges that the line was queued — not that it has been heard. */
export interface AnnounceResult {
  success: boolean;
  /** Matches the `id` on the `onAnnounceDone` event for this line. */
  id?: number;
  error?: string;
}

/**
 * One line finished playing. `id` is null when the copilot died with a request
 * outstanding, which is a failure the caller must treat as "this will never
 * arrive" rather than waiting on it.
 */
export interface AnnounceDoneEvent {
  id: number | null;
  ok: boolean;
}

/** The copilot's output level, as the operator set it. */
export interface VoiceLevel {
  /** 0–1. Applied to the PCM samples inside `announcer.py`, not to the host mixer. */
  volume: number;
  muted: boolean;
}

export interface CameraTiltResult {
  success: boolean;
  /** Degrees actually published, after the airframe's limits are applied. */
  tilt?: number;
  pan?: number;
  error?: string;
}

export interface CameraTiltEvent {
  tilt: number;
  pan: number;
}

/** The host finished tearing a mission down; the renderer may clear its state. */
export interface MissionResetEvent {
  at: number;
  missionKilled: boolean;
  landCommanded: boolean;
}

/** One rehearsal on the bench: a single stage, motors inert. */
export interface BenchStageOptions extends MissionLaunchOptions {
  /** 1–5, as numbered in `MISSION_STAGES`. */
  stage: number;
}

export interface EnvironmentInfo {
  activated: boolean;
  venvPath: string;
  pythonPath: string;
  rosDistro: string;
  rosDomainId: string;
}

export interface DiagnosticsReport {
  at: number;
  env: EnvironmentInfo;
  missionRunning: boolean;
  driverRunning: boolean;
  streamer: StreamStatus;
  telemetryBridge: { running: boolean };
  nodes: string[];
  topics: string[];
  processes: { pid: number; command: string }[];
  readiness: LinkReadiness;
  missionDir: string;
  configPath: string;
  errors: string[];
}

/** A finished command in the diagnostics terminal. */
export interface TerminalExecResult {
  success: boolean;
  id: string;
  exitCode: number | null;
  signal?: string | null;
  stdout: string;
  stderr: string;
  durationMs?: number;
  /** The shell's working directory after the command (`cd` changes it). */
  cwd: string;
}

export interface TerminalOutputEvent {
  id: string;
  stream: 'stdout' | 'stderr';
  text: string;
}

export interface TerminalInfo {
  cwd: string;
  home: string;
  user: string;
  host: string;
}

/** Battery failsafe: land on its own when the charge reaches this level in flight. */
export interface BatteryFailsafe {
  enabled: boolean;
  /** 10–40 %. */
  thresholdPct: number;
}

export interface ExportResult {
  success: boolean;
  path?: string;
  error?: string;
}

export interface BmgAPI {
  isElectron: true;
  missionDir: string;

  scanWifi: (options?: { forceRescan?: boolean }) => Promise<WifiScanResult>;
  connectWifi: (
    ssid: string
  ) => Promise<{ success: boolean; pingOk?: boolean; message?: string; error?: string }>;

  startDriver: () => Promise<{ success: boolean; pid?: number; message?: string; error?: string }>;
  stopDriver: () => Promise<{ success: boolean }>;
  checkDriverStatus: () => Promise<{ running: boolean }>;

  ensureLink: (options?: { ssid?: string }) => Promise<EnsureLinkResult>;
  getLinkReadiness: () => Promise<LinkReadiness>;

  startMission: (
    options?: MissionLaunchOptions
  ) => Promise<{ success: boolean; pid?: number; message?: string; error?: string; argv?: string[] }>;
  startBenchStage: (
    options: BenchStageOptions | number
  ) => Promise<{ success: boolean; pid?: number; message?: string; error?: string; argv?: string[] }>;
  abortMission: () => Promise<{ success: boolean; missionKilled?: boolean }>;
  /**
   * End the cycle: stop the mission process, land the aircraft if it is still
   * airborne, silence the copilot and drop the gimbal bridge. Idempotent — a
   * second call against an already-clear host changes nothing.
   */
  endMission: () => Promise<{
    success: boolean;
    missionKilled?: boolean;
    landCommanded?: boolean;
    reset?: boolean;
  }>;
  getMissionStatus: () => Promise<{ running: boolean; pid: number | null; startedAt: number | null }>;

  getParameters: () => Promise<{ success: boolean; params?: Record<string, unknown>; error?: string }>;
  saveParameters: (params: unknown) => Promise<{ success: boolean; error?: string }>;
  getDefaultParameters: () => Promise<{ success: boolean; params?: Record<string, unknown>; error?: string }>;

  /** Speak one line. Resolves when it is queued; `onAnnounceDone` says when it was heard. */
  announce: (text: string | AnnounceRequest, priority?: AnnouncePriority) => Promise<AnnounceResult>;
  cancelSpeech: () => Promise<{ success: boolean; cancelled?: boolean; error?: string }>;
  setVoiceLevel: (level: Partial<VoiceLevel>) => Promise<{ success: boolean } & VoiceLevel>;
  getVoiceLevel: () => Promise<VoiceLevel>;

  setCameraTilt: (degrees: number, pan?: number) => Promise<CameraTiltResult>;
  getCameraTilt: () => Promise<{ tilt: number | null }>;
  /** Ask the running mission to continue at `stage`. The mission may refuse. */
  gotoStage: (stage: number) => Promise<{ success: boolean; stage?: number; error?: string }>;
  /** Coarse, city-level host position. The fallback when Chromium cannot produce a fix. */
  getHostLocation: () => Promise<{
    success: boolean;
    latitude?: number;
    longitude?: number;
    accuracyM?: number;
    city?: string | null;
    region?: string | null;
    /** True when there was no route and this is the position cached on disk. */
    cached?: boolean;
    cachedAt?: number | null;
    cachedSource?: 'device' | 'ip';
    error?: string;
  }>;
  /** Persist a device-level fix so it survives the station losing internet. */
  saveOperatorLocation: (location: {
    latitude: number;
    longitude: number;
    accuracyM?: number;
  }) => Promise<{ success: boolean; error?: string }>;

  getEnvInfo: () => Promise<EnvironmentInfo>;
  getTelemetry: () => Promise<BmgTelemetry>;
  getStreamStatus: () => Promise<StreamStatus>;
  getDiagnostics: () => Promise<DiagnosticsReport>;
  getLogHistory: () => Promise<{ mission: LogLine[]; driver: LogLine[] }>;

  /** Run one shell command; output also streams on `onTerminalOutput`. */
  terminalExec: (command: string, id: string) => Promise<TerminalExecResult>;
  /** Interrupt a running command (Ctrl+C). */
  terminalKill: (id: string) => Promise<{ success: boolean }>;
  getTerminalInfo: () => Promise<TerminalInfo>;

  listEvidence: () => Promise<{ success: boolean; items: EvidenceItem[]; error?: string }>;
  exportDossier: (payload: {
    html: string;
    stamp: string;
  }) => Promise<ExportResult>;
  revealPath: (p: string) => Promise<{ success: boolean }>;

  onTelemetryUpdate: (cb: (data: BmgTelemetry) => void) => () => void;
  onStepChange: (cb: (step: MissionStepEvent) => void) => () => void;
  onMilestone: (cb: (event: MilestoneEvent) => void) => () => void;
  onRawEvidenceReady: (cb: (evidence: RawEvidence) => void) => () => void;
  onMissionLog: (cb: (log: LogLine) => void) => () => void;
  onDriverLog: (cb: (log: LogLine) => void) => () => void;
  onMissionExit: (cb: (event: MissionExitEvent) => void) => () => void;
  onLinkProgress: (cb: (event: LinkProgressEvent) => void) => () => void;
  onAnnounceDone: (cb: (event: AnnounceDoneEvent) => void) => () => void;
  onCameraTiltChanged: (cb: (event: CameraTiltEvent) => void) => () => void;
  onMissionReset: (cb: (event: MissionResetEvent) => void) => () => void;
  onTerminalOutput: (cb: (event: TerminalOutputEvent) => void) => () => void;
}

declare global {
  interface Window {
    bmgAPI?: BmgAPI;
  }
}
