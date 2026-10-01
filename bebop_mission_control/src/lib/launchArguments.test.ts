import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import { loadMain, type Spawned } from './__fixtures__/mainHarness';

let dir: string;
let config: string;
let main: ReturnType<typeof loadMain>;

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'bmg-launch-'));
  config = join(dir, 'mission_config.json');
  main = loadMain({ BMG_MISSION_CONFIG: config });
});
afterAll(() => {
  main.restore();
  rmSync(dir, { recursive: true, force: true });
});
afterEach(() => {
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
