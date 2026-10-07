import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import { loadMain, type Spawned } from './__fixtures__/mainHarness';

let dir: string;
let config: string;
let main: ReturnType<typeof loadMain>;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'bmg-launch-'));
  config = join(dir, 'mission_config.json');
  main = loadMain({ BMG_MISSION_CONFIG: config, BMG_SKIP_READINESS: '1' });
});
afterAll(() => {
  main.restore();
  rmSync(dir, { recursive: true, force: true });
});
afterEach(async () => {
  // A test that failed before ending its mission would leave the host holding
  // it, and every later launch would be refused as already running.
  for (const spawned of main.spawned) if (!spawned.child.closed) spawned.child.emit('close', 0, null);
  await new Promise((resolve) => setTimeout(resolve, 0));
  main.spawned.length = 0;
  rmSync(config, { force: true });
});

const missionSpawn = (): Spawned | undefined =>
  main.spawned.find((child) => child.args.some((arg) => arg.endsWith('mission.py')) && !child.args.includes('--dump-defaults'));

async function endMission(child: Spawned | undefined) {
  child?.child.emit('close', 0, null);
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('bmg:start-mission', () => {
  it('sends the parameter document as the only source of the flight parameters', async () => {
    const doc = completeDocument({ 'kinematics.target_altitude_m': 1.6 });
    const result = (await main.handlers['bmg:start-mission']({}, {
      noFly: true,
      countdown: 4,
      height: 1,
      velocity: 0.2,
      rtlVelocity: 0.1,
      searchTimeout: 30,
      hoverDuration: 7,
      confidence: 0.5,
      arrivalRadius: 0.2,
      modelPath: 'other.pt',
      ip: '10.0.0.1',
      detectionTopic: '/x',
      paramsJson: JSON.stringify(doc),
    })) as { success: boolean; argv: string[] };
    expect(result.success).toBe(true);
    const args = missionSpawn()!.args;
    for (const flag of [
      '--height', '--velocity', '--rtl-velocity', '--search-timeout', '--hover-duration',
      '--confidence', '--arrival-radius', '--model-path', '--ip', '--detection-topic', '--countdown',
    ]) {
      expect(args).not.toContain(flag);
    }
    expect(JSON.parse(args[args.indexOf('--params-json') + 1])).toEqual(doc);
    expect(args).toContain('--no-fly');
    await endMission(missionSpawn());
  });

  it('states the arming mode explicitly for a real flight', async () => {
    await main.handlers['bmg:start-mission']({}, { noFly: false, paramsJson: JSON.stringify(completeDocument()) });
    expect(missionSpawn()!.args).toContain('--fly');
    await endMission(missionSpawn());
  });

  it.each([undefined, '', 'not json', '[1]', 'null'])('refuses to launch without a parameter document (%s)', async (paramsJson) => {
    const result = (await main.handlers['bmg:start-mission']({}, { noFly: true, paramsJson })) as {
      success: boolean;
      error?: string;
    };
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/parâmetros/i);
    expect(missionSpawn()).toBeUndefined();
  });
});

describe('bmg:start-bench-stage', () => {
  it('runs the routine without a second countdown, from the same document', async () => {
    const doc = completeDocument({ 'kinematics.countdown_sec': 8 });
    const result = (await main.handlers['bmg:start-bench-stage']({}, {
      stage: 3,
      countdown: 0,
      paramsJson: JSON.stringify(doc),
    })) as { success: boolean };
    expect(result.success).toBe(true);
    const args = missionSpawn()!.args;
    expect(args[args.indexOf('--stages') + 1]).toBe('3');
    const sent = JSON.parse(args[args.indexOf('--params-json') + 1]);
    expect(sent.kinematics.countdown_sec).toBe(0);
    expect(sent.kinematics.target_altitude_m).toBe(doc.kinematics && (doc.kinematics as Record<string, unknown>).target_altitude_m);
    expect(args).toContain('--no-fly');
    expect(args).not.toContain('--launch-at-ms');
    await endMission(missionSpawn());
  });

  it('counts the whole countdown on the station before it spawns the routine', async () => {
    vi.useFakeTimers();
    try {
      const doc = completeDocument({ 'kinematics.countdown_sec': 3 });
      const result = (await main.handlers['bmg:start-bench-stage']({}, {
        stage: 2,
        countdown: 3,
        paramsJson: JSON.stringify(doc),
      })) as { success: boolean; deferred?: boolean };
      expect(result).toMatchObject({ success: true, deferred: true });
      expect(missionSpawn()).toBeUndefined();
      await vi.advanceTimersByTimeAsync(2_900);
      expect(missionSpawn()).toBeUndefined();
      await vi.advanceTimersByTimeAsync(200);
      const args = missionSpawn()!.args;
      expect(args[args.indexOf('--stages') + 1]).toBe('2');
      expect(args).not.toContain('--launch-at-ms');
      expect(JSON.parse(args[args.indexOf('--params-json') + 1]).kinematics.countdown_sec).toBe(0);
    } finally {
      vi.useRealTimers();
    }
    await endMission(missionSpawn());
  });
});

describe('parameter IPC', () => {
  it('refuses a corrupt mission_config.json instead of returning defaults', async () => {
    writeFileSync(config, '{"kinematics": ');
    const result = (await main.handlers['bmg:get-parameters']({})) as { success: boolean; error?: string };
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/mission_config\.json/);
    expect(main.spawned.some((child) => child.args.includes('--dump-defaults'))).toBe(false);
  });

  it('serves the factory defaults from mission.py --dump-defaults', async () => {
    const pending = main.handlers['bmg:get-parameter-defaults']({}) as Promise<{ success: boolean; params?: unknown }>;
    const dump = main.spawned.find((child) => child.args.includes('--dump-defaults'))!;
    expect(dump.args.slice(0, 2)).toEqual(['python3', expect.stringMatching(/mission\.py$/)]);
    dump.child.stdout.emit('data', '{"kinematics": {"target_altitude_m": 1.0}}\n');
    dump.child.emit('close', 0);
    expect(await pending).toEqual({ success: true, params: { kinematics: { target_altitude_m: 1.0 } } });
  });

  it('starts a fresh station from the factory defaults when there is no file', async () => {
    const pending = main.handlers['bmg:get-parameters']({}) as Promise<{ success: boolean; params?: unknown; source?: string }>;
    await new Promise((resolve) => setTimeout(resolve, 0));
    const dump = main.spawned.find((child) => child.args.includes('--dump-defaults'))!;
    dump.child.stdout.emit('data', '{"no_fly": false}');
    dump.child.emit('close', 0);
    expect(await pending).toEqual({ success: true, params: { no_fly: false }, source: 'defaults' });
  });

  it('reports a failed defaults dump', async () => {
    const pending = main.handlers['bmg:get-parameter-defaults']({}) as Promise<{ success: boolean; error?: string }>;
    const dump = main.spawned.find((child) => child.args.includes('--dump-defaults'))!;
    dump.child.stderr.emit('data', 'ModuleNotFoundError: rclpy');
    dump.child.emit('close', 1);
    const result = await pending;
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/rclpy/);
  });

  it('writes the saved document atomically', async () => {
    const doc = completeDocument();
    const result = (await main.handlers['bmg:save-parameters']({}, doc)) as { success: boolean };
    expect(result.success).toBe(true);
    expect(JSON.parse(readFileSync(config, 'utf-8'))).toEqual(doc);
    expect(readdirSync(dir)).toEqual(['mission_config.json']);
  });
});

describe('instant launch from a prepared mission', () => {
  const standbySpawns = () => main.spawned.filter((child) => child.args.includes('--standby'));
  const freshSpawns = () =>
    main.spawned.filter((child) => child.args.some((arg) => arg.endsWith('mission.py')) && !child.args.includes('--standby') && !child.args.includes('--dump-defaults'));

  it('prepares a standby mission from the saved document', async () => {
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.1' });
    await main.handlers['bmg:save-parameters']({}, doc);
    const standby = standbySpawns().slice(-1)[0];
    expect(standby.args).toContain('--no-fly');
    expect(JSON.parse(standby.args[standby.args.indexOf('--params-json') + 1])).toEqual(doc);
    expect(standby.env.BMG_GCS_SESSION).toBe('1');
  });

  it('hands the click instant to the prepared mission instead of spawning one', async () => {
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.2' });
    await main.handlers['bmg:save-parameters']({}, doc);
    const standby = standbySpawns().slice(-1)[0];
    standby.child.stdout.emit('data', '[STANDBY] ready\n');
    const writes: string[] = [];
    standby.child.stdin.write = ((text: string) => writes.push(text) > 0) as never;
    const result = (await main.handlers['bmg:start-mission']({}, {
      noFly: true,
      countdown: 10,
      launchAtMs: 4242,
      paramsJson: JSON.stringify(doc),
    })) as { success: boolean };
    expect(result.success).toBe(true);
    expect(freshSpawns()).toHaveLength(0);
    const go = JSON.parse(writes[0]);
    expect(go.op).toBe('go');
    expect(go.launch_at_ms).toBe(4242);
    expect(go.params.kinematics).toEqual(doc.kinematics);
    standby.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  const missionLog = async (): Promise<string> => {
    const history = (await main.handlers['bmg:get-log-history']({})) as { mission: { text: string }[] };
    return history.mission.map((entry) => entry.text).join('');
  };

  it('spawns a cold launch without the click instant, stopping the standby first and saying why', async () => {
    // Regression: the cold spawn was handed the click instant, read it after
    // its own 6-12 s start-up and took off on a deadline already behind it.
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.3' });
    await main.handlers['bmg:save-parameters']({}, doc);
    const standby = standbySpawns().slice(-1)[0];
    const killed: string[] = [];
    standby.child.kill = ((signal: string) => killed.push(signal) > 0) as never;
    await main.handlers['bmg:start-mission']({}, { noFly: true, launchAtMs: 777, paramsJson: JSON.stringify(doc) });
    const fresh = freshSpawns().slice(-1)[0];
    expect(fresh.args).not.toContain('--launch-at-ms');
    expect(killed).toEqual(['SIGTERM']);
    expect(await missionLog()).toContain('[BMG] Lançamento frio (missão em espera ainda em preparo)');
    fresh.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('launches a second time, before the standby is prepared again, cold and unpinned', async () => {
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.4' });
    await main.handlers['bmg:save-parameters']({}, doc);
    const first = standbySpawns().slice(-1)[0];
    first.child.stdout.emit('data', '[STANDBY] ready\n');
    const launchDoc = { paramsJson: JSON.stringify(doc), noFly: true, countdown: 10 };
    await main.handlers['bmg:start-mission']({}, { ...launchDoc, launchAtMs: 1000 });
    expect(freshSpawns()).toHaveLength(0);
    first.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const rearmed = standbySpawns().slice(-1)[0];
    expect(rearmed).not.toBe(first);

    await main.handlers['bmg:start-mission']({}, { ...launchDoc, launchAtMs: 2000 });
    const second = freshSpawns().slice(-1)[0];
    expect(second.args).not.toContain('--launch-at-ms');
    expect(await missionLog()).toContain('[BMG] Lançamento frio (missão em espera ainda em preparo)');
    second.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it('names the start-up fields a standby was prepared for differently', async () => {
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.5' });
    await main.handlers['bmg:save-parameters']({}, doc);
    standbySpawns().slice(-1)[0].child.stdout.emit('data', '[STANDBY] ready\n');
    const other = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.6' });
    await main.handlers['bmg:start-mission']({}, { noFly: true, launchAtMs: 3000, paramsJson: JSON.stringify(other) });
    const fresh = freshSpawns().slice(-1)[0];
    expect(fresh.args).not.toContain('--launch-at-ms');
    expect(await missionLog()).toContain('[BMG] Lançamento frio (missão em espera preparada para outro network.drone_ip)');
    fresh.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});

describe('the parameter store a mission writes back to', () => {
  it('is the station store, for a standby and for a fresh spawn alike', async () => {
    const doc = completeDocument({ no_fly: true, 'network.drone_ip': '10.0.0.9' });
    await main.handlers['bmg:save-parameters']({}, doc);
    const standby = main.spawned.filter((child) => child.args.includes('--standby')).slice(-1)[0];
    expect(standby.args[standby.args.indexOf('--config') + 1]).toBe(config);
    await main.handlers['bmg:start-mission']({}, { noFly: true, launchAtMs: 1, paramsJson: JSON.stringify(doc) });
    const fresh = main.spawned
      .filter((child) => child.args.some((arg) => arg.endsWith('mission.py')) && !child.args.includes('--standby'))
      .slice(-1)[0];
    expect(fresh.args[fresh.args.indexOf('--config') + 1]).toBe(config);
    fresh.child.emit('close', 0, null);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});

