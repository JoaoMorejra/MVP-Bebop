/**
 * @file landSupervisor.cjs
 *
 * Supervisor de pouso da estação (R6).
 *
 * Garante em malha fechada que após qualquer pouso comandado (abort ou
 * Finalizar com aeronave no ar), o comando seja retransmitido periodicamente
 * pela ponte (<= 1/s) e com backup CLI em +3 s se a aeronave continuar nos
 * estados no ar {1, 2, 3, 7}, até confirmação em {0, 4, 5, 8} ou timeout de 15 s.
 *
 * Emite `bmg:land-progress {phase: commanded|landing|landed|unconfirmed, t}`.
 */

'use strict';

/**
 * ARSDK flying states:
 * 0: landed
 * 1: taking off
 * 2: hovering
 * 3: flying
 * 4: landing
 * 5: emergency (motors cut)
 * 6: user takeoff
 * 7: motor ramping
 * 8: emergency landing
 */
const STOP_RESEND_STATES = new Set([0, 4, 5, 8]);
const LANDING_STATES = new Set([4, 8]);

/**
 * Avaliação pura de cada ciclo do supervisor de pouso.
 *
 * @param {object} input
 * @param {number} input.startedAt - Momento (ms) em que o pouso foi disparado
 * @param {number} input.lastBridgeResendAt - Momento (ms) do último envio pela ponte
 * @param {boolean} input.cliBackupSent - Se o backup CLI já foi disparado
 * @param {number|null|undefined} input.flyingState - Estado de voo atual
 * @param {number} now - Momento atual (ms)
 * @param {number} [timeoutMs=15000] - Tempo limite para desistir da confirmação
 * @returns {{
 *   phase: 'commanded'|'landing'|'landed'|'unconfirmed',
 *   shouldResendBridge: boolean,
 *   shouldSendCliBackup: boolean,
 *   isDone: boolean
 * }}
 */
function evaluateLandStep(input, now, timeoutMs = 15000) {
  const { startedAt, lastBridgeResendAt, cliBackupSent, flyingState } = input;
  const elapsed = now - startedAt;

  // 1. Em solo (0) ou corte de emergência (5) -> Pousada e concluído
  if (flyingState === 0 || flyingState === 5) {
    return {
      phase: 'landed',
      shouldResendBridge: false,
      shouldSendCliBackup: false,
      isDone: true,
    };
  }

  // 2. Timeout de 15 s sem pouso confirmado
  if (elapsed >= timeoutMs) {
    return {
      phase: 'unconfirmed',
      shouldResendBridge: false,
      shouldSendCliBackup: false,
      isDone: true,
    };
  }

  // 3. Em descida/pouso ativo (4, 8) -> Pousando, sem novos reenvios
  if (LANDING_STATES.has(flyingState)) {
    return {
      phase: 'landing',
      shouldResendBridge: false,
      shouldSendCliBackup: false,
      isDone: false,
    };
  }

  // 4. Ainda nos estados no ar {1, 2, 3, 7} ou estado desconhecido
  // Reenviar pela ponte com taxa <= 1/s (intervalo >= 1000 ms)
  const shouldResendBridge = (now - lastBridgeResendAt) >= 1000;
  // Backup CLI disparado uma única vez após 3 s
  const shouldSendCliBackup = !cliBackupSent && elapsed >= 3000;

  return {
    phase: 'commanded',
    shouldResendBridge,
    shouldSendCliBackup,
    isDone: false,
  };
}

/**
 * Cria o executor de supervisão de pouso da estação.
 *
 * @param {object} options
 * @param {() => (number|null|undefined)} options.getFlyingState - Função para ler flying_state
 * @param {() => void} [options.sendBridgeLand] - Disparo de land via ponte residente
 * @param {() => void} [options.sendCliBackup] - Disparo de land via backup CLI (ros2 topic pub)
 * @param {(progress: {phase: string, t: number}) => void} options.emitProgress - Emissor para IPC renderer
 * @param {(channel: string, entry: {type: string, text: string}) => void} [options.recordLog] - Logger do BMG
 * @param {number} [options.intervalMs=500] - Ciclo de amostragem
 * @param {number} [options.timeoutMs=15000] - Janela máxima de supervisão
 * @param {() => number} [options.clock=Date.now] - Provedor de relógio
 */
function createLandSupervisor(options) {
  const {
    getFlyingState,
    sendBridgeLand,
    sendCliBackup,
    emitProgress,
    recordLog,
    intervalMs = 500,
    timeoutMs = 15000,
    clock = Date.now,
  } = options;

  let timer = null;
  let active = false;
  let state = {
    startedAt: 0,
    lastBridgeResendAt: 0,
    cliBackupSent: false,
    lastPhase: null,
  };

  function step() {
    if (!active) return;
    const now = clock();
    const flyingState = getFlyingState();
    const decision = evaluateLandStep(
      {
        startedAt: state.startedAt,
        lastBridgeResendAt: state.lastBridgeResendAt,
        cliBackupSent: state.cliBackupSent,
        flyingState,
      },
      now,
      timeoutMs
    );

    if (decision.phase !== state.lastPhase) {
      state.lastPhase = decision.phase;
      emitProgress({ phase: decision.phase, t: now });
      if (recordLog) {
        recordLog('mission', {
          type: decision.phase === 'unconfirmed' ? 'stderr' : 'stdout',
          text: `[BMG] Supervisor de pouso: fase=${decision.phase} (flying_state=${flyingState})\n`,
        });
      }
    }

    if (decision.shouldResendBridge) {
      state.lastBridgeResendAt = now;
      if (sendBridgeLand) {
        sendBridgeLand();
      }
    }

    if (decision.shouldSendCliBackup) {
      state.cliBackupSent = true;
      if (sendCliBackup) {
        sendCliBackup();
      }
    }

    if (decision.isDone) {
      stop();
    }
  }

  function start() {
    stop();
    const now = clock();
    active = true;
    state = {
      startedAt: now,
      lastBridgeResendAt: now,
      cliBackupSent: false,
      lastPhase: null,
    };

    // Avaliação inicial imediata
    step();

    if (active) {
      timer = setInterval(step, intervalMs);
    }
  }

  function stop() {
    active = false;
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  function isActive() {
    return active;
  }

  function getState() {
    return { ...state };
  }

  return {
    start,
    step,
    stop,
    isActive,
    getState,
  };
}

module.exports = {
  STOP_RESEND_STATES,
  LANDING_STATES,
  evaluateLandStep,
  createLandSupervisor,
};
