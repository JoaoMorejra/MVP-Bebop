/**
 * Single authoritative pre-flight launch readiness evaluator.
 *
 * Implements Section 2.4 (R4/R8) of docs/PROMPT_VOO_REAL_ESCOPO_ATUAL.md.
 *
 * Evaluates whether a mission can be safely launched, enforcing:
 * - Valid parameters configuration
 * - No orphan mission processes
 * - Driver ROS 2 running
 * - Bench vs real-flight mode consistency
 *
 * For real flight (--fly / benchMode === false):
 * - Link with aircraft active (states/link / connected)
 * - Fresh telemetry (< 1500 ms)
 * - Required topics receiving
 * - Odometry and video stream fresh
 * - Flying state === 0 (on ground)
 * - Battery known and >= max(failsafeThreshold + 10, 30)%
 * - Magnetometer calibration not required
 * - Resident command bridge ready
 * - Backup ROS 2 CLI available
 * - Voice copilot ready (daemon alive, audio device open, online)
 *
 * For standby:
 * - If not ready, produces warning "Contagem começa no clique" without blocking.
 */

/**
 * @typedef {Object} LaunchReadinessInput
 * @property {boolean} benchMode
 * @property {boolean} [noFly]
 * @property {boolean} configValid
 * @property {boolean} driverRunning
 * @property {Array<number|string>} [orphanPids]
 * @property {boolean} [statesLink]
 * @property {number | null} [telemetryAgeMs]
 * @property {boolean} [odomFresh]
 * @property {boolean} [videoFresh]
 * @property {string[]} [missingTopics]
 * @property {number | null} [flyingState]
 * @property {boolean} [batteryKnown]
 * @property {number | null} [batteryPct]
 * @property {number} [batteryFailsafeThreshold]
 * @property {boolean} [magnetoRequired]
 * @property {boolean} [commandBridgeReady]
 * @property {boolean} [ros2CliAvailable]
 * @property {boolean} [voiceReady]
 * @property {boolean} [standbyReady]
 */

/**
 * @typedef {Object} LaunchReadinessResult
 * @property {boolean} ready
 * @property {string | null} blockedReason
 * @property {string | null} warning
 */

/**
 * Evaluates full launch readiness verdict.
 *
 * @param {LaunchReadinessInput} input
 * @returns {LaunchReadinessResult}
 */
function evaluateLaunchReadiness(input) {
  if (!input || typeof input !== 'object') {
    return { ready: false, blockedReason: 'Dados de prontidão ausentes', warning: null };
  }

  // 1. Parameters document validation
  if (!input.configValid) {
    return { ready: false, blockedReason: 'Documento de parâmetros ausente ou inválido', warning: null };
  }

  // 2. Orphan mission check
  if (Array.isArray(input.orphanPids) && input.orphanPids.length > 0) {
    return {
      ready: false,
      blockedReason: `Missão órfã ainda em execução (pid ${input.orphanPids.join(', ')}). Aguarde o pouso ou encerre-a antes de lançar.`,
      warning: null,
    };
  }

  // 3. Mode coherence (benchMode vs noFly flag)
  if (input.noFly !== undefined) {
    if (input.benchMode && !input.noFly) {
      return { ready: false, blockedReason: 'Incoerência: bancada requer modo sem voo (--no-fly)', warning: null };
    }
    if (!input.benchMode && input.noFly) {
      return { ready: false, blockedReason: 'Incoerência: voo real requer flag de voo (--fly)', warning: null };
    }
  }

  // If bench mode, aircraft link and flight hardware gates are dropped
  if (input.benchMode) {
    const warning = input.standbyReady === false ? 'Contagem começa no clique' : null;
    return { ready: true, blockedReason: null, warning };
  }

  // Real Flight Gates (--fly)
  // 4. Driver running check
  if (!input.driverRunning) {
    return { ready: false, blockedReason: 'Driver ROS 2 fora do ar', warning: null };
  }

  // 5. Aircraft link
  if (!input.statesLink) {
    return { ready: false, blockedReason: 'Link com a aeronave inativo', warning: null };
  }

  // 6. Telemetry freshness (< 1500 ms)
  if (input.telemetryAgeMs === null || input.telemetryAgeMs === undefined || input.telemetryAgeMs >= 1500) {
    return { ready: false, blockedReason: 'Telemetria defasada (>= 1,5 s)', warning: null };
  }

  // 7. Mandatory topics receiving
  if (Array.isArray(input.missingTopics) && input.missingTopics.length > 0) {
    return {
      ready: false,
      blockedReason: `Tópicos obrigatórios sem tráfego: ${input.missingTopics.join(', ')}`,
      warning: null,
    };
  }

  // 8. Odometry fresh
  if (input.odomFresh === false) {
    return { ready: false, blockedReason: 'Sem odometria recente', warning: null };
  }

  // 9. Video stream fresh
  if (input.videoFresh === false) {
    return { ready: false, blockedReason: 'Transmissão de vídeo inativa', warning: null };
  }

  // 10. Flying state === 0 (landed on ground)
  if (input.flyingState !== 0) {
    return {
      ready: false,
      blockedReason: `Aeronave não está em solo (flying_state ${input.flyingState ?? 'nulo'})`,
      warning: null,
    };
  }

  // 11. Battery known and >= max(failsafe + 10, 30)%
  if (input.batteryKnown === false) {
    return { ready: false, blockedReason: 'Bateria da aeronave desconhecida', warning: null };
  }
  const minBattery = Math.max((input.batteryFailsafeThreshold ?? 20) + 10, 30);
  if (typeof input.batteryPct !== 'number' || input.batteryPct < minBattery) {
    return {
      ready: false,
      blockedReason: `Bateria insuficiente para voo real (${input.batteryPct ?? 0}% < ${minBattery}%)`,
      warning: null,
    };
  }

  // 12. Magnetometer calibration
  if (input.magnetoRequired === true) {
    return { ready: false, blockedReason: 'Calibração do magnetômetro necessária', warning: null };
  }

  // 13. Resident command bridge ready
  if (input.commandBridgeReady === false) {
    return { ready: false, blockedReason: 'Ponte de comando não está pronta', warning: null };
  }

  // 14. Emergency ros2 CLI backup
  if (input.ros2CliAvailable === false) {
    return { ready: false, blockedReason: 'ROS 2 CLI de backup indisponível', warning: null };
  }

  // 15. Voice copilot ready (daemon alive, audio open, backend online)
  if (input.voiceReady === false) {
    return { ready: false, blockedReason: 'Voz do copiloto indisponível ou offline', warning: null };
  }

  const warning = input.standbyReady === false ? 'Contagem começa no clique' : null;
  return { ready: true, blockedReason: null, warning };
}

module.exports = {
  evaluateLaunchReadiness,
};
