import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { cues } from '../audio/cues';
import { loadMain } from './__fixtures__/mainHarness';
import { alertSentence } from './copilotPhrases';

describe('single-voice architecture', () => {
  let main: ReturnType<typeof loadMain>;

  beforeAll(() => {
    main = loadMain();
  });
  afterAll(() => {
    main.restore();
  });

  it('silences the mission process and only it', async () => {
    const started = (await main.handlers['bmg:start-mission']({}, {
      countdown: 0,
      noFly: true,
      paramsJson: '{}',
    })) as { success: boolean };
    expect(started.success).toBe(true);

    const mission = main.spawned.find((child) => child.args.some((arg) => arg.endsWith('mission.py')));
    const daemon = main.spawned.find((child) => child.args.some((arg) => arg.includes('--serve')));
    expect(mission?.env.BMG_GCS_SESSION).toBe('1');
    // 4.3: every station process loads the large-sample Fast DDS profile.
    expect(mission?.env.FASTRTPS_DEFAULT_PROFILES_FILE).toMatch(/config\/fastdds_video\.xml$/);
    expect(daemon?.env.FASTRTPS_DEFAULT_PROFILES_FILE).toMatch(/config\/fastdds_video\.xml$/);
    expect(daemon, 'the copilot daemon is started alongside the mission').toBeDefined();
    expect(daemon?.env.BMG_GCS_SESSION).toBeUndefined();
  });

  it('plays no synthetic cues', () => {
    expect(cues.isEnabled()).toBe(false);
  });
});

describe('alertSentence', () => {
  it('reads the sentence the mission composed for the fault', () => {
    expect(alertSentence({ text: ' Alerta de voo: odometria perdida. Executando pouso seguro imediatamente. ' })).toBe(
      'Alerta de voo: odometria perdida. Executando pouso seguro imediatamente.'
    );
  });

  it('falls back to a generic call without one', () => {
    for (const payload of [{}, { text: '' }, { text: 12 }, null, undefined]) {
      expect(alertSentence(payload as never)).toBe('Alerta de voo recebido. Verifique a aeronave.');
    }
  });
});
