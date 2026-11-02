import { describe, expect, it } from 'vitest';
import { evaluateLaunchReadiness, LaunchReadinessInput } from '../../electron/launchReadiness.cjs';

describe('evaluateLaunchReadiness', () => {
  const healthyBenchInput: LaunchReadinessInput = {
    benchMode: true,
    noFly: true,
    configValid: true,
    driverRunning: true,
    orphanPids: [],
    standbyReady: true,
  };

  const healthyRealFlightInput: LaunchReadinessInput = {
    benchMode: false,
    noFly: false,
    configValid: true,
    driverRunning: true,
    orphanPids: [],
    statesLink: true,
    telemetryAgeMs: 250,
    odomFresh: true,
    videoFresh: true,
    missingTopics: [],
    flyingState: 0,
    batteryKnown: true,
    batteryPct: 85,
    batteryFailsafeThreshold: 20,
    commandBridgeReady: true,
    ros2CliAvailable: true,
    voiceReady: true,
    standbyReady: true,
  };

  it('approves bench mode when basic requirements are met', () => {
    const verdict = evaluateLaunchReadiness(healthyBenchInput);
    expect(verdict.ready).toBe(true);
    expect(verdict.blockedReason).toBeNull();
    expect(verdict.warning).toBeNull();
  });

  it('warns when standby is unavailable in bench mode without blocking', () => {
    const verdict = evaluateLaunchReadiness({ ...healthyBenchInput, standbyReady: false });
    expect(verdict.ready).toBe(true);
    expect(verdict.blockedReason).toBeNull();
    expect(verdict.warning).toBe('Contagem começa no clique');
  });

  it('approves real flight when all aircraft and telemetry conditions pass', () => {
    const verdict = evaluateLaunchReadiness(healthyRealFlightInput);
    expect(verdict.ready).toBe(true);
    expect(verdict.blockedReason).toBeNull();
    expect(verdict.warning).toBeNull();
  });

  it('blocks if config document is invalid or missing', () => {
    const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, configValid: false });
    expect(verdict.ready).toBe(false);
    expect(verdict.blockedReason).toMatch(/parâmetros ausente ou inválido/i);
  });

  it('blocks if orphan missions are detected', () => {
    const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, orphanPids: [1234, 5678] });
    expect(verdict.ready).toBe(false);
    expect(verdict.blockedReason).toMatch(/Missão órfã ainda em execução \(pid 1234, 5678\)/);
  });

  it('blocks if mode flag is inconsistent with benchMode', () => {
    const benchMismatch = evaluateLaunchReadiness({ ...healthyBenchInput, noFly: false });
    expect(benchMismatch.ready).toBe(false);
    expect(benchMismatch.blockedReason).toMatch(/Incoerência: bancada requer modo sem voo/);

    const flightMismatch = evaluateLaunchReadiness({ ...healthyRealFlightInput, noFly: true });
    expect(flightMismatch.ready).toBe(false);
    expect(flightMismatch.blockedReason).toMatch(/Incoerência: voo real requer flag de voo/);
  });

  it('blocks if driver is down', () => {
    const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, driverRunning: false });
    expect(verdict.ready).toBe(false);
    expect(verdict.blockedReason).toBe('Driver ROS 2 fora do ar');
  });

  describe('real flight aircraft gates', () => {
    it('blocks if aircraft link is inactive', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, statesLink: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Link com a aeronave inativo');
    });

    it('blocks if telemetry is older than 1500 ms', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, telemetryAgeMs: 1550 });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toMatch(/Telemetria defasada/);
    });

    it('blocks if required topics are missing', () => {
      const verdict = evaluateLaunchReadiness({
        ...healthyRealFlightInput,
        missingTopics: ['/bebop/camera/image_raw', '/bebop/cmd_vel'],
      });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe(
        'Tópicos obrigatórios sem tráfego: /bebop/camera/image_raw, /bebop/cmd_vel'
      );
    });

    it('blocks if odometry is stale', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, odomFresh: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Sem odometria recente');
    });

    it('blocks if video stream is inactive', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, videoFresh: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Transmissão de vídeo inativa');
    });

    it('blocks if aircraft is not in flying_state 0 (on ground)', () => {
      const airborne = evaluateLaunchReadiness({ ...healthyRealFlightInput, flyingState: 2 });
      expect(airborne.ready).toBe(false);
      expect(airborne.blockedReason).toBe('Aeronave não está em solo (flying_state 2)');

      const nullState = evaluateLaunchReadiness({ ...healthyRealFlightInput, flyingState: null });
      expect(nullState.ready).toBe(false);
      expect(nullState.blockedReason).toBe('Aeronave não está em solo (flying_state nulo)');
    });

    it('blocks if battery is unknown', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, batteryKnown: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Bateria da aeronave desconhecida');
    });

    it('blocks only when battery is at or below the operator threshold', () => {
      const verdictFor = (batteryPct: number, batteryFailsafeThreshold?: number) =>
        evaluateLaunchReadiness({ ...healthyRealFlightInput, batteryPct, batteryFailsafeThreshold });
      expect(verdictFor(28, 20).ready).toBe(true);
      const atLimit = verdictFor(20, 20);
      expect(atLimit.ready).toBe(false);
      expect(atLimit.blockedReason).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
      expect(verdictFor(21, 20).ready).toBe(true);
      expect(verdictFor(5, 0).ready).toBe(true);
      expect(verdictFor(100, 100).blockedReason).toBe('Bateria insuficiente para voo real (100% <= limiar 100%)');
      expect(verdictFor(20, undefined).blockedReason).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
      expect(verdictFor(21, undefined).ready).toBe(true);
    });

    it('does not gate on the charge with the battery failsafe off', () => {
      const off = { ...healthyRealFlightInput, batteryFailsafeEnabled: false };
      expect(evaluateLaunchReadiness({ ...off, batteryKnown: false }).ready).toBe(true);
      expect(evaluateLaunchReadiness({ ...off, batteryPct: 5, batteryFailsafeThreshold: 20 }).ready).toBe(true);
      const on = evaluateLaunchReadiness({
        ...healthyRealFlightInput,
        batteryFailsafeEnabled: true,
        batteryPct: 20,
        batteryFailsafeThreshold: 20,
      });
      expect(on.blockedReason).toBe('Bateria insuficiente para voo real (20% <= limiar 20%)');
    });

    it('blocks if resident command bridge is not ready', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, commandBridgeReady: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Ponte de comando não está pronta');
    });

    it('blocks if backup ros2 CLI is unavailable', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, ros2CliAvailable: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('ROS 2 CLI de backup indisponível');
    });

    it('blocks if voice copilot is unavailable or offline', () => {
      const verdict = evaluateLaunchReadiness({ ...healthyRealFlightInput, voiceReady: false });
      expect(verdict.ready).toBe(false);
      expect(verdict.blockedReason).toBe('Voz do copiloto indisponível ou offline');
    });
  });
});
