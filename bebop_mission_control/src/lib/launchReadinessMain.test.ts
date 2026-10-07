import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import { loadMain, type Spawned } from './__fixtures__/mainHarness';

let dir: string;
let config: string;
let main: ReturnType<typeof loadMain>;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'bmg-readiness-'));
  config = join(dir, 'mission_config.json');
  main = loadMain({ BMG_MISSION_CONFIG: config });
});

afterAll(() => {
  main.restore();
  rmSync(dir, { recursive: true, force: true });
});

afterEach(async () => {
  for (const spawned of main.spawned) {
    if (!spawned.child.closed) spawned.child.emit('close', 0, null);
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
  main.spawned.length = 0;
  rmSync(config, { force: true });
});

const missionSpawn = (): Spawned | undefined =>
  main.spawned.find((child) => child.args.some((arg) => arg.endsWith('mission.py')) && !child.args.includes('--dump-defaults'));

async function setHealthyTelemetry() {
  await main.handlers['bmg:test-set-telemetry']({}, {
    connected: true,
    driver_running: true,
    flying_state: 0,
    battery_known: true,
    battery_pct: 90,
    calibration_required: false,
    topics: {
      '/bebop/odom': { receiving: true, age_sec: 0.1 },
      '/bebop/camera/image_raw': { present: true, receiving: true },
      '/bebop/camera/camera_info': { receiving: true },
      '/bebop/states/battery': { receiving: true },
      '/bebop/cmd_vel': { receiving: true },
      '/bebop/takeoff': { receiving: true },
      '/bebop/land': { receiving: true },
      '/bebop/move_camera': { receiving: true },
    },
  });
  await main.handlers['bmg:test-set-speech-state']({}, { ready: true, detail: 'pronto' });
  await main.handlers['bmg:test-set-command-bridge-ready']({}, true);
  await main.handlers['bmg:test-set-stream-status']({}, { running: true, live: true });
}

describe('R4 / R8 Launch Readiness in main.cjs', () => {
  it('permits bench mode (--no-fly) even if aircraft is disconnected', async () => {
    // Aircraft is disconnected by default
    await main.handlers['bmg:test-set-telemetry']({}, {
      connected: false,
      driver_running: true,
    });

    const res = (await main.handlers['bmg:start-mission']({}, {
      noFly: true,
      paramsJson: JSON.stringify(completeDocument()),
    })) as { success: boolean };

    expect(res.success).toBe(true);
    expect(missionSpawn()).toBeDefined();
    expect(missionSpawn()!.args).toContain('--no-fly');
  });

  describe('blocking table for real flight (--fly)', () => {
    it('blocks if driver is down', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { driver_running: false });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Driver ROS 2 fora do ar');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if link is disconnected', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { connected: false });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Link com a aeronave inativo');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if aircraft is not in flying_state 0', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { flying_state: 2 }); // hovering

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Aeronave não está em solo (flying_state 2)');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if battery is unknown', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { battery_known: false });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Bateria da aeronave desconhecida');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if battery is below safe threshold', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { battery_pct: 25 });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toMatch(/Bateria insuficiente para voo real \(25% < 30%\)/);
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if magnetometer calibration is required', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { calibration_required: true });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Calibração do magnetômetro necessária');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if command bridge is not ready', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-command-bridge-ready']({}, false);

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Ponte de comando não está pronta');
      expect(missionSpawn()).toBeUndefined();
    });

    it('blocks if voice copilot is unavailable or offline', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-speech-state']({}, { ready: false, detail: 'offline' });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Voz do copiloto indisponível ou offline');
      expect(missionSpawn()).toBeUndefined();
    });
  });

  describe('re-evaluation on click and mutual exclusion', () => {
    it('re-evaluates fresh state on click and launches once condition clears', async () => {
      await setHealthyTelemetry();
      // Initially blocked on low battery
      await main.handlers['bmg:test-set-telemetry']({}, { battery_pct: 15 });

      const failRes = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean };
      expect(failRes.success).toBe(false);
      expect(missionSpawn()).toBeUndefined();

      // Battery recharged
      await main.handlers['bmg:test-set-telemetry']({}, { battery_pct: 95 });

      // Immediate re-evaluation on next click
      const passRes = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean };
      expect(passRes.success).toBe(true);
      expect(missionSpawn()).toBeDefined();
      expect(missionSpawn()!.args).toContain('--fly');
    });

    it('prevents double launch: simultaneous start calls launch at most one mission', async () => {
      await setHealthyTelemetry();

      const [res1, res2] = await Promise.all([
        main.handlers['bmg:start-mission']({}, {
          noFly: false,
          paramsJson: JSON.stringify(completeDocument()),
        }) as Promise<{ success: boolean; message?: string }>,
        main.handlers['bmg:start-mission']({}, {
          noFly: false,
          paramsJson: JSON.stringify(completeDocument()),
        }) as Promise<{ success: boolean; message?: string }>,
      ]);

      const successes = [res1, res2].filter((r) => r.success);
      const failures = [res1, res2].filter((r) => !r.success);

      expect(successes).toHaveLength(1);
      expect(failures).toHaveLength(1);
      expect(failures[0].message).toBe('Uma missão já está em andamento.');
    });
  });
});
