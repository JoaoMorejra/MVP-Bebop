/**
 * Is the running Bebop driver holding a dead ARSDK session?
 *
 * The driver does not exit when the aircraft powers off: its state callback
 * only marks the link stopped, the node stays in the graph with every topic
 * advertised, and nothing reconnects. Reused on the next bring-up, it ticks the
 * subscription and `camera_info` topics while odometry and video never arrive.
 *
 * Restarting it is the recovery, but killing a driver is only acceptable when
 * nothing depends on it. A driver is judged stale only when every condition
 * below holds; any unknown answers "keep it":
 *
 * - a driver process is alive and no mission process is up;
 * - the last flying state the aircraft reported was a ground state. The
 *   telemetry bridge blanks `flying_state` once navigation goes stale, so the
 *   last *known* value is what tells an aircraft powered off on the ground
 *   from one that lost its link in the air;
 * - telemetry itself is fresh, so the odometry facts describe the present;
 * - the driver has had {@link DRIVER_SESSION_GRACE_MS} to finish its handshake;
 * - odometry has been silent for more than {@link ODOM_SILENT_SEC}, or the
 *   bridge has been up for the grace window without a single sample.
 */

/** ARSDK flying states that mean the aircraft is off the ground. */
const AIRBORNE_FLYING_STATES = new Set([1, 2, 3, 4, 7, 8]);

/** Time a new driver or bridge is given before its silence means anything. */
const DRIVER_SESSION_GRACE_MS = 20000;

/** Odometry silence, in seconds, past which the ARSDK session is dead. */
const ODOM_SILENT_SEC = 5;

/**
 * @param {{
 *   driverAlive: boolean,
 *   missionRunning: boolean,
 *   lastFlyingState: number | null | undefined,
 *   telemetryFresh: boolean,
 *   odomAgeSec: number | null | undefined,
 *   driverAgeMs: number | null | undefined,
 *   bridgeAgeMs: number | null | undefined,
 * }} facts
 * @returns {{ stale: boolean, reason: string }}
 */
function staleDriverSession(facts) {
  if (!facts.driverAlive) return { stale: false, reason: 'sem driver em execução' };
  if (facts.missionRunning) return { stale: false, reason: 'missão em execução' };
  if (typeof facts.lastFlyingState !== 'number') {
    return { stale: false, reason: 'estado de voo nunca reportado' };
  }
  if (AIRBORNE_FLYING_STATES.has(facts.lastFlyingState)) {
    return { stale: false, reason: 'último estado de voo conhecido era em voo' };
  }
  if (!facts.telemetryFresh) return { stale: false, reason: 'telemetria congelada' };
  if (typeof facts.driverAgeMs === 'number' && facts.driverAgeMs < DRIVER_SESSION_GRACE_MS) {
    return { stale: false, reason: 'driver recém-iniciado' };
  }

  if (typeof facts.odomAgeSec === 'number') {
    return facts.odomAgeSec > ODOM_SILENT_SEC
      ? { stale: true, reason: `odometria parada há ${facts.odomAgeSec.toFixed(1)} s` }
      : { stale: false, reason: 'odometria em tráfego' };
  }
  if (typeof facts.bridgeAgeMs === 'number' && facts.bridgeAgeMs >= DRIVER_SESSION_GRACE_MS) {
    return { stale: true, reason: 'nenhuma odometria desde que a ponte subiu' };
  }
  return { stale: false, reason: 'ponte recém-iniciada' };
}

module.exports = { AIRBORNE_FLYING_STATES, DRIVER_SESSION_GRACE_MS, ODOM_SILENT_SEC, staleDriverSession };
