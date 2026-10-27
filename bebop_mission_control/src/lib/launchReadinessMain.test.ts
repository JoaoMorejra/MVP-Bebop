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

    describe('battery threshold from battery.land_pct', () => {
      const launchWith = async (batteryPct: number, landPct?: number) => {
        await setHealthyTelemetry();
        await main.handlers['bmg:test-set-telemetry']({}, { battery_pct: batteryPct });
        const doc = completeDocument() as Record<string, unknown>;
        if (landPct !== undefined) doc.battery = { ...((doc.battery as object) ?? {}), land_pct: landPct };
        return (await main.handlers['bmg:start-mission']({}, {
          noFly: false,
          paramsJson: JSON.stringify(doc),
        })) as { success: boolean; error?: string };
      };

      it('launches at 25% with no threshold in the document (default 20)', async () => {
        expect((await launchWith(25)).success).toBe(true);
      });

      it('blocks at 15% with the default threshold', async () => {
        const res = await launchWith(15);
        expect(res.success).toBe(false);
        expect(res.error).toBe('Bateria insuficiente para voo real (15% <= limiar 20%)');
        expect(missionSpawn()).toBeUndefined();
      });

      it('blocks 28% against a land_pct of 30', async () => {
        const res = await launchWith(28, 30);
        expect(res.success).toBe(false);
        expect(res.error).toBe('Bateria insuficiente para voo real (28% <= limiar 30%)');
      });

      it('launches 25% against a land_pct of 10', async () => {
        expect((await launchWith(25, 10)).success).toBe(true);
      });

      it('respects a land_pct of 0 instead of falling back to 20', async () => {
        expect((await launchWith(5, 0)).success).toBe(true);
      });

      it('readiness query reads the same threshold from the document', async () => {
        await setHealthyTelemetry();
        await main.handlers['bmg:test-set-telemetry']({}, { battery_pct: 25 });
        const doc = { ...completeDocument(), battery: { land_pct: 30 } };
        const verdict = (await main.handlers['bmg:get-launch-readiness']({}, {
          paramsJson: JSON.stringify(doc),
        })) as { ready: boolean; blockedReason: string | null };
        expect(verdict.blockedReason).toBe('Bateria insuficiente para voo real (25% <= limiar 30%)');
      });
    });

    it('does not block on a calibration flag in telemetry', async () => {
      await setHealthyTelemetry();
      await main.handlers['bmg:test-set-telemetry']({}, { calibration_required: true });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error?: string };

      expect(res.error ?? '').not.toMatch(/calibra/i);
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

  describe('copilot voice served from the phrase cache', () => {
    const speechSpawn = (): Spawned | undefined => main.spawned.find((child) => child.args.includes('--serve'));

    async function startSpeechDaemon(): Promise<Spawned> {
      await main.handlers['bmg:test-set-speech-state']({}, { ready: false, active: false });
      const res = (await main.handlers['bmg:cache-speech']({}, ['Pouso confirmado na base.'])) as { success: boolean };
      expect(res.success).toBe(true);
      const daemon = speechSpawn();
      expect(daemon).toBeDefined();
      return daemon!;
    }

    function emitSpeechEvent(daemon: Spawned, event: Record<string, unknown>) {
      daemon.child.stdout.emit('data', `BMG_SPEECH:${JSON.stringify(event)}\n`);
    }

    async function copilotLog(): Promise<string> {
      const history = (await main.handlers['bmg:get-log-history']()) as { mission: Array<{ text: string }> };
      return history.mission.map((entry) => entry.text).join('');
    }

    afterEach(async () => {
      await main.handlers['bmg:test-set-speech-state']({}, { ready: false, active: false });
      // Marked closed so the shared teardown does not emit `close`, whose
      // handler would respawn the daemon 500 ms later into the next test.
      const daemon = speechSpawn();
      if (daemon) daemon.child.closed = true;
    });

    it('launches when the daemon reports voice ready from the cache alone', async () => {
      const daemon = await startSpeechDaemon();
      await setHealthyTelemetry();
      emitSpeechEvent(daemon, {
        event: 'ready',
        voice: 'Orbit',
        model: 'gemini-live',
        synthesis: false,
        playback: true,
        voice_ready: true,
        cache_entries: 120,
        offline: true,
        detail: 'pronto (voz em cache, sem rede)',
      });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean };

      expect(res.success).toBe(true);
      const log = await copilotLog();
      expect(log).toContain('voz em cache, sem rede');
      expect(log).not.toContain('[copiloto] sem voz');
    });

    it('says the network is down on a synthesis-ready line without changing the gate', async () => {
      const daemon = await startSpeechDaemon();
      await setHealthyTelemetry();
      emitSpeechEvent(daemon, {
        event: 'ready',
        voice: 'Orbit',
        model: 'gemini-live',
        synthesis: true,
        playback: true,
        voice_ready: true,
        cache_entries: 0,
        offline: true,
        detail: 'pronto',
      });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean };

      expect(res.success).toBe(true);
      expect(await copilotLog()).toContain('[copiloto] pronto (voz Orbit, modelo gemini-live, sem rede)');
    });

    it('blocks when the daemon reports no voice at all', async () => {
      const daemon = await startSpeechDaemon();
      await setHealthyTelemetry();
      emitSpeechEvent(daemon, {
        event: 'ready',
        synthesis: false,
        playback: true,
        voice_ready: false,
        cache_entries: 0,
        offline: true,
        detail: 'sem chave de API (SPEECH_API_KEY / GEMINI_API_KEY)',
      });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Voz do copiloto indisponível ou offline');
    });

    it('falls back to synthesis and playback for a daemon without voice_ready', async () => {
      const daemon = await startSpeechDaemon();
      await setHealthyTelemetry();
      emitSpeechEvent(daemon, { event: 'ready', synthesis: false, playback: true, detail: 'sem chave de API' });

      const res = (await main.handlers['bmg:start-mission']({}, {
        noFly: false,
        paramsJson: JSON.stringify(completeDocument()),
      })) as { success: boolean; error: string };

      expect(res.success).toBe(false);
      expect(res.error).toBe('Voz do copiloto indisponível ou offline');
    });

    it('logs how many lines will be skipped when the cache is incomplete offline', async () => {
      const daemon = await startSpeechDaemon();
      emitSpeechEvent(daemon, { event: 'cache', scheduled: 0, requested: 10, missing: 3, offline: true });

      expect(await copilotLog()).toContain('[copiloto] sem rede: 3 de 10 falas fora do cache serão puladas.');
    });
  });
});
