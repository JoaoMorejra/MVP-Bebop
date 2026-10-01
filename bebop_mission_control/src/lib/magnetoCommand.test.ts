import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { completeDocument } from './__fixtures__/missionDocument';
import { loadMain } from './__fixtures__/mainHarness';

let main: ReturnType<typeof loadMain>;
beforeAll(() => {
  main = loadMain({ BMG_MISSION_CONFIG: '/tmp/bmg-magneto-test-config.json' });
});
afterAll(() => main.restore());

describe('bmg:magneto-calibration', () => {
  it('refuses a request that is not a boolean start', async () => {
    const result = (await main.handlers['bmg:magneto-calibration']({}, { start: 'yes' })) as { success: boolean };
    expect(result.success).toBe(false);
  });

  it('reports a command bridge that is not up', async () => {
    const result = (await main.handlers['bmg:magneto-calibration']({}, { start: true })) as {
      success: boolean;
      error?: string;
    };
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/ponte/);
  });

  it('refuses to start while a mission is running', async () => {
    await main.handlers['bmg:start-mission']({}, { noFly: true, paramsJson: JSON.stringify(completeDocument()) });
    const result = (await main.handlers['bmg:magneto-calibration']({}, { start: true })) as {
      success: boolean;
      error?: string;
    };
    expect(result.success).toBe(false);
    expect(result.error).toMatch(/missão/i);
    const mission = main.spawned.find((child) => child.args.some((arg) => arg.endsWith('mission.py')));
    mission?.child.emit('close', 0, null);
  });
});
