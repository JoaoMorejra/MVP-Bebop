import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { loadMain } from './__fixtures__/mainHarness';

/**
 * The station's real parameter store must be out of reach of every test that
 * loads main.cjs. Measured: a test run wrote its fixture document (confidence
 * 12, tilts +4/+5) into mvp_mission_bebop/mission_config.json, and every later
 * launch failed to load the detector.
 */
const REAL_STORE = resolve(__dirname, '../../../mvp_mission_bebop/mission_config.json');
const digest = () => (existsSync(REAL_STORE) ? createHash('sha1').update(readFileSync(REAL_STORE)).digest('hex') : null);

let main: ReturnType<typeof loadMain>;
let before: string | null;
beforeAll(() => {
  before = digest();
  main = loadMain();
});
afterAll(() => main.restore());

describe('mainHarness', () => {
  it('points main.cjs at a scratch parameter store by default', async () => {
    expect(main.configPath).not.toBe(REAL_STORE);
    const result = (await main.handlers['bmg:save-parameters']({}, { kinematics: { countdown_sec: 3 } })) as {
      success: boolean;
    };
    expect(result.success).toBe(true);
    expect(JSON.parse(readFileSync(main.configPath, 'utf-8'))).toEqual({ kinematics: { countdown_sec: 3 } });
    expect(digest()).toBe(before);
  });
});
